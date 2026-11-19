"""ประกอบ prompt ให้ Claude แตก commit เป็น task + แกะคำตอบกลับมาเป็นข้อมูล

ไม่มี Qt และไม่รัน process เอง — ฝั่ง UI (gui/worklog_tab.py) เป็นคนคุม QProcess เพราะต้อง
ยกเลิกกลางคันได้ ไฟล์นี้จึงเหลือแค่ส่วนที่เป็นข้อความล้วน ทดสอบได้ด้วยการ pipe ผ่าน command line

**Claude ไม่มี tool ให้ใช้เลย** — บังคับด้วย --tools "" ใน build_command ไม่ใช่แค่หวังว่าจะไม่ใช้
แอปอ่าน git มาให้ครบแล้วยัดลง prompt ตรงๆ จบใน turn เดียว ไม่มีจังหวะที่ headless mode ค้างรอ
permission ผลลัพธ์เดาทางได้ และไม่ต้องเสียโทเคนกับนิยาม tool ที่ไม่ได้ใช้

กติกาการแตก task ยกมาจาก slash command /timesheet เดิมทั้งชุด เพื่อให้สำนวนที่ได้เหมือนกับ
ไฟล์ที่ผู้ใช้ยอมรับมาแล้ว
"""
from __future__ import annotations

import json
import re
import shutil

# ตัดข้อความ prompt ไม่ให้บวมจน CLI ปฏิเสธ — 400k อักษรยังอยู่ในวิสัยที่ context รับไหว
MAX_PROMPT_CHARS = 400_000
FILES_SHOWN_PER_COMMIT = 12


class ClaudeTaskError(Exception):
    pass


def find_claude(explicit_path: str = "") -> str:
    """path ของ claude CLI — ระบุเองได้ ไม่งั้นหาจาก PATH

    shutil.which จับ .cmd/.exe ให้เองผ่าน PATHEXT บน Windows (claude ที่ลงผ่าน npm เป็น .cmd)
    """
    if explicit_path.strip():
        return explicit_path.strip()
    return shutil.which("claude") or ""


# โมเดลที่ให้เลือกในแท็บ — (model id, ป้ายที่โชว์) ตัวว่างคือปล่อยให้ CLI เลือกเอง
# คำบรรยายมาจากการวัดจริงด้วย prompt ตัวอย่าง (1 วัน 3 commit) ไม่ใช่การเดาจากขนาดโมเดล:
# Sonnet 5 ~20 วิ · Opus 5 ~23 วิ · Haiku 4.5 ~46 วิ และแตกได้แค่ 3 task ก้อนละ 3 ชม.
# (Haiku ใช้ output token มากกว่าตัวอื่นหลายเท่าเพราะไล่คิดยาว จึงทั้งช้ากว่าและหยาบกว่า)
DEFAULT_MODEL = "claude-sonnet-5"

# ค่าพิเศษที่หมายถึง "ไม่ต้องใส่ธง --model ปล่อยให้ CLI เลือกเอง" — ต้องเป็นคำนี้ ไม่ใช่สตริงว่าง
# เพราะสตริงว่าง = "ยังไม่ได้ตั้ง" ซึ่งต้องตกไปเป็น DEFAULT_MODEL (config.yaml ที่เขียนไว้ก่อนหน้า
# มี claude_model: '' ค้างอยู่ ถ้าตีความว่า "ให้ CLI เลือก" ค่าตั้งต้นใหม่จะไม่มีผลกับเครื่องเดิมเลย)
CLI_DEFAULT_MODEL = "cli"

MODEL_CHOICES = [
    ("claude-sonnet-5", "Sonnet 5 — เร็วและคุ้มที่สุด (ค่าเริ่มต้น)"),
    ("claude-opus-5", "Opus 5 — รายละเอียดในหมายเหตุดีที่สุด"),
    ("claude-haiku-4-5", "Haiku 4.5 — วัดแล้วช้ากว่าและแตก task หยาบกว่า"),
    (CLI_DEFAULT_MODEL, "ใช้ค่าเริ่มต้นของ claude CLI"),
]


def resolve_model(saved: str) -> str:
    """ค่าที่จะส่งเข้า build_command — ว่าง/ไม่ได้ตั้ง = DEFAULT_MODEL, "cli" = ไม่ใส่ธง --model"""
    value = (saved or "").strip()
    if not value:
        return DEFAULT_MODEL
    return "" if value == CLI_DEFAULT_MODEL else value

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_EFFORT = "low"

# system prompt ตัวจิ๋วที่ใช้แทนของ Claude Code ทั้งก้อน — กติกาทั้งหมดอยู่ใน prompt ฝั่งผู้ใช้
# อยู่แล้ว ไม่ต้องมีคำอธิบาย tool/สภาพแวดล้อม/git status ที่ CLI ใส่มาให้ตามปกติ
FAST_SYSTEM_PROMPT = (
    "You convert work logs into timesheet task lists. "
    "Follow the rules in the user message exactly and reply with one JSON object, nothing else."
)


def build_command(claude_path: str, model: str = "", effort: str = DEFAULT_EFFORT,
                  fast: bool = True) -> list[str]:
    """argv สำหรับรัน claude แบบ headless — prompt ส่งทาง stdin ไม่ใช่ argv

    --output-format stream-json ให้ event ทีละบรรทัดระหว่างทาง (JSONL) แทนที่จะเงียบไป
    ทั้งนาทีแล้วโผล่มาก้อนเดียวตอนจบแบบ --output-format json — เอาไว้บอกความคืบหน้าให้ผู้ใช้
    บรรทัดสุดท้าย (type=result) มี payload ชุดเดียวกับแบบ json เป๊ะ ของเดิมจึงไม่เสียไป
    --verbose จำเป็น ถ้าไม่ใส่ CLI จะไม่ยอมใช้ stream-json คู่กับ -p

    fast=True เพิ่มชุดธงที่ตัดของที่งานนี้ไม่ได้ใช้ออกทั้งหมด วัดกับ prompt ตัวอย่างได้
    44.7 วิ / $0.1375 -> 16.0 วิ / $0.0341 (input token ลด ~95%, output ลด ~63%):

      --safe-mode              ไม่โหลด CLAUDE.md / skill / plugin / hook / MCP ของเครื่องผู้ใช้
                               (auth กับการเลือกโมเดลยังทำงานปกติ ต่างจาก --bare ที่บังคับใช้
                               ANTHROPIC_API_KEY อย่างเดียวและจะพังกับคนที่ login แบบ OAuth)
      --tools ""               ตัดนิยาม tool ทิ้งทั้งชุด — เดิมโค้ดนี้แค่ "ตั้งใจ" ไม่ให้ใช้ tool
                               แต่ไม่มีอะไรบังคับ นิยาม tool จึงถูกส่งไปกินโทเคนทุกครั้ง
      --system-prompt          แทน system prompt ของ Claude Code ทั้งก้อนด้วยบรรทัดเดียว
      --strict-mcp-config      ไม่แตะ MCP server ที่ตั้งไว้ในเครื่อง (ไม่ต้องรอ start ด้วย)
      --no-session-persistence งานยิงทิ้ง ไม่ต้องเขียน session ลงดิสก์ให้รกและช้า
      --effort                 ตัวคุมความลึกในการคิด = ตัวแปรใหญ่สุดของเวลาที่ใช้

    ถ้า claude บนเครื่องเป็นเวอร์ชันเก่าที่ไม่รู้จักธงเหล่านี้ ให้ตั้ง worklog.claude_fast: false
    ใน config.yaml เพื่อกลับไปใช้คำสั่งชุดเดิม
    """
    cmd = [claude_path, "-p", "--output-format", "stream-json", "--verbose"]
    if model.strip():
        cmd += ["--model", model.strip()]
    if fast:
        cmd += [
            "--safe-mode",
            "--tools", "",
            "--system-prompt", FAST_SYSTEM_PROMPT,
            "--strict-mcp-config",
            "--no-session-persistence",
        ]
        level = (effort or "").strip().lower()
        if level in EFFORT_LEVELS:
            cmd += ["--effort", level]
    return cmd


def parse_stream_line(line: str) -> dict | None:
    """แกะ 1 บรรทัดของ stream-json เป็นความคืบหน้าที่เอาไปโชว์ได้ — บรรทัดที่ไม่สนใจคืน None

    คืน {"kind": ..., ...} โดย kind เป็นหนึ่งใน:
      started  — ต่อกับ CLI ได้แล้ว (มี model)
      thinking — กำลังคิด (มี tokens ที่เพิ่มขึ้นเรื่อยๆ เป็นตัวเดียวที่ขยับถี่จริง)
      writing  — เริ่มเขียนคำตอบแล้ว
      done     — จบแล้ว (มี is_error)

    ห้าม raise เด็ดขาด — ทางเรียกอยู่ใน readyReadStandardOutput ซึ่งพังแล้วจะกลืนทั้ง process
    """
    line = (line or "").strip()
    if not line.startswith("{"):
        return None  # stream มีบรรทัดว่าง/ขยะปนได้
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None

    kind = event.get("type")
    subtype = event.get("subtype")

    if kind == "system" and subtype == "init":
        return {"kind": "started", "model": str(event.get("model") or "")}
    if kind == "system" and subtype == "thinking_tokens":
        # estimated_tokens นับใหม่ทุก thinking block (วัดจริง: 50..534 แล้วเด้งกลับไป 50)
        # ถ้าเอาไปโชว์ตรงๆ ตัวเลขจะวิ่งถอยหลังต่อหน้าผู้ใช้ — ส่ง delta ให้ผู้เรียกบวกสะสมเอง
        try:
            delta = int(event.get("estimated_tokens_delta") or 0)
        except (TypeError, ValueError):
            delta = 0
        return {"kind": "thinking", "delta": delta}
    if kind == "assistant":
        content = ((event.get("message") or {}).get("content")) or []
        types = {c.get("type") for c in content if isinstance(c, dict)}
        # thinking block จบแล้วยังไม่ใช่คำตอบ — รอ block ที่เป็น text ถึงจะเรียกว่าเริ่มเขียน
        return {"kind": "writing"} if "text" in types else None
    if kind == "result":
        return {"kind": "done", "is_error": bool(event.get("is_error"))}
    return None


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

# กติกาแตก task ข้อ 1-6 ใช้ร่วมกันทุกต้นทาง (git / ไฟล์สรุป) — คำเรียกต้นทางในข้อ 2 กับ 6 จึงเขียน
# เป็นกลาง ("ทุกวันที่มีงาน" / "ข้อมูลที่ให้มา") แทนคำว่า commit ที่เคยฝังอยู่ ไม่งั้นต้องมีกติกา
# สองชุดที่ต้องไล่แก้คู่กันทุกครั้งที่ปรับสำนวน
_TASK_RULES = """\
กติกา:
1. ขนาด task 1-2 ชั่วโมงต่อ task (ปกติได้ราว 5-8 task ต่อวัน) เลี่ยง task 0.5 ชม. และเลี่ยง
   task ก้อนใหญ่ 4 ชม.
2. ชั่วโมงรวมของแต่ละวันต้องเท่ากับเป้าหมายที่ระบุ "พอดี" ทุกวันที่มีงาน
3. subject = ชื่องาน ภาษาไทย เป็นวลีบอกสิ่งที่ทำ ไม่ต้องมีชื่อโปรเจกต์นำหน้า ไม่ต้องมีเลข task
   ตัวอย่าง: "ปรับราคาและจำนวนเงินในรายงานวัสดุคงเหลือเป็นทศนิยม 4 ตำแหน่ง"
4. comment = หมายเหตุ ขึ้นต้นด้วย "<ชื่อโปรเจกต์>: " แล้วตามด้วยรายละเอียดทางเทคนิคจริง
   (ชื่อไฟล์ ชื่อคลาส/เมธอด ค่าที่เปลี่ยน commit hash) ตัวอย่าง:
   "fpo_inventory2: ReportController.cs เปลี่ยนฟอร์แมต N2 -> N4 (commit 050d750)"
5. งานประเภท pull/rebuild/ทดสอบ/commit-push รวมเป็น task เดียวได้ ไม่ต้องแยกทุกคำสั่ง
6. ห้ามแต่งรายละเอียดที่ไม่มีในข้อมูลที่ให้มา ถ้าเนื้องานน้อยกว่าชั่วโมงเป้าหมายอย่างชัดเจน
   ให้เกลี่ยชั่วโมงตามความหนักของงานที่มีจริง อย่าเพิ่ม task ปลอมเพื่อให้ครบชั่วโมง"""

# ต่อสตริงธรรมดา ไม่ใช่ f-string — schema JSON ข้างล่างมีวงเล็บปีกกา ถ้าใช้ f-string ต้องหนีเป็น
# {{ }} ทุกตัวจนอ่านไม่ออกว่า prompt จริงหน้าตาเป็นแบบไหน
_GIT_RULES = """\
คุณคือผู้ช่วยลงชั่วโมงทำงาน (timesheet) หน้าที่คือแปลง commit จริงจาก git ให้เป็นรายการ task
พร้อมชั่วโมง สำหรับบันทึกเข้า OpenProject

""" + _TASK_RULES + """
7. repo ของแต่ละ task ต้องเป็นชื่อ repo ที่ปรากฏใน commit เท่านั้น (ใช้จับคู่โปรเจกต์)

นอกจาก task ให้เขียนสรุปงานเป็น markdown (summary_md) ด้วย ภาษาไทย โครงสร้าง:
หัวเรื่องพร้อมวันที่ -> บรรทัดโปรเจกต์/repo -> "## ภาพรวม" -> "## ไทม์ไลน์" (ตาราง เวลา | งาน
จากเวลา commit จริง) -> "## รายละเอียดสิ่งที่แก้" (แยกตามไฟล์/commit)

ตอบกลับเป็น JSON ก้อนเดียวเท่านั้น ห้ามมีข้อความอื่นนอก JSON:
{
  "summary_md": "<สรุปงาน markdown>",
  "tasks": [
    {"date": "YYYY-MM-DD", "hours": 2, "subject": "...", "comment": "...", "repo": "<ชื่อ repo>"}
  ]
}
"""

# ต้นทางเป็นสรุปที่เขียนไว้แล้ว: ไม่ขอ summary_md กลับมา (มีอยู่แล้ว ให้เขียนซ้ำเสียเวลาและ token
# เปล่า) และไม่มีฟิลด์ repo เพราะไม่มี repo ให้จับคู่ — ฝั่ง UI เติมโปรเจกต์ให้ทุกแถวเองทีเดียว
_SUMMARY_RULES = """\
คุณคือผู้ช่วยลงชั่วโมงทำงาน (timesheet) หน้าที่คือแปลงสรุปงานที่เขียนไว้แล้ว ให้เป็นรายการ task
พร้อมชั่วโมง สำหรับบันทึกเข้า OpenProject

""" + _TASK_RULES + """

ตอบกลับเป็น JSON ก้อนเดียวเท่านั้น ห้ามมีข้อความอื่นนอก JSON และไม่ต้องเขียนสรุปกลับมา:
{
  "tasks": [
    {"date": "YYYY-MM-DD", "hours": 2, "subject": "...", "comment": "..."}
  ]
}
"""


def _format_commit(commit: dict) -> str:
    lines = [
        f"- {commit['authored_at'][11:16]} [{commit['repo']}] {commit['hash']} {commit['subject']}"
    ]
    body = (commit.get("body") or "").strip()
    if body:
        indented = "\n".join(f"    {ln}" for ln in body.splitlines() if ln.strip())
        lines.append(indented)
    files = commit.get("files") or []
    for f in files[:FILES_SHOWN_PER_COMMIT]:
        added, deleted = f.get("added"), f.get("deleted")
        stat = "binary" if added is None else f"+{added}/-{deleted}"
        lines.append(f"    {f['path']} ({stat})")
    hidden = len(files) - FILES_SHOWN_PER_COMMIT + (commit.get("files_omitted") or 0)
    if hidden > 0:
        lines.append(f"    ... และอีก {hidden} ไฟล์")
    return "\n".join(lines)


def _finalize_prompt(parts: list[str], what: str, hint: str) -> str:
    """ต่อ prompt แล้วตรวจความยาว — ใช้ร่วมกันทุกต้นทาง ต่างกันแค่ข้อความบอกทางแก้"""
    prompt = "\n".join(parts)
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ClaudeTaskError(f"{what}ยาวเกินไป ({len(prompt):,} อักษร) — {hint}")
    return prompt


def build_prompt(commits: dict, hours_per_day: float, projects_by_repo: dict | None = None) -> str:
    """prompt เต็มที่จะส่งเข้า stdin ของ claude — commits มาจาก gitlog.collect_commits()"""
    days = commits.get("days") or {}
    if not days:
        raise ClaudeTaskError("ไม่มี commit ในช่วงวันที่ที่เลือก")

    parts = [_GIT_RULES, "", f"เป้าหมายชั่วโมงต่อวัน: {hours_per_day:g} ชั่วโมง", ""]

    projects_by_repo = projects_by_repo or {}
    named = {repo: name for repo, name in projects_by_repo.items() if name}
    if named:
        parts.append("ชื่อโปรเจกต์ของแต่ละ repo (ใช้ขึ้นต้น comment):")
        parts += [f"  {repo} = {name}" for repo, name in named.items()]
        parts.append("")

    parts.append(f"commit ทั้งหมด ({commits.get('total', 0)} commit, {len(days)} วัน):")
    for day, day_commits in days.items():
        parts.append("")
        parts.append(f"## {day}")
        parts += [_format_commit(c) for c in day_commits]

    return _finalize_prompt(parts, "ข้อมูล commit ", "ลดช่วงวันที่หรือจำนวน repo ลง")


def build_summary_prompt(summary_text: str, hours_per_day: float, project_name: str = "") -> str:
    """prompt สำหรับต้นทางที่เป็นสรุปงานเขียนไว้แล้ว — ไฟล์ .md หรือข้อความที่ผู้ใช้วางมาในแท็บ

    ไม่ขอ summary_md กลับมาและไม่มีฟิลด์ repo (ดู _SUMMARY_RULES) โปรเจกต์ส่งเข้ามาเป็นชื่อเดียว
    ทั้งชุดเพราะไม่มี repo ให้จับคู่ ใช้แค่ให้ Claude ขึ้นต้น comment ตามกติกาข้อ 4 ส่วนค่าที่จะลง
    ไฟล์จริงฝั่ง UI เติมเองหลังแกะคำตอบ ไม่ได้อ่านจากที่ Claude ตอบ
    """
    text = (summary_text or "").strip()
    if not text:
        raise ClaudeTaskError("ยังไม่มีเนื้อสรุปงาน — เลือกไฟล์ .md หรือวางข้อความก่อน")

    parts = [_SUMMARY_RULES, "", f"เป้าหมายชั่วโมงต่อวัน: {hours_per_day:g} ชั่วโมง", ""]
    if project_name.strip():
        parts.append(f"ชื่อโปรเจกต์ (ใช้ขึ้นต้น comment): {project_name.strip()}")
        parts.append("")
    parts.append("สรุปงานที่ต้องแตกเป็น task:")
    parts.append(text)
    return _finalize_prompt(parts, "สรุปงาน", "ตัดส่วนที่ไม่เกี่ยวออก หรือแบ่งเป็นหลายไฟล์")


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


def _result_envelope(stdout: str) -> dict | None:
    """หาซองคำตอบสุดท้ายจาก stdout ซึ่งเป็นได้ 2 แบบ

    stream-json (ที่ build_command ใช้อยู่) = JSONL หลายบรรทัด เอาบรรทัด type=result
    json (แบบเดิม) = JSON ก้อนเดียวทั้งก้อน — ยังรองรับไว้เพราะทดสอบไฟล์นี้ด้วยการ pipe
    ผ่าน command line ได้ทั้งสองแบบ และเผื่อถอยกลับไปใช้ --output-format json
    """
    try:
        whole = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        pass
    else:
        return whole if isinstance(whole, dict) else None

    envelope = None
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            envelope = event  # เอาอันสุดท้ายเสมอ
    return envelope


def _unwrap_cli_envelope(stdout: str) -> str:
    """ดึงเนื้อคำตอบออกจากซองของ CLI {"type":"result","result":"...","is_error":...}

    ถ้าแกะซองไม่ได้ให้คืน stdout เดิม — เผื่อวันหน้า CLI เปลี่ยนรูปแบบ หรือผู้ใช้เรียกแบบ text
    """
    envelope = _result_envelope(stdout)
    if envelope is None:
        return stdout
    if envelope.get("is_error"):
        raise ClaudeTaskError(str(envelope.get("result") or "claude ตอบกลับว่าเกิดข้อผิดพลาด"))
    result = envelope.get("result")
    return result if isinstance(result, str) else stdout


def result_text_for_display(stdout: str) -> str:
    """เนื้อคำตอบล้วนๆ ไว้โชว์ตอนแตก task ไม่สำเร็จ

    stream-json ยาวเป็นหมื่นตัวอักษรและเป็น event ของ CLI ล้วน ถ้าเอาไปโชว์ดิบๆ จะหาไม่เจอว่า
    Claude ตอบอะไรมากันแน่ — แกะเอาแต่เนื้อ ถ้าแกะไม่ได้ค่อยคืนของดิบ (ห้าม raise ทางนี้
    ถูกเรียกตอนมีอะไรพังอยู่แล้ว)
    """
    envelope = _result_envelope(stdout)
    if envelope is None:
        return stdout
    result = envelope.get("result")
    return result if isinstance(result, str) else stdout


def _extract_json_object(text: str) -> dict:
    candidates = []
    fenced = _FENCE.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(text.strip())
    # เผื่อมีข้อความนำหน้า/ต่อท้าย JSON — คว้าตั้งแต่ { แรกถึง } สุดท้าย
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ClaudeTaskError("แกะคำตอบของ Claude เป็น JSON ไม่ได้")


def parse_response(stdout: str) -> tuple[str, list[dict]]:
    """คืน (summary_md, tasks) — tasks ผ่านการตรวจรูปแบบแล้ว แต่ยังไม่ได้เติม project"""
    if not (stdout or "").strip():
        raise ClaudeTaskError("claude ไม่ได้ตอบอะไรกลับมาเลย")

    payload = _extract_json_object(_unwrap_cli_envelope(stdout))
    raw_tasks = payload.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ClaudeTaskError("คำตอบของ Claude ไม่มีรายการ task")

    tasks = []
    for i, item in enumerate(raw_tasks, start=1):
        if not isinstance(item, dict):
            continue
        date = str(item.get("date") or "").strip()
        subject = str(item.get("subject") or "").strip()
        if not date or not subject:
            continue  # แถวที่ขาดข้อมูลหลักทิ้งไป ดีกว่าพาให้ทั้งชุดพัง
        try:
            hours = float(item.get("hours") or 0)
        except (TypeError, ValueError):
            hours = 0.0
        tasks.append({
            "date": date,
            "hours": hours,
            "subject": subject,
            "comment": str(item.get("comment") or "").strip(),
            "repo": str(item.get("repo") or "").strip(),
            "project": "",
        })

    if not tasks:
        raise ClaudeTaskError("คำตอบของ Claude ไม่มี task ที่ใช้ได้เลย (ขาด date หรือ subject)")

    summary_md = str(payload.get("summary_md") or "").strip()
    return summary_md, tasks


def apply_project_mapping(tasks: list[dict], projects_by_repo: dict) -> None:
    """เติมคอลัมน์ project ให้แต่ละ task จาก repo ที่ commit มา — แอปเป็นคนแมป ไม่ให้ Claude เดา

    ค่าที่เติมคือสิ่งที่จะไปลงคอลัมน์ new_project ในไฟล์ Excel และถูก
    gui/loader._resolve_new_projects() แปลงเป็น project id ตอนเปิดไฟล์
    """
    # repo เดียวในชุด = ทุก task เป็นของโปรเจกต์นั้น แม้ Claude จะไม่ได้ใส่ชื่อ repo กลับมา
    only = next(iter(projects_by_repo.values())) if len(projects_by_repo) == 1 else ""
    for task in tasks:
        task["project"] = projects_by_repo.get(task.get("repo") or "", "") or only

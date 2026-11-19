"""อ่าน commit จาก git repo หลายตัวมารวมเป็นข้อมูลก้อนเดียว จัดกลุ่มตามวัน

ไม่มี Qt ไม่มี LLM ไม่ยิงเน็ต — deterministic ล้วน รันซ้ำได้ผลเดิม จึงทดสอบจาก command line
ได้โดยไม่ต้องเปิดแอป (ดู __main__ ท้ายไฟล์) ผลลัพธ์ถูกส่งต่อให้ claude_tasks.build_prompt()
เป็นวัตถุดิบให้ Claude แตกเป็น task

ใช้ subprocess ตรงๆ ไม่พึ่ง library git เพราะต้องแพ็กลง .exe ด้วย PyInstaller และ git
มีอยู่บนเครื่องอยู่แล้ว
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta

# คั่นฟิลด์ใน --pretty ด้วยอักขระที่ไม่มีทางโผล่ใน commit message จริง
_FIELD_SEP = "\x1f"
_COMMIT_SEP = "\x1e"
_PRETTY = _COMMIT_SEP + _FIELD_SEP.join(["%H", "%h", "%aI", "%an", "%ae", "%s", "%b"])

# commit message ยาวเกินนี้ถูกตัด — body ยาวๆ (เช่น squash merge ที่รวม log มาทั้งก้อน)
# ทำให้ prompt บวมโดยไม่ได้ข้อมูลเพิ่ม
MAX_BODY_CHARS = 1200
# ไฟล์ต่อ commit ที่ส่งต่อไปใน prompt — commit ที่แตะ 300 ไฟล์มักเป็น rename/format ทั้ง repo
MAX_FILES_PER_COMMIT = 40

GIT_TIMEOUT = 60

# git.exe เป็นโปรแกรม console ตัวแอปถูกแพ็กด้วย PyInstaller --windowed จึงไม่มี console เป็นของตัวเอง
# Windows เลยสร้างหน้าต่างดำใหม่ให้ทุกครั้งที่เรียก — กด "อ่าน git" ทีหนึ่งแวบเท่าจำนวนครั้งที่เรียก
# (repo ละ 2 ครั้ง ถ้าไม่ได้ตั้ง worklog.authors) ห้ามลบทิ้งเพราะดูเหมือนไม่จำเป็น: ตอนรันด้วย
# python app.py จะไม่เห็นอาการ เพราะ process แม่มี console อยู่แล้ว git ไปเกาะอันเดิม
# getattr กันไว้ให้ไฟล์นี้ยัง import ได้บน OS ที่ไม่มีค่านี้
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class GitLogError(Exception):
    pass


def _run_git(repo_path: str, args: list[str]) -> str:
    """เรียก git ในโฟลเดอร์ repo แล้วคืน stdout — ล้มเหลวโยน GitLogError พร้อม stderr จริง"""
    try:
        proc = subprocess.run(
            ["git", "-C", repo_path, *args],
            capture_output=True,
            timeout=GIT_TIMEOUT,
            # commit message ภาษาไทย/emoji ต้องอ่านได้บน Windows ที่ default เป็น cp874
            encoding="utf-8",
            errors="replace",
            # ไม่ให้ git เด้ง credential prompt หรือ pager มาค้าง (เราอ่าน local อย่างเดียว)
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat"},
            creationflags=_NO_WINDOW,  # ดูคอมเมนต์ที่ _NO_WINDOW
        )
    except FileNotFoundError:
        raise GitLogError("ไม่พบคำสั่ง git บนเครื่อง — ติดตั้ง Git แล้วเปิดโปรแกรมใหม่")
    except subprocess.TimeoutExpired:
        raise GitLogError(f"git ใช้เวลานานเกิน {GIT_TIMEOUT} วินาทีที่ {repo_path}")

    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        raise GitLogError(f"git ล้มเหลวที่ {repo_path}: {detail[0] if detail else 'ไม่ทราบสาเหตุ'}")
    return proc.stdout


def is_git_repo(path: str) -> bool:
    return os.path.isdir(os.path.join(path, ".git"))


def repo_display_name(path: str) -> str:
    return os.path.basename(os.path.normpath(path)) or path


def default_author(repo_path: str) -> str:
    """อีเมลใน git config ของ repo นั้น — ใช้เมื่อผู้ใช้ไม่ได้ระบุ authors เอง"""
    try:
        return _run_git(repo_path, ["config", "user.email"]).strip()
    except GitLogError:
        return ""


def _parse_numstat(lines: list[str]) -> list[dict]:
    """แปลงบรรทัด --numstat ("<added>\t<deleted>\t<path>") เป็น list of dict

    ไฟล์ binary git ใส่ "-" แทนตัวเลข — เก็บเป็น None ไม่ใช่ 0 เพื่อไม่ให้ดูเหมือนไฟล์ที่ไม่มีอะไรเปลี่ยน
    """
    files = []
    for line in lines:
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added, deleted, path = parts[0], parts[1], parts[2]
        files.append({
            "path": path.strip(),
            "added": None if added == "-" else int(added or 0),
            "deleted": None if deleted == "-" else int(deleted or 0),
        })
    return files


def _parse_log_output(output: str, repo_name: str, project: str) -> list[dict]:
    commits = []
    for block in output.split(_COMMIT_SEP):
        block = block.strip("\n")
        if not block.strip():
            continue
        head, _, rest = block.partition("\n")
        fields = head.split(_FIELD_SEP)
        if len(fields) < 7:
            continue
        full_hash, short_hash, authored_at, author_name, author_email, subject, body = fields[:7]

        # %b เป็นฟิลด์สุดท้ายและมีขึ้นบรรทัดใหม่ในตัวเอง ส่วนที่เหลือของ block จึงเป็น body ต่อ
        # แล้วตามด้วยบรรทัด numstat — แยกกันด้วยบรรทัดว่างที่ git ใส่ก่อน numstat เสมอ
        body_tail, numstat_lines = _split_body_and_numstat(rest)
        body = (body + ("\n" + body_tail if body_tail else "")).strip()

        files = _parse_numstat(numstat_lines)
        truncated_files = max(0, len(files) - MAX_FILES_PER_COMMIT)

        commits.append({
            "hash": short_hash,
            "full_hash": full_hash,
            "authored_at": authored_at,
            "date": authored_at[:10],
            "author": f"{author_name} <{author_email}>",
            "repo": repo_name,
            "project": project,
            "subject": subject.strip(),
            "body": body[:MAX_BODY_CHARS],
            "files": files[:MAX_FILES_PER_COMMIT],
            "files_omitted": truncated_files,
        })
    return commits


def _split_body_and_numstat(rest: str) -> tuple[str, list[str]]:
    """ส่วนท้ายของแต่ละ block คือ body ที่เหลือ + บรรทัด numstat

    numstat มีรูปแบบตายตัว "<เลขหรือ->\t<เลขหรือ->\t<path>" — ไล่จากท้ายขึ้นมาจนเจอบรรทัดที่
    ไม่เข้ารูปแบบ ปลอดภัยกว่าการหาบรรทัดว่างคั่น เพราะ body เองก็มีบรรทัดว่างได้
    """
    lines = rest.split("\n")
    cut = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i]
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 3 and all(p == "-" or p.isdigit() for p in parts[:2]):
            cut = i
        else:
            break
    return "\n".join(lines[:cut]).strip(), [ln for ln in lines[cut:] if ln.strip()]


def collect_commits(
    repos: list[dict], date_from: str, date_to: str, authors: list[str] | None = None
) -> dict:
    """อ่าน commit ของทุก repo ในช่วงวันที่ (รวมวันปลายทาง) แล้วจัดกลุ่มตามวัน

    repos = [{"path": ..., "project": "<ชื่อหรือ id โปรเจกต์ OpenProject>"}]
    authors ว่าง = ใช้ user.email ของแต่ละ repo (ไม่ใช่ "เอาทุกคน" — timesheet เป็นของคนเดียว)

    คืน {"days": {"YYYY-MM-DD": [commit...]}, "repos": [...], "total": N, "errors": [...]}
    repo ที่อ่านไม่ได้ไม่ล้มทั้งชุด — เก็บไว้ใน errors ให้ UI แสดงเตือน
    """
    days: dict[str, list[dict]] = {}
    errors: list[str] = []
    repo_names: list[str] = []
    seen_hashes: set[str] = set()

    for repo in repos:
        path = repo.get("path") or ""
        if not is_git_repo(path):
            errors.append(f"{path or '(ว่าง)'} ไม่ใช่ git repo")
            continue

        name = repo_display_name(path)
        repo_names.append(name)
        who = [a for a in (authors or []) if a.strip()] or [default_author(path)]
        who = [a for a in who if a]
        if not who:
            errors.append(f"{name}: ไม่รู้ว่าจะกรอง commit ของใคร (git config user.email ว่าง)")
            continue

        args = [
            "log", "--all", "--no-merges", "--numstat", f"--pretty=format:{_PRETTY}",
            f"--since={date_from} 00:00:00",
            # ไม่ใส่เวลา = git ตีความเป็น 00:00 ของวันนั้น แล้ว commit ทั้งวันสุดท้ายหายหมด
            f"--until={date_to} 23:59:59",
        ]
        args += [f"--author={a}" for a in who]

        try:
            output = _run_git(path, args)
        except GitLogError as e:
            errors.append(str(e))
            continue

        for commit in _parse_log_output(output, name, repo.get("project") or ""):
            # commit เดียวโผล่ได้หลาย branch เพราะ --all — นับครั้งเดียว
            if commit["full_hash"] in seen_hashes:
                continue
            seen_hashes.add(commit["full_hash"])
            days.setdefault(commit["date"], []).append(commit)

    for commits in days.values():
        commits.sort(key=lambda c: c["authored_at"])

    return {
        "days": dict(sorted(days.items())),
        "repos": repo_names,
        "total": len(seen_hashes),
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# ช่วงวันที่แบบกดปุ่มเดียว (ใช้ร่วมกันระหว่าง UI กับ CLI)
# ---------------------------------------------------------------------------

def date_range(preset: str, today: date | None = None) -> tuple[str, str]:
    """คืน (from, to) เป็น YYYY-MM-DD ตาม preset — วันนี้/เมื่อวาน/สัปดาห์นี้/เดือนนี้"""
    today = today or date.today()
    if preset == "yesterday":
        d = today - timedelta(days=1)
        return d.isoformat(), d.isoformat()
    if preset == "week":
        start = today - timedelta(days=today.weekday())  # จันทร์ของสัปดาห์นี้
        return start.isoformat(), today.isoformat()
    if preset == "month":
        return today.replace(day=1).isoformat(), today.isoformat()
    return today.isoformat(), today.isoformat()


def validate_range(date_from: str, date_to: str) -> None:
    try:
        start = datetime.strptime(date_from, "%Y-%m-%d").date()
        end = datetime.strptime(date_to, "%Y-%m-%d").date()
    except ValueError:
        raise GitLogError("รูปแบบวันที่ต้องเป็น YYYY-MM-DD")
    if start > end:
        raise GitLogError("วันเริ่มต้นต้องไม่เกินวันสิ้นสุด")


def main() -> int:
    # console บน Windows เป็น cp874 ทำให้ print ภาษาไทย/บุลเล็ตพังตั้งแต่บรรทัดแรก
    # (ฝั่งแอปไม่เจอปัญหานี้เพราะวาดผ่าน Qt ไม่ผ่าน stdout)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="อ่าน commit จาก git repo หลายตัว (ตรวจสอบ gitlog.py เดี่ยวๆ)")
    ap.add_argument("--from", dest="date_from", required=True)
    ap.add_argument("--to", dest="date_to", required=True)
    ap.add_argument("--repo", action="append", default=[], help="path ไป repo (ใส่ซ้ำได้)")
    ap.add_argument("--author", action="append", default=[], help="กรอง author (ไม่ใส่ = user.email ของ repo)")
    ap.add_argument("--json", action="store_true", help="พิมพ์ JSON ดิบแทนสรุปอ่านง่าย")
    args = ap.parse_args()

    validate_range(args.date_from, args.date_to)
    result = collect_commits(
        [{"path": p, "project": ""} for p in args.repo], args.date_from, args.date_to, args.author
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    for err in result["errors"]:
        print(f"[WARN] {err}")
    print(f"{result['total']} commit · {len(result['repos'])} repo · {len(result['days'])} วัน\n")
    for day, commits in result["days"].items():
        print(f"{day} ({len(commits)} commit)")
        for c in commits:
            print(f"  {c['authored_at'][11:16]} {c['hash']} [{c['repo']}] {c['subject']}")
            for f in c["files"][:5]:
                print(f"        {f['path']} (+{f['added']}/-{f['deleted']})")
            if len(c["files"]) > 5:
                print(f"        ... และอีก {len(c['files']) - 5} ไฟล์")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Excel -> server-row loading, ported from the old /api/state Flask route.

Runs entirely inside a background Worker (gui.threads.run_async) — read_timesheet plus one
get_work_package call per row with a wp_id is blocking I/O and must never run on the UI thread.

Rows that ask for a brand-new work package (wp_mode = new) carry a project as free text in the
Excel file; resolving that text to a real project id needs the project list, so it happens here
rather than in row_model, and only when at least one row actually needs it.
"""
from __future__ import annotations

from common import OpenProjectAPIError, get_unsynced_rows, read_timesheet, row_to_dict


def load_rows_from_excel(client, file_path: str, excel_cfg: dict) -> list[dict]:
    df = read_timesheet(file_path, excel_cfg)
    unsynced = get_unsynced_rows(df)

    rows = [row_to_dict(row) for _, row in unsynced.iterrows()]

    wp_ids = {r["wp_id"] for r in rows if r["wp_id"] is not None}
    try:
        matches = client.get_work_packages_by_ids(list(wp_ids))
    except OpenProjectAPIError:
        matches = {}
    for r in rows:
        r["match"] = matches.get(r["wp_id"]) if r["wp_id"] is not None else None

    _resolve_new_projects(client, rows)
    return rows


def _resolve_new_projects(client, rows: list[dict]) -> None:
    """เติม new_project_id / new_project_name จากข้อความในคอลัมน์ new_project (ชื่อหรือ id ก็ได้)"""
    for r in rows:
        r["new_project_id"] = None
        r["new_project_name"] = None

    wanted = [r for r in rows if r.get("new_project")]
    if not wanted:
        return

    try:
        projects = client.get_projects()
    except OpenProjectAPIError:
        return  # หาไม่ได้ก็ปล่อยว่าง ผู้ใช้เลือกโปรเจกต์เองในแอปได้ตามปกติ

    by_id = {str(p["id"]): p for p in projects}
    by_name = {(p["name"] or "").strip().lower(): p for p in projects}

    for r in wanted:
        text = r["new_project"].strip()
        # ชื่อโปรเจกต์ต้องตรงทั้งชื่อ ไม่ใช้การจับคู่บางส่วน เพราะชื่อโปรเจกต์มักต่างกันแค่ท้ายชื่อ
        # (เช่น "FPO_Back Office - MA2026" กับ "FPO_Back Office - CR Inventory 2025")
        project = by_id.get(text) or by_name.get(text.lower())
        if project:
            r["new_project_id"] = project["id"]
            r["new_project_name"] = project["name"]

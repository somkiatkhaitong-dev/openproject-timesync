"""Row submission — ported 1:1 from the old /api/submit Flask route.

Runs entirely inside a background Worker (blocking network calls per row); one row's
OpenProjectAPIError/ValueError is caught individually so it never aborts the rest of the batch.
"""
from __future__ import annotations

from datetime import datetime

from common import OpenProjectAPIError, resolve_activity_id, resolve_user_id, update_timesheet_rows
from gui.row_model import RowState


def submit_rows(client, config: dict, file_path: str, rows: list[RowState], report_progress=None) -> dict:
    """report_progress(done, total) — run_async ยัดให้เองเมื่อผู้เรียกส่ง on_progress มา
    (ดู gui/threads.py) เรียกจาก thread เบื้องหลัง ห้ามแตะ widget ในนั้น"""
    total = len(rows)

    def tick(done: int) -> None:
        if report_progress:
            report_progress(done, total)

    tick(0)
    updates: dict[int, dict] = {}
    appends: list[dict] = []
    results = []
    success = 0
    failed = 0

    def record(row: RowState, synced: bool, note: str, wp_id: int | None) -> None:
        """แถวจาก Excel = อัปเดตแถวเดิม / แถวที่เพิ่มในแอป = ต่อท้ายไฟล์ทั้งแถว
        (ต้องเขียนทั้งกรณีสำเร็จและล้มเหลว ไม่งั้นแถวที่เพิ่งเพิ่มจะหายตอน reload หลังส่ง)"""
        if row.excel_row is not None:
            updates[row.excel_row] = {"synced": synced, "sync_note": note, "wp_id": wp_id}
            return
        # แถวใหม่ที่ล้มเหลวยังเก็บ WP ที่เลือกไว้ติดไปด้วย จะได้ไม่ต้องเลือกใหม่หลัง reload
        appends.append({
            "date": row.date,
            "hours": row.hours,
            "activity": row.activity,
            "comment": row.comment or "",
            "user_email": row.user_email or "",
            "synced": synced,
            "sync_note": note,
            "wp_id": wp_id if wp_id is not None else row.wp_id,
            "wp_mode": row.mode,
            "new_subject": row.new_subject or "",
            "new_project": row.new_project_name or "",
        })

    for index, row in enumerate(rows):
        excel_row = row.excel_row
        try:
            date = row.date
            hours = row.hours
            activity = row.activity
            comment = row.comment or ""
            user_email = row.user_email or ""

            if not date:
                raise ValueError("วันที่ไม่ถูกต้อง")
            try:
                hours = float(hours)
            except (TypeError, ValueError):
                raise ValueError("ชั่วโมงไม่ถูกต้อง")
            if hours <= 0:
                raise ValueError("ชั่วโมงต้องมากกว่า 0")
            # กิจกรรมถูกเลือกจาก dropdown ในตารางแล้ว (แถวที่ยังไม่เลือกกด submit ไม่ได้ตั้งแต่แรก)
            # เหลือ activity_mapping ไว้เป็น fallback ให้เคสที่ดึงรายการจากเซิร์ฟเวอร์ไม่สำเร็จ
            activity_id = row.activity_id
            if not activity_id:
                if not activity:
                    raise ValueError("ไม่ได้ระบุกิจกรรม (activity)")
                activity_id = resolve_activity_id(activity, config["activity_mapping"])
            user_id = resolve_user_id(user_email, config["user_mapping"])

            if row.mode == "new":
                project_id = row.new_project_id
                subject = (row.new_subject or "").strip()
                type_id = row.new_type_id
                if not project_id or not subject or not type_id:
                    raise ValueError("สร้างงานใหม่ต้องระบุโปรเจกต์ ชื่องาน และประเภทงานให้ครบ")
                wp_id = client.create_work_package(int(project_id), subject, int(type_id))
            else:
                wp_id = row.wp_id
                if not wp_id:
                    raise ValueError("ยังไม่ได้เลือก Work Package")
                wp_id = int(wp_id)

            client.create_time_entry(
                work_package_id=wp_id,
                spent_on=date,
                hours=hours,
                activity_id=activity_id,
                comment=comment,
                user_id=user_id,
            )

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            note = f"บันทึกสำเร็จเมื่อ {timestamp}"
            if row.mode == "new":
                note = f"สร้าง WP #{wp_id} ใหม่ และ{note}"
            record(row, synced=True, note=note, wp_id=wp_id)
            results.append({"excel_row": excel_row, "ok": True, "wp_id": wp_id, "message": note})
            success += 1

        except (OpenProjectAPIError, ValueError) as e:
            record(row, synced=False, note=str(e), wp_id=None)
            results.append({"excel_row": excel_row, "ok": False, "message": str(e)})
            failed += 1

        # นับแถวที่ "ผ่านมือแล้ว" ไม่ใช่แถวที่สำเร็จ — หลอดต้องเดินต่อแม้บางแถวจะล้ม
        tick(index + 1)

    if updates or appends:
        update_timesheet_rows(file_path, config["excel"], updates, appends)

    return {"success": success, "failed": failed, "results": results}

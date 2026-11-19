"""CLI สำหรับ sync ชั่วโมงทำงานเข้า OpenProject แบบอัตโนมัติ (เช่นรันผ่าน cron)

ต่างจาก app.py ตรงที่สคริปต์นี้ไม่มีหน้าเว็บให้ตรวจสอบก่อนส่ง — จะ sync เฉพาะแถวที่มี
wp_id กรอกไว้แล้วและตรงกับ Work Package จริงใน OpenProject เท่านั้น แถวที่ยังไม่มี wp_id
(ต้องเลือก/สร้างงานใหม่) จะถูกข้ามและบันทึก sync_note ไว้ให้ไปจัดการผ่าน app.py แทน

Usage (dev):
    python sync_timesheet.py --file timesheet.xlsx [--config config.yaml]

เมื่อแพ็กเป็น .exe ด้วย PyInstaller แล้วไม่ระบุ --config จะมองหา config.yaml ข้าง .exe ให้อัตโนมัติ
เหมาะสำหรับตั้ง Windows Task Scheduler ให้รันตามรอบเวลา (ไม่มีหน้าต่างให้โต้ตอบ)
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

from common import (
    ConfigError,
    OpenProjectAPIError,
    OpenProjectClient,
    get_unsynced_rows,
    load_config,
    load_config_raw,
    read_timesheet,
    resolve_activity_id,
    resolve_user_id,
    row_to_dict,
    update_timesheet_rows,
)
# gui/__init__.py ว่างเปล่า และ logging_setup ไม่ import Qt เลย — CLI จึงใช้ตัวเดียวกับ GUI ได้
# โดยไม่ลาก PySide6 เข้ามาใน .exe ของ CLI
from gui.logging_setup import log_startup_banner, setup_logging
from version import APP_NAME, APP_VERSION


def app_base_dir() -> str:
    """โฟลเดอร์ที่ควรมองหา config.yaml — ข้าง .exe เวลาแพ็ก, ข้าง sync_timesheet.py เวลารันด้วย python"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _run_diagnose(config_path: str, base_dir: str) -> int:
    """--diagnose ใช้ตรวจเครื่องปลายทางได้แม้ยังไม่มี config.yaml หรือ config ยังกรอกไม่ครบ

    ตั้งใจให้ผู้ใช้ที่เปิด GUI ไม่ขึ้นยังรันตัวนี้จาก PowerShell แล้วส่งผลกลับมาได้
    """
    from diagnostics import run_diagnostics

    url = ""
    api_key = ""
    if os.path.exists(config_path):
        try:
            config = load_config_raw(config_path)
            url = config["openproject"].get("url", "")
            api_key = config["openproject"].get("api_key", "")
        except Exception as e:  # noqa: BLE001 - config พังก็ยังต้องตรวจส่วนอื่นให้ได้
            print(f"[WARN] อ่าน {config_path} ไม่สำเร็จ: {e}", file=sys.stderr)

    report = run_diagnostics(url, api_key, config_path)
    print(report)

    # console ของ Windows หลายเครื่องแสดงภาษาไทยไม่ได้ (ต้อง chcp 65001 ก่อน) รายงานบนจอจึงอาจ
    # อ่านไม่ออก — เขียนไฟล์ UTF-8 ไว้ให้เสมอ แล้วบอก path เป็น ASCII ล้วนซึ่งอ่านออกแน่นอน
    out_dir = os.path.join(base_dir, "logs")
    out_path = os.path.join(out_dir, f"diagnostics-{datetime.now():%Y%m%d-%H%M%S}.txt")
    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(report)
    except OSError as e:
        print(f"[WARN] saving report failed: {e}", file=sys.stderr)
    else:
        print(f"\nReport saved to: {out_path}")
        print("Send that file to your administrator (it contains no API key).")

    return 0


def main() -> int:
    base_dir = app_base_dir()
    # CLI มี console อยู่แล้ว จึงยัง print ออกจอตามเดิม (mirror) แต่เก็บลงไฟล์เพิ่มด้วย
    # เพราะส่วนใหญ่ถูกตั้งให้รันผ่าน Task Scheduler ซึ่งไม่มีใครเห็นหน้าจอตอนรัน
    setup_logging(base_dir)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", help="path ไปยังไฟล์ Excel ชั่วโมงทำงาน")
    parser.add_argument("--config", default=None, help="path ไปยัง config.yaml (ค่าเริ่มต้น: config.yaml ข้างโปรแกรม)")
    parser.add_argument("--diagnose", action="store_true", help="ตรวจสอบสภาพแวดล้อมของเครื่องแล้วพิมพ์รายงานออกมา (ไม่ต้องใช้ --file)")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    args = parser.parse_args()

    config_path = args.config or os.path.join(base_dir, "config.yaml")
    log_startup_banner(config_path)

    if args.diagnose:
        return _run_diagnose(config_path, base_dir)

    if not args.file:
        print("[ERROR] ต้องระบุ --file (หรือใช้ --diagnose เพื่อตรวจสอบระบบ)", file=sys.stderr)
        return 1

    if not os.path.exists(config_path):
        print(f"[ERROR] ไม่พบไฟล์ config.yaml ที่: {config_path}", file=sys.stderr)
        print("กรุณาคัดลอก config.example.yaml เป็น config.yaml แล้วกรอกข้อมูล OpenProject ก่อนใช้งาน", file=sys.stderr)
        return 1

    try:
        config = load_config(config_path)
    except ConfigError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    if not os.path.exists(args.file):
        print(f"[ERROR] ไม่พบไฟล์ Excel ที่: {args.file}", file=sys.stderr)
        return 1

    client = OpenProjectClient(config["openproject"]["url"], config["openproject"]["api_key"])

    df = read_timesheet(args.file, config["excel"])
    unsynced = get_unsynced_rows(df)

    if unsynced.empty:
        print("ไม่มีแถวที่ต้อง sync")
        return 0

    updates: dict[int, dict] = {}
    success = 0
    skipped = 0

    for _, row in unsynced.iterrows():
        r = row_to_dict(row)
        excel_row = r["excel_row"]
        try:
            if r["wp_id"] is None:
                raise ValueError("ไม่พบ wp_id — CLI ไม่สร้าง Work Package ใหม่ให้ "
                                 "(รวมถึงแถวที่ตั้ง wp_mode = new ไว้) ให้เปิด TimesheetSync.exe เลือก/สร้างงานก่อน")
            if r["hours"] is None or r["hours"] <= 0:
                raise ValueError("ชั่วโมงไม่ถูกต้อง (ต้องมากกว่า 0)")
            if not r["date"]:
                raise ValueError("วันที่ไม่ถูกต้อง")
            if not r["activity"]:
                raise ValueError("ไม่ได้ระบุกิจกรรม (activity)")

            wp = client.get_work_package(r["wp_id"])
            if wp is None:
                raise ValueError(f"ไม่พบ Work Package #{r['wp_id']} ใน OpenProject")

            activity_id = resolve_activity_id(r["activity"], config["activity_mapping"])
            user_id = resolve_user_id(r["user_email"], config["user_mapping"])

            client.create_time_entry(
                work_package_id=r["wp_id"],
                spent_on=r["date"],
                hours=r["hours"],
                activity_id=activity_id,
                comment=r["comment"],
                user_id=user_id,
            )

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            updates[excel_row] = {"synced": True, "sync_note": f"บันทึกสำเร็จเมื่อ {timestamp}"}
            success += 1
            print(f"[OK] แถว {excel_row}: WP #{r['wp_id']} {r['hours']}ชม. วันที่ {r['date']}")

        except (OpenProjectAPIError, ValueError) as e:
            updates[excel_row] = {"synced": False, "sync_note": str(e)}
            skipped += 1
            print(f"[SKIP] แถว {excel_row}: {e}", file=sys.stderr)

    update_timesheet_rows(args.file, config["excel"], updates)

    print(f"\nสรุป: สำเร็จ {success} แถว, ข้าม/ล้มเหลว {skipped} แถว (ดูรายละเอียดที่คอลัมน์ sync_note)")
    return 0 if skipped == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

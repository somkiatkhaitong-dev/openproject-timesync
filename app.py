"""Timesheet Sync — native desktop app (PySide6). No local web server, no browser engine.

Usage (dev):
    python app.py [--file timesheet.xlsx] [--config config.yaml]

ไม่ต้องมี config.yaml ก่อนก็เปิดได้ — ถ้ายังไม่มี url/api_key จะเจอหน้า login ให้กรอกเอง
(ติ๊ก "จำไว้" เพื่อบันทึกกลับ config.yaml ไว้ใช้ครั้งถัดไปได้) login สำเร็จแล้วเข้าหน้าตารางหลักทันทีในสถานะว่าง
ไม่มีหน้าอัปโหลดแยกและไม่โหลดไฟล์ให้อัตโนมัติ — ผู้ใช้กด "⬆ อัปโหลดไฟล์ใหม่" เองเมื่อพร้อม จึงจะเด้ง native
file picker เลือกไฟล์เสร็จข้อมูลโหลดเข้าตารางทันที ไฟล์ที่เลือกจะถูกจำไว้ (last_file ใน config.yaml)
เอาไว้เป็นตำแหน่งตั้งต้นของ picker ครั้งถัดไป เช่นเดียวกับ --file
"""
from __future__ import annotations

import argparse
import os
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from common import default_config, load_config_raw
from gui.controller import AppController
from gui.logging_setup import install_excepthook, log_startup_banner, setup_logging
from gui.paths import app_base_dir, resource_path
from gui.state import AppState
from gui.theme import apply_theme
from version import APP_NAME, APP_VERSION

# ตอนแพ็กเป็น .exe แบบ --windowed จะไม่มี console ทำให้ sys.stdout/stderr เป็น None
# ซึ่ง print() ปกติจะ crash ทันที — ของเดิม redirect ทิ้งลง devnull แต่แบบนั้น traceback
# ทุกอันหายตามไปด้วย ตอนนี้เปลี่ยนปลายทางเป็นไฟล์ log แทน ต้องทำก่อนที่อื่นในไฟล์นี้จะ print
setup_logging(app_base_dir())


def _show_crash_dialog(exc_type, exc_value, _exc_tb) -> None:
    QMessageBox.critical(
        None,
        f"{APP_NAME} {APP_VERSION}",
        f"เกิดข้อผิดพลาดที่ไม่ได้คาดไว้\n\n{exc_type.__name__}: {exc_value}\n\n"
        "รายละเอียดถูกบันทึกไว้ในไฟล์ log แล้ว — กด 'ตรวจสอบระบบ' ที่หน้าเข้าสู่ระบบเพื่อดูตำแหน่งไฟล์",
    )


def main():
    base_dir = app_base_dir()

    parser = argparse.ArgumentParser(description="Preview/ยืนยันชั่วโมงทำงานก่อนส่งเข้า OpenProject")
    parser.add_argument("--file", help="ไฟล์ Excel ตั้งต้นที่จะเปิดค้างไว้ให้ในหน้าต่างเลือกไฟล์ (ไม่ระบุ = เลือกเองในแอป)")
    parser.add_argument("--config", default=None, help="path ไปยัง config.yaml (ค่าเริ่มต้น: config.yaml ข้างโปรแกรม, ไม่จำเป็นต้องมีอยู่แล้วก็ได้)")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    args = parser.parse_args()

    config_path = args.config or os.path.join(base_dir, "config.yaml")
    log_startup_banner(config_path)
    config = load_config_raw(config_path) if os.path.exists(config_path) else default_config()

    state = AppState(base_dir=base_dir, config_path=config_path, config=config)

    app = QApplication(sys.argv)
    apply_theme(app)
    # ต้องมี QApplication ก่อน ไม่งั้น QMessageBox ใน hook สร้างไม่ได้
    install_excepthook(_show_crash_dialog)

    icon_path = resource_path(os.path.join("assets", "icon.ico"))
    if os.path.exists(icon_path):
        from PySide6.QtGui import QIcon

        app.setWindowIcon(QIcon(icon_path))

    if args.file:
        if not os.path.exists(args.file):
            QMessageBox.critical(None, "Timesheet Sync", f"ไม่พบไฟล์ Excel ที่:\n{args.file}")
            sys.exit(1)
        state.cli_file = args.file

    controller = AppController(state)
    controller.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

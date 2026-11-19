"""Path helpers shared across gui/ modules — mirrors the old app.py helpers,
adapted for a PyInstaller onefile build with no templates/static folders."""
from __future__ import annotations

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(relative_path: str) -> str:
    """ที่อยู่ของ asset ที่ถูก bundle ไปกับ .exe (เช่น assets/icon.ico) ทั้งตอนรันด้วย python และตอนแพ็กเป็น .exe"""
    base = getattr(sys, "_MEIPASS", _PROJECT_ROOT)
    return os.path.join(base, relative_path)


def app_base_dir() -> str:
    """โฟลเดอร์ที่ควรมองหา config.yaml / ไฟล์ Excel — ข้าง .exe เวลาแพ็ก, ข้าง app.py เวลารันด้วย python"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return _PROJECT_ROOT

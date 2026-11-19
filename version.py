"""เลขเวอร์ชันของแอป — แหล่งความจริงเพียงที่เดียว

ทั้ง app.py, sync_timesheet.py และ installer.iss อ้างค่าจากไฟล์นี้ (build_installer.bat
อ่านค่าแล้วส่งเข้า ISCC ผ่าน /DMyAppVersion) เพื่อไม่ให้เลขในแอปกับในตัวติดตั้งหลุดจากกัน
เวลาผู้ใช้ปลายทางแจ้งปัญหาแล้วเราถามว่ารัน build ไหนอยู่
"""
from __future__ import annotations

APP_VERSION = "1.5.0"
APP_NAME = "Timesheet Sync"

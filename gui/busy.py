"""ตัวบอกว่า "กำลังทำงานอยู่" ที่ใช้ร่วมกันทั้งแอป

เดิมทุกปุ่มที่รอนานเขียน setEnabled(False) + setText("กำลัง...") เองซ้ำๆ ราว 10 จุด ปัญหาคือ
QSS ย้อมปุ่ม disabled เป็นสีเทา (theme.py: QPushButton#outlineButton:disabled) ข้อความ
"กำลัง..." จึงจางจนดูเหมือนปุ่มตายมากกว่าปุ่มที่กำลังทำงาน set_button_busy จึงติด property
busy ให้ด้วย เพื่อให้กฎ [busy="true"]:disabled ย้อมกลับเป็นสี accent ที่อ่านออก

หลอดโหลดมี 2 แบบ เลือกตามว่ารู้ปริมาณงานล่วงหน้าหรือไม่ — อย่าใช้หลอดมี % กับงานที่ต้องเดา
เวลาเอา เพราะเวลาที่เดาผิดจะกลายเป็นหลอดค้างที่ 99% ซึ่งแย่กว่าไม่มีหลอดเลย
"""
from __future__ import annotations

from PySide6.QtWidgets import QProgressBar, QPushButton

BAR_HEIGHT = 6


def _make_bar(width: int) -> QProgressBar:
    bar = QProgressBar()
    bar.setObjectName("busyBar")
    bar.setTextVisible(False)
    bar.setFixedSize(width, BAR_HEIGHT)
    bar.hide()  # ผู้เรียกเป็นคน setVisible เอง — แถวปุ่มตอนพักต้องหน้าตาเหมือนเดิม
    return bar


def make_busy_bar(width: int = 150) -> QProgressBar:
    """หลอดวิ่งไปมา สำหรับงานที่ไม่รู้ว่าจะเสร็จเมื่อไหร่

    range 0-0 คือโหมด indeterminate ของ Qt — วิ่งเองไม่ต้องมี QTimer ช่วย
    """
    bar = _make_bar(width)
    bar.setRange(0, 0)
    return bar


def make_progress_bar(width: int = 150) -> QProgressBar:
    """หลอดแบบมี % จริง สำหรับงานที่นับจำนวนได้ล่วงหน้า (เช่นส่ง timesheet ทีละแถว)

    ใช้คู่กับ set_progress() ด้านล่าง
    """
    bar = _make_bar(width)
    bar.setRange(0, 100)
    bar.setValue(0)
    return bar


def set_progress(bar: QProgressBar, done: int, total: int) -> None:
    """เดินหลอดตามจำนวนงานที่เสร็จจริง — total เป็น 0 ให้ถอยไปเป็นหลอดวิ่งแทนการหาร 0"""
    if total <= 0:
        bar.setRange(0, 0)
        return
    bar.setRange(0, total)
    bar.setValue(min(done, total))


def set_button_busy(btn: QPushButton, running: bool, busy_text: str, idle_text: str) -> None:
    """ปิดปุ่ม + เปลี่ยนข้อความ + ย้อมสี accent ระหว่างทำงาน

    ใช้ pattern setProperty + unpolish/polish เดียวกับปุ่ม filter/tab ที่อื่นในแอป — Qt ไม่คิด
    QSS ใหม่ให้เองเมื่อ property เปลี่ยน ต้องบอกให้ re-evaluate เอง
    """
    btn.setEnabled(not running)
    btn.setText(busy_text if running else idle_text)
    btn.setProperty("busy", running)
    btn.style().unpolish(btn)
    btn.style().polish(btn)

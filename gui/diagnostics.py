"""หน้าต่างแสดงผลตรวจสอบระบบและ log — เปลือก Qt บางๆ ครอบ diagnostics.py

แยกส่วนตรรกะการตรวจไว้ที่ diagnostics.py (ไม่มี Qt) เพื่อให้ CLI เรียกใช้ชุดเดียวกันได้ผ่าน
sync_timesheet.py --diagnose
"""
from __future__ import annotations

import os

from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from diagnostics import run_diagnostics
from gui.logging_setup import log_file_path
from gui.theme import FONT_MONO
from gui.threads import run_async

_LOG_TAIL_LINES = 400


class TextReportDialog(QDialog):
    """กล่องข้อความยาวแบบเลือก/ก็อป/บันทึกได้ — ใช้ทั้งกับรายงานตรวจสอบและ log

    มีเพราะ error เดิมถูกยัดลง QLabel ในการ์ดกว้าง 400px ข้อความยาวจึงล้นกรอบและก็อปไม่ได้
    ผู้ใช้ปลายทางเลยส่งรายละเอียดกลับมาให้ไม่ได้เลยนอกจากถ่ายรูปจอ
    """

    def __init__(self, title: str, text: str = "", parent=None, default_filename: str = "report.txt"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(820, 560)
        self._default_filename = default_filename

        layout = QVBoxLayout(self)

        self.body = QPlainTextEdit()
        self.body.setReadOnly(True)
        self.body.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.body.setFont(QFont(FONT_MONO, 9))
        self.body.setPlainText(text)
        layout.addWidget(self.body)

        self.status = QLabel()
        self.status.setObjectName("hintLabel")
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.copy_btn = QPushButton("คัดลอกทั้งหมด")
        self.copy_btn.setObjectName("outlineButton")
        self.copy_btn.clicked.connect(self._copy)
        buttons.addWidget(self.copy_btn)

        self.save_btn = QPushButton("บันทึกเป็นไฟล์")
        self.save_btn.setObjectName("mutedButton")
        self.save_btn.clicked.connect(self._save)
        buttons.addWidget(self.save_btn)

        buttons.addStretch(1)

        close_btn = QPushButton("ปิด")
        close_btn.setObjectName("mutedButton")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)

        layout.addLayout(buttons)

    def set_text(self, text: str) -> None:
        self.body.setPlainText(text)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.body.toPlainText())
        self.status.setText("คัดลอกไปยังคลิปบอร์ดแล้ว")

    def _save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "บันทึกรายงาน", self._default_filename, "Text (*.txt)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.body.toPlainText())
        except OSError as e:
            self.status.setText(f"บันทึกไม่สำเร็จ: {e}")
        else:
            self.status.setText(f"บันทึกแล้วที่ {path}")


def show_diagnostics(parent, url: str, api_key: str, config_path: str | None) -> None:
    """เปิดหน้าต่างแล้วรันการตรวจเบื้องหลัง (มีการต่อ network จริง จึงห้ามรันบน UI thread)"""
    dialog = TextReportDialog(
        "ตรวจสอบระบบ", "กำลังตรวจสอบ...", parent, default_filename="timesheetsync-diagnostics.txt"
    )
    dialog.copy_btn.setEnabled(False)
    dialog.save_btn.setEnabled(False)
    dialog.status.setText("กำลังตรวจ DNS / TCP / TLS และเรียก API — อาจใช้เวลาสักครู่")

    def done(report: str) -> None:
        dialog.set_text(report)
        dialog.copy_btn.setEnabled(True)
        dialog.save_btn.setEnabled(True)
        dialog.status.setText("ตรวจเสร็จแล้ว — กดคัดลอกทั้งหมดเพื่อส่งให้ผู้ดูแล")

    def failed(message: str) -> None:
        dialog.set_text(f"ตรวจสอบไม่สำเร็จ: {message}")
        dialog.copy_btn.setEnabled(True)
        dialog.save_btn.setEnabled(True)
        dialog.status.setText("")

    run_async(
        run_diagnostics,
        url,
        api_key,
        config_path,
        on_success=done,
        on_error=failed,
    )
    dialog.exec()


def read_log_tail(lines: int = _LOG_TAIL_LINES) -> str:
    path = log_file_path()
    if not path:
        return "ยังไม่มีไฟล์ log (เขียนไฟล์ไม่ได้)"
    if not os.path.exists(path):
        return f"ยังไม่มีไฟล์ log ที่ {path}"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read().splitlines()
    except OSError as e:
        return f"อ่าน log ไม่ได้: {e}"
    tail = content[-lines:]
    return f"{path}\n(แสดง {len(tail)} บรรทัดท้ายสุด)\n" + "-" * 62 + "\n" + "\n".join(tail)


def show_log(parent) -> None:
    dialog = TextReportDialog(
        "รายละเอียดทางเทคนิค (log)",
        read_log_tail(),
        parent,
        default_filename="timesheetsync-log.txt",
    )
    dialog.status.setText("บรรทัดล่างสุดคือเหตุการณ์ล่าสุด — ไม่มี API key อยู่ในนี้")
    dialog.exec()


def show_text(parent, title: str, text: str) -> None:
    dialog = TextReportDialog(title, text, parent)
    dialog.exec()


__all__ = [
    "TextReportDialog",
    "show_diagnostics",
    "show_log",
    "show_text",
    "read_log_tail",
]

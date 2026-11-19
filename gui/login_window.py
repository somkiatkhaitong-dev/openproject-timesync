"""Login screen — ported from templates/login.html + static/auth.js.

Shown on every launch (never auto-skipped even with valid stored credentials — only
pre-fills the fields). Validates via a background, read-only client.get_projects() call
so the UI never freezes.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from common import OpenProjectClient
from gui.busy import make_busy_bar, set_button_busy
from gui.diagnostics import show_diagnostics, show_log
from gui.threads import run_async
from version import APP_VERSION


class LoginWindow(QWidget):
    # client, url, api_key, remember
    # client, url, api_key, remember, projects (ผลจาก get_projects ที่ใช้ตรวจ credential)
    logged_in = Signal(object, str, str, bool, object)

    def __init__(
        self,
        prefill_url: str = "",
        prefill_api_key: str = "",
        config_path: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        # เก็บไว้ให้หน้าตรวจสอบระบบรายงานว่าอ่าน/เขียน config จากที่ไหน
        self.config_path = config_path
        self._build_ui(prefill_url, prefill_api_key)

    def _build_ui(self, prefill_url: str, prefill_api_key: str) -> None:
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignCenter)

        card = QFrame()
        card.setObjectName("card")
        card.setFixedWidth(400)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(32, 32, 32, 32)
        card_layout.setSpacing(10)

        logo = QLabel("⏱")
        logo.setObjectName("authLogo")
        logo.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(logo)

        title = QLabel("Timesheet Sync")
        title.setObjectName("authTitle")
        title.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(title)

        subtitle = QLabel("เข้าสู่ระบบด้วย OpenProject API key ของคุณ")
        subtitle.setObjectName("authSubtitle")
        subtitle.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(subtitle)

        card_layout.addSpacing(14)

        url_label = QLabel("OpenProject URL")
        url_label.setObjectName("fieldLabel")
        card_layout.addWidget(url_label)

        self.url_input = QLineEdit(prefill_url)
        self.url_input.setPlaceholderText("https://your-openproject-instance.com")
        card_layout.addWidget(self.url_input)

        key_label = QLabel("API Key")
        key_label.setObjectName("fieldLabel")
        card_layout.addWidget(key_label)

        self.api_key_input = QLineEdit(prefill_api_key)
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setPlaceholderText("วาง API key ที่นี่")
        card_layout.addWidget(self.api_key_input)

        self.remember_checkbox = QCheckBox("จำไว้สำหรับครั้งถัดไป (บันทึกลง config.yaml บนเครื่องนี้)")
        card_layout.addWidget(self.remember_checkbox)

        self.error_label = QLabel()
        self.error_label.setObjectName("authError")
        self.error_label.setWordWrap(True)
        self.error_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.error_label.hide()
        card_layout.addWidget(self.error_label)

        # ปุ่มเปิด log ตัวเต็ม โผล่เฉพาะตอนมี error — ข้อความในกล่องแดงเป็นฉบับย่อเสมอ
        self.detail_btn = QPushButton("ดูรายละเอียดทางเทคนิค")
        self.detail_btn.setObjectName("linkButton")
        self.detail_btn.setCursor(Qt.PointingHandCursor)
        self.detail_btn.clicked.connect(lambda: show_log(self))
        self.detail_btn.hide()
        card_layout.addWidget(self.detail_btn, alignment=Qt.AlignRight)

        card_layout.addSpacing(4)

        self.submit_btn = QPushButton("เข้าสู่ระบบ")
        self.submit_btn.setObjectName("primaryButton")
        self.submit_btn.setCursor(Qt.PointingHandCursor)
        self.submit_btn.clicked.connect(self._on_submit)
        card_layout.addWidget(self.submit_btn)

        # ตรวจ credential ต้องยิงเน็ต ช้าได้หลายวินาทีบนเครือข่ายที่มี proxy คั่น
        self.login_progress = make_busy_bar()
        card_layout.addWidget(self.login_progress, alignment=Qt.AlignCenter)

        self.diagnostics_btn = QPushButton("ตรวจสอบระบบ")
        self.diagnostics_btn.setObjectName("mutedButton")
        self.diagnostics_btn.setCursor(Qt.PointingHandCursor)
        self.diagnostics_btn.clicked.connect(self._on_diagnostics)
        card_layout.addWidget(self.diagnostics_btn)

        hint = QLabel("ดู API key ได้จาก OpenProject: My Account > Access tokens")
        hint.setObjectName("hintLabel")
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        card_layout.addWidget(hint)

        version_label = QLabel(f"เวอร์ชัน {APP_VERSION}")
        version_label.setObjectName("hintLabel")
        version_label.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(version_label)

        outer.addWidget(card)

        self.url_input.returnPressed.connect(self._on_submit)
        self.api_key_input.returnPressed.connect(self._on_submit)

    def reset_fields_for_relogin(self) -> None:
        self.error_label.hide()
        self.detail_btn.hide()
        self._set_login_busy(False)

    def _set_login_busy(self, running: bool) -> None:
        set_button_busy(self.submit_btn, running, "กำลังตรวจสอบ...", "เข้าสู่ระบบ")
        self.login_progress.setVisible(running)
        self.diagnostics_btn.setEnabled(not running)

    def _on_diagnostics(self) -> None:
        show_diagnostics(
            self,
            self.url_input.text().strip(),
            self.api_key_input.text().strip(),
            self.config_path,
        )

    def _on_submit(self) -> None:
        url = self.url_input.text().strip()
        api_key = self.api_key_input.text().strip()
        remember = self.remember_checkbox.isChecked()

        self.error_label.hide()
        self.detail_btn.hide()

        if not url or not api_key:
            self._show_error("กรุณากรอก URL และ API key ให้ครบ")
            return

        self._set_login_busy(True)

        client = OpenProjectClient(url, api_key)
        run_async(
            client.get_projects,
            on_success=lambda projects: self._on_verified(client, url, api_key, remember, projects),
            on_error=self._on_error,
        )

    def _on_verified(
        self, client: OpenProjectClient, url: str, api_key: str, remember: bool, projects: list
    ) -> None:
        self._set_login_busy(False)
        # ส่งรายชื่อโปรเจกต์ที่เพิ่งดึงมาตรวจ credential ต่อไปด้วย แทนที่จะทิ้ง — ฝั่ง picker
        # ต้องใช้ชุดเดียวกันนี้อยู่แล้ว
        self.logged_in.emit(client, url, api_key, remember, projects or [])

    def _on_error(self, message: str) -> None:
        self._set_login_busy(False)
        # message ผ่าน describe_request_error/describe_status_error มาแล้ว จึงเป็นภาษาคน
        # และมีคำแนะนำในตัว ไม่ต้องเติมคำนำหน้าซ้ำอีก
        self._show_error(message, with_detail=True)

    def _show_error(self, text: str, with_detail: bool = False) -> None:
        self.error_label.setText(text)
        self.error_label.show()
        self.detail_btn.setVisible(with_detail)

"""Top-level QStackedWidget that owns the login<->main-window transition.

Logout never quits the app — it just clears the in-memory client/credentials and switches
the stack back to LoginWindow (mirrors the old api_logout route).
"""
from __future__ import annotations

from PySide6.QtWidgets import QStackedWidget

from common import save_credentials
from gui.login_window import LoginWindow
from gui.main_window import MainWindow
from gui.state import AppState
from version import APP_NAME, APP_VERSION


class AppController(QStackedWidget):
    def __init__(self, state: AppState):
        super().__init__()
        self.state = state
        # เวอร์ชันอยู่บน title bar เพื่อให้ผู้ใช้ปลายทางอ่านบอกได้ทันทีเวลาแจ้งปัญหา
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.resize(1280, 860)
        self.setMinimumSize(960, 640)

        op = state.config["openproject"]
        self.login_window = LoginWindow(
            prefill_url=op.get("url", ""),
            prefill_api_key=op.get("api_key", ""),
            config_path=state.config_path,
        )
        self.login_window.logged_in.connect(self._on_logged_in)

        self.main_window = MainWindow(state)
        self.main_window.logout_requested.connect(self._on_logout)

        self.addWidget(self.login_window)
        self.addWidget(self.main_window)
        self.setCurrentWidget(self.login_window)

    def _on_logged_in(self, client, url: str, api_key: str, remember: bool, projects) -> None:
        self.state.client = client
        self.state.config["openproject"]["url"] = url
        self.state.config["openproject"]["api_key"] = api_key

        if remember:
            try:
                save_credentials(self.state.config_path, url, api_key)
            except Exception as e:
                print(f"[WARN] บันทึก config.yaml ไม่สำเร็จ: {e}", flush=True)

        self.main_window.seed_projects(projects)
        self.setCurrentWidget(self.main_window)
        self.main_window.on_shown_after_login()

    def _on_logout(self) -> None:
        self.state.client = None
        self.state.config["openproject"]["api_key"] = ""
        self.main_window.reset_for_logout()
        self.login_window.reset_fields_for_relogin()
        self.setCurrentWidget(self.login_window)

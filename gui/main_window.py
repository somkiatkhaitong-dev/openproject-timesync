"""Main table window — header, file loading, filters, editable table, and the summary dock.

File loading is never automatic: after login the table just shows an empty-state message and
waits. "⬆ อัปโหลดไฟล์ใหม่" is the only entry point — it opens a native QFileDialog positioned
at --file / config['last_file'] if either still exists (as a starting point only, nothing is
read until the user picks). Old data stays visible in the table until a newly picked file is
confirmed loadable.

Filters / summary / submit-button gating ported from applyFilter/renderSummary/
updateSubmitButton in static/app.js. WP picker interaction (Stage 4) and the real submit
flow (Stage 5) still land later — the submit button here is gated correctly but not wired.
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from common import check_existing_work, find_duplicate_subjects, save_last_file
from gui.busy import make_busy_bar, set_button_busy
from gui.gen_task_tab import GenTaskTab
from gui.history_tab import HistoryTab
from gui.loader import load_rows_from_excel
from gui.row_model import RowState, build_row_state, new_manual_row
from gui.state import AppState
from gui.submit import submit_rows
from gui.summary_dock import SummaryDock
from gui.table_controller import TableController
from gui.theme import COLORS
from gui.threads import run_async
from gui.worklog_tab import WorklogTab
from gui.wp_picker_widget import PickerContext, PickerSharedState

SEARCH_DEBOUNCE_MS = 250
# สั้นกว่า debounce ของช่องค้นหามาก เพราะนี่ไม่ใช่การหน่วงให้ผู้ใช้พิมพ์จบ แค่รวบ event ที่มาติดกัน
# รัวๆ (ติ๊กหลายช่อง / พิมพ์ชื่องาน) ให้เหลือรอบเดียว — สั้นพอที่ยังรู้สึกว่าอัปเดตทันที
RECOMPUTE_COALESCE_MS = 50
MAX_PREVIEW_LINES = 10


class MainWindow(QWidget):
    logout_requested = Signal()

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self._loaded_once = False
        self._activities: list[dict] = []
        self._activities_loading = False
        # cache ของ picker (โปรเจกต์/ประเภทงาน) อยู่ยาวทั้ง session เหมือน _activities —
        # เดิม _build_picker_context สร้างใหม่ทุกครั้งที่เปิดไฟล์/reload หลัง submit ทำให้ต้อง
        # ยิง get_projects/get_types ใหม่ทั้งชุดทุกรอบ
        self._picker_shared = PickerSharedState()
        # กันกดปุ่มเช็คซ้ำระหว่างที่รอบก่อนยังไม่กลับ
        self._checking = False

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._apply_search)

        # _recompute_filters_and_gating วนทุกแถว ~6 รอบ + วาดสรุปใหม่ แต่ถูกกระตุ้นทุกครั้งที่ติ๊ก
        # checkbox หนึ่งช่องและทุกตัวอักษรที่พิมพ์ในช่องชื่องานใหม่ — รวบให้เหลือรอบเดียว
        self._recompute_timer = QTimer(self)
        self._recompute_timer.setSingleShot(True)
        self._recompute_timer.setInterval(RECOMPUTE_COALESCE_MS)
        self._recompute_timer.timeout.connect(self._recompute_filters_and_gating)

        self._build_ui()

    def _schedule_recompute(self) -> None:
        self._recompute_timer.start()  # start() บน single-shot ที่วิ่งอยู่ = เริ่มนับใหม่

    def _make_divider(self) -> QWidget:
        divider = QWidget()
        divider.setFixedWidth(1)
        divider.setStyleSheet(f"background: {COLORS['border']};")
        return divider

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(20)

        header = QHBoxLayout()

        brand = QLabel("⏱ Timesheet Sync")
        brand.setStyleSheet("font-weight: 600; font-size: 16px;")
        header.addWidget(brand)

        self.file_badge = QLabel()
        self.file_badge.setObjectName("fileBadge")
        self.file_badge.hide()
        header.addWidget(self.file_badge)

        header.addStretch()

        self.add_row_btn = QPushButton("+ เพิ่มแถว")
        self.add_row_btn.setObjectName("outlineButton")
        self.add_row_btn.setCursor(Qt.PointingHandCursor)
        self.add_row_btn.setToolTip("เพิ่มรายการชั่วโมงเอง — จะถูกต่อท้ายไฟล์ Excel ตอนกดส่ง")
        self.add_row_btn.setEnabled(False)  # ต้องมีไฟล์อยู่ก่อน ถึงจะรู้ว่าจะต่อท้ายไฟล์ไหน
        self.add_row_btn.clicked.connect(self._on_add_row_clicked)
        header.addWidget(self.add_row_btn)

        self.clear_file_btn = QPushButton("✕ เคลียร์ไฟล์")
        self.clear_file_btn.setObjectName("mutedButton")
        self.clear_file_btn.setCursor(Qt.PointingHandCursor)
        self.clear_file_btn.setToolTip("ล้างไฟล์ที่โหลดอยู่ออกจากตาราง (ไฟล์ต้นฉบับไม่ถูกลบ)")
        self.clear_file_btn.setEnabled(False)  # ต้องมีไฟล์โหลดอยู่ก่อน
        self.clear_file_btn.clicked.connect(self._on_clear_file_clicked)
        header.addWidget(self.clear_file_btn)

        header.addSpacing(8)
        header.addWidget(self._make_divider())
        header.addSpacing(8)

        self.upload_btn = QPushButton("⬆ อัปโหลดไฟล์ใหม่")
        self.upload_btn.setObjectName("outlineButton")
        self.upload_btn.setCursor(Qt.PointingHandCursor)
        self.upload_btn.clicked.connect(self._on_upload_new_clicked)
        header.addWidget(self.upload_btn)

        self.logout_btn = QPushButton("ออกจากระบบ")
        self.logout_btn.setObjectName("linkButton")
        self.logout_btn.setCursor(Qt.PointingHandCursor)
        self.logout_btn.clicked.connect(self.logout_requested.emit)
        header.addWidget(self.logout_btn)

        layout.addLayout(header)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, stretch=1)

        sync_tab = QWidget()
        sync_layout = QVBoxLayout(sync_tab)
        sync_layout.setContentsMargins(16, 16, 16, 16)
        sync_layout.setSpacing(14)

        toolbar = QHBoxLayout()
        self.filter_all_btn = QPushButton("ทั้งหมด (0)")
        self.filter_all_btn.setObjectName("filterButton")
        self.filter_all_btn.setProperty("active", True)
        self.filter_all_btn.setCursor(Qt.PointingHandCursor)
        self.filter_all_btn.clicked.connect(lambda: self._set_filter("all"))
        toolbar.addWidget(self.filter_all_btn)

        self.filter_unresolved_btn = QPushButton("ต้องเลือก (0)")
        self.filter_unresolved_btn.setObjectName("filterButton")
        self.filter_unresolved_btn.setProperty("active", False)
        self.filter_unresolved_btn.setCursor(Qt.PointingHandCursor)
        self.filter_unresolved_btn.clicked.connect(lambda: self._set_filter("unresolved"))
        toolbar.addWidget(self.filter_unresolved_btn)

        toolbar.addSpacing(8)
        toolbar.addWidget(self._make_divider())
        toolbar.addSpacing(8)

        self.select_all_btn = QPushButton("เลือกทั้งหมด")
        self.select_all_btn.setObjectName("outlineButton")
        self.select_all_btn.setCursor(Qt.PointingHandCursor)
        self.select_all_btn.clicked.connect(self._on_select_all_clicked)
        toolbar.addWidget(self.select_all_btn)

        self.select_none_btn = QPushButton("ล้างการเลือก")
        self.select_none_btn.setObjectName("outlineButton")
        self.select_none_btn.setCursor(Qt.PointingHandCursor)
        self.select_none_btn.clicked.connect(self._on_select_none_clicked)
        toolbar.addWidget(self.select_none_btn)

        toolbar.addSpacing(8)
        toolbar.addWidget(self._make_divider())
        toolbar.addSpacing(8)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("ค้นหาชื่องาน, หมายเหตุ, วันที่, เลขงาน...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._search_timer.start)
        toolbar.addWidget(self.search_edit, stretch=1)

        self.search_count_label = QLabel("")
        self.search_count_label.setObjectName("fieldLabel")
        toolbar.addWidget(self.search_count_label)

        self.check_btn = QPushButton("เช็คบน OpenProject")
        self.check_btn.setObjectName("outlineButton")
        self.check_btn.setCursor(Qt.PointingHandCursor)
        self.check_btn.setEnabled(False)
        self.check_btn.setToolTip(
            "ถาม OpenProject ว่างานในตารางนี้มีอยู่แล้วไหม และวันนั้นลงชั่วโมงไปหรือยัง\n"
            "ยิง 1 คำขอต่อชื่องานใหม่ที่ไม่ซ้ำกัน + อีก 1 คำขอดึงชั่วโมงตามช่วงวันที่ในไฟล์\n"
            "อ่านอย่างเดียว ไม่สร้างและไม่แก้อะไรบน OpenProject"
        )
        self.check_btn.clicked.connect(self._start_check)
        toolbar.addWidget(self.check_btn)

        # ไม่รู้ว่าจะเสร็จเมื่อไหร่ (ขึ้นกับจำนวนชื่องาน) จึงเป็นหลอดวิ่ง ไม่ใช่หลอด %
        self.check_progress = make_busy_bar(120)
        toolbar.addWidget(self.check_progress)

        self.dup_check = QCheckBox("ตรวจชื่องานซ้ำก่อนส่ง")
        self.dup_check.setChecked(True)
        self.dup_check.setCursor(Qt.PointingHandCursor)
        self.dup_check.setToolTip(
            "ค้นหาใน OpenProject ว่ามีงานชื่อเดียวกันอยู่แล้วไหม ก่อนสร้างงานใหม่\n"
            "ยิง 1 คำขอต่อชื่องานที่ไม่ซ้ำกัน — ถ้ามีหลายสิบแถวจะใช้เวลาสักครู่"
        )
        toolbar.addWidget(self.dup_check)

        sync_layout.addLayout(toolbar)

        self.table = QTableWidget()
        sync_layout.addWidget(self.table, stretch=1)
        self.table_controller = TableController(
            self.table,
            on_row_edited=self._on_row_edited,
            on_wp_changed=self._on_wp_row_changed,
            on_selection_changed=self._schedule_recompute,
        )

        self.summary_dock = SummaryDock()
        self.summary_dock.submit_clicked.connect(self._on_submit_clicked)
        sync_layout.addWidget(self.summary_dock)

        self.tabs.addTab(sync_tab, "อัปโหลด & ส่งเข้า OpenProject")

        self.history_tab = HistoryTab(self.state)
        self.tabs.addTab(self.history_tab, "ประวัติตามช่วงวันที่")

        self.worklog_tab = WorklogTab(self.state, self._picker_shared)
        self.worklog_tab.file_ready.connect(self._on_worklog_file_ready)
        self.tabs.addTab(self.worklog_tab, "สรุปงาน")

        self.gen_task_tab = GenTaskTab(self.state)
        self.tabs.addTab(self.gen_task_tab, "สร้างไฟล์ Template")

    def _on_worklog_file_ready(self, path: str) -> None:
        """แท็บ Git เขียน .xlsx เสร็จแล้ว — โหลดเข้าตารางหลักต่อทันที ไม่ต้องผ่าน file dialog

        ใช้ _load_file ตัวเดิมเพื่อให้ได้พฤติกรรมเหมือนกดอัปโหลดเองทุกอย่าง (ป้ายชื่อไฟล์,
        save_last_file, picker context, โหลด activity)
        """
        self.tabs.setCurrentIndex(0)
        self._load_file(path)

    def on_shown_after_login(self) -> None:
        # ไม่โหลดไฟล์และไม่เด้ง picker อัตโนมัติ — ผู้ใช้กด "⬆ อัปโหลดไฟล์ใหม่" เองเมื่อพร้อม
        self._show_no_file_state()
        self._ensure_activities_loaded()

    def _ensure_activities_loaded(self) -> None:
        """ดึงรายการ activity จากเซิร์ฟเวอร์ครั้งเดียวต่อ login (ช้าราว 5 วิ จึงทำเบื้องหลัง)"""
        if self._activities or self._activities_loading or not self.state.client:
            return
        self._activities_loading = True
        run_async(
            self.state.client.get_activities,
            on_success=self._on_activities_loaded,
            on_error=self._on_activities_error,
        )

    def _on_activities_loaded(self, activities: list) -> None:
        self._activities_loading = False
        if not self.state.client:
            return  # logout ระหว่างที่ยังโหลดค้างอยู่ — ผลลัพธ์ของ client เก่าไม่ต้องใช้แล้ว
        self._activities = activities or []
        self._apply_default_activities(self.table_controller.rows)
        self.table_controller.set_activities(self._activities)
        if self.table_controller.rows:
            self._recompute_filters_and_gating()

    def _on_activities_error(self, message: str) -> None:
        # ดึงไม่ได้ก็ยังทำงานต่อได้ด้วย activity_mapping ใน config เหมือนเดิม แค่ไม่มี dropdown ให้เลือก
        self._activities_loading = False
        print(f"[WARN] โหลดรายการกิจกรรมจาก OpenProject ไม่สำเร็จ: {message}", flush=True)

    def _apply_default_activities(self, rows: list[RowState]) -> None:
        """เลือก activity ตั้งต้นให้แต่ละแถว: activity_mapping ก่อน แล้วค่อยจับคู่ด้วยชื่อที่ตรงกัน
        แถวที่หาไม่เจอจะเว้นว่างไว้ให้เลือกเอง (นับเป็น "ต้องเลือก" จนกว่าจะเลือก)"""
        mapping = self.state.config.get("activity_mapping") or {}
        by_name = {(a["name"] or "").strip().lower(): a["id"] for a in self._activities}
        valid_ids = set(by_name.values())

        for row in rows:
            if row.activity_id and (not valid_ids or row.activity_id in valid_ids):
                continue
            name = (row.activity or "").strip()
            candidate = mapping.get(name)
            if candidate is None:
                candidate = by_name.get(name.lower())
            try:
                candidate = int(candidate) if candidate is not None else None
            except (TypeError, ValueError):
                candidate = None
            # id จาก config ที่ไม่มีอยู่จริงบนเซิร์ฟเวอร์ถือว่าใช้ไม่ได้
            row.activity_id = candidate if (candidate is None or not valid_ids or candidate in valid_ids) else None

    def _show_no_file_state(self) -> None:
        # หน้าตาต้องเหมือนกันทั้ง login ครั้งแรกและ login ซ้ำหลัง logout
        self.table_controller.show_empty_message(
            'ยังไม่ได้เลือกไฟล์ — กด "⬆ อัปโหลดไฟล์ใหม่" ด้านบนเพื่อเลือกไฟล์'
        )
        self.filter_all_btn.setText("ทั้งหมด (0)")
        self.filter_unresolved_btn.setText("ต้องเลือก (0)")
        self.add_row_btn.setEnabled(False)
        self.clear_file_btn.setEnabled(False)
        self.summary_dock.clear()
        self.table_controller.clear_check_result()
        self.summary_dock.set_logged_by_date({})

    def _picker_start_path(self) -> str:
        # --file ตอนรัน หรือไฟล์ล่าสุดที่เคยเลือก ใช้เป็นตำแหน่งตั้งต้นของ dialog (ไม่ได้โหลดให้เอง)
        for candidate in (self.state.cli_file, self.state.config.get("last_file")):
            if candidate and os.path.exists(candidate):
                return candidate
        return ""

    def _prompt_file_pick(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "เลือกไฟล์ Excel ชั่วโมงทำงาน",
            self._picker_start_path(),
            "Excel files (*.xlsx);;All files (*.*)",
        )
        if not path:
            return
        self.tabs.setCurrentIndex(0)
        self._load_file(path)

    def _on_upload_new_clicked(self) -> None:
        self._prompt_file_pick()

    def _on_add_row_clicked(self) -> None:
        row = new_manual_row()
        self._apply_default_activities([row])
        self.table_controller.add_row(row)
        self._recompute_filters_and_gating()

    def _on_clear_file_clicked(self) -> None:
        reply = QMessageBox.question(
            self,
            "Timesheet Sync",
            'ล้างไฟล์ที่โหลดอยู่ตอนนี้ออกจากตาราง?\n'
            'การแก้ไขที่ยังไม่ได้กด "ยืนยันและส่งเข้า OpenProject" จะหายไป\n'
            "(ไฟล์ต้นฉบับบนเครื่องไม่ถูกลบ)",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._clear_file()

    def _clear_file(self) -> None:
        # ต่างจาก reset_for_logout: ยังอยู่ session เดิม ไม่ต้องดึง activities ใหม่หรือแตะ tab ประวัติ
        self.state.file_path = None
        self._loaded_once = False
        self.file_badge.hide()
        self.table_controller.set_rows([])  # ล้าง picker_ctx ที่ผูกกับ client เก่าไปด้วย
        self._clear_search()
        self._set_filter("all")
        self._show_no_file_state()

    def reset_for_logout(self) -> None:
        # ล้างข้อมูลของผู้ใช้คนเดิมออกให้หมด — login รอบใหม่ต้องเริ่มที่สถานะว่างเสมอ
        self.state.file_path = None
        self._loaded_once = False
        self._activities = []  # login รอบใหม่อาจเป็นคนละเซิร์ฟเวอร์ — ดึงรายการกิจกรรมใหม่
        self._activities_loading = False
        self._picker_shared = PickerSharedState()  # เช่นกัน โปรเจกต์คนละเซิร์ฟเวอร์ใช้ร่วมกันไม่ได้
        self.table_controller.set_activities([])
        self.file_badge.hide()
        self.table_controller.set_rows([])  # ล้าง picker_ctx ที่ผูกกับ client เก่าไปด้วย
        self._clear_search()
        self._set_filter("all")
        self._show_no_file_state()
        self.history_tab.reset()  # คนละ user/server รอบใหม่ ข้อมูลประวัติเก่าต้องไม่ค้าง
        self.worklog_tab.reset()  # เช่นกัน — project id ที่เลือกไว้เป็นของเซิร์ฟเวอร์เก่า
        self.tabs.setCurrentIndex(0)

    def _load_file(self, path: str) -> None:
        client = self.state.client
        excel_cfg = self.state.config["excel"]
        if not self._loaded_once:
            self.table_controller.show_empty_message("กำลังโหลดไฟล์...")

        run_async(
            load_rows_from_excel,
            client,
            path,
            excel_cfg,
            on_success=lambda rows: self._on_load_success(path, rows),
            on_error=self._on_load_error,
        )

    def _on_load_success(self, path: str, server_rows: list) -> None:
        self.state.file_path = path
        self._loaded_once = True
        self.file_badge.setText(os.path.basename(path))
        self.file_badge.show()
        self.add_row_btn.setEnabled(True)
        self.clear_file_btn.setEnabled(True)

        try:
            save_last_file(self.state.config_path, path)
        except Exception as e:
            print(f"[WARN] บันทึกไฟล์ล่าสุดไม่สำเร็จ: {e}", flush=True)

        rows = [build_row_state(r) for r in server_rows]
        self._apply_default_activities(rows)
        self.summary_dock.hide_banner()
        # ไฟล์ใหม่ ผลเช็คของไฟล์เก่าใช้ไม่ได้แล้ว — ต้องล้างก่อน set_rows ไม่งั้นแถวใหม่จะถูกย้อม
        # ด้วยข้อมูลของไฟล์ก่อนหน้า
        self.table_controller.clear_check_result()
        self.summary_dock.set_logged_by_date({})
        self.table_controller.set_rows(rows, picker_ctx=self._build_picker_context())
        self._recompute_filters_and_gating()
        self._ensure_activities_loaded()

    def _build_picker_context(self) -> PickerContext:
        client = self.state.client
        return PickerContext(
            search_work_packages_page=lambda q, pid, page: client.search_work_packages_page(
                q, project_id=pid, offset=page
            ),
            get_projects=client.get_projects,
            get_types=lambda pid: client.get_types(project_id=pid),
            shared=self._picker_shared,
        )

    def seed_projects(self, projects: list) -> None:
        """หน้า login ยิง get_projects เป็นตัวตรวจ credential อยู่แล้ว — เอาผลมาใช้ต่อแทนที่จะทิ้ง
        เปิดไฟล์ครั้งแรกจะได้ไม่ต้องยิงซ้ำ"""
        if projects:
            self._picker_shared.set_projects(projects)
            # แท็บ Git สร้าง combo โปรเจกต์ไว้ตั้งแต่ตอน cache ยังว่าง ต้องเติมย้อนหลังให้
            self.worklog_tab.refresh_projects()

    def _on_load_error(self, message: str) -> None:
        if self._loaded_once:
            QMessageBox.warning(self, "Timesheet Sync", f"เปิดไฟล์ไม่สำเร็จ: {message}")
        else:
            self.table_controller.show_empty_message(f"เปิดไฟล์ไม่สำเร็จ: {message}")

    def _set_filter(self, mode: str) -> None:
        self.filter_all_btn.setProperty("active", mode == "all")
        self.filter_unresolved_btn.setProperty("active", mode == "unresolved")
        for btn in (self.filter_all_btn, self.filter_unresolved_btn):
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self.table_controller.apply_filter(mode)
        self._update_search_count()

    def _on_select_all_clicked(self) -> None:
        # select_all() เรียก on_selection_changed (= _recompute_filters_and_gating) ให้เองแล้ว
        self.table_controller.select_all()

    def _on_select_none_clicked(self) -> None:
        self.table_controller.select_none()

    def _clear_search(self) -> None:
        self._search_timer.stop()
        self.search_edit.blockSignals(True)
        self.search_edit.clear()
        self.search_edit.blockSignals(False)
        self.table_controller.filter_query = ""
        self.search_count_label.setText("")

    def _apply_search(self) -> None:
        self.table_controller.apply_filter(
            self.table_controller.filter_mode, self.search_edit.text()
        )
        self._update_search_count()

    def _update_search_count(self) -> None:
        # ปุ่ม "ทั้งหมด/ต้องเลือก" ยังนับจากทั้งไฟล์เหมือนเดิม ป้ายนี้บอกผลของคำค้นอย่างเดียว
        if self.table_controller.filter_query:
            self.search_count_label.setText(f"ค้นเจอ {self.table_controller.visible_count()} แถว")
        else:
            self.search_count_label.setText("")

    def _recompute_filters_and_gating(self) -> None:
        rows = self.table_controller.rows
        total, unresolved = self.table_controller.counts()
        self.filter_all_btn.setText(f"ทั้งหมด ({total})")
        self.filter_unresolved_btn.setText(f"ต้องเลือก ({unresolved})")
        # ไม่ส่ง query มาด้วย = ใช้คำค้นเดิมที่ controller เก็บไว้ (กันคำค้นหายตอนแก้แถว)
        self.table_controller.apply_filter(self.table_controller.filter_mode)
        self._update_search_count()
        self.summary_dock.update_summary(rows)
        self.summary_dock.update_gating(rows)
        self.check_btn.setEnabled(bool(self.state.client and rows) and not self._checking)

    def _on_row_edited(self, row: RowState) -> None:
        # date/hours edits never change resolution state — only the summary needs to react
        # (matches static/app.js's onTbodyInput, which calls renderSummary() only here).
        self.summary_dock.update_summary(self.table_controller.rows)

    def _on_wp_row_changed(self, index: int) -> None:
        # WP selection/mode/subject changes affect resolution state -> full recompute
        # (matches static/app.js's selectExistingWp/setMode, which touch badge+summary+filter+gating).
        # หน่วงรวบไว้เพราะทางนี้โดนยิงทุกตัวอักษรที่พิมพ์ในช่องชื่องานใหม่ ส่วน badge ของแถวนั้น
        # TableController อัปเดตให้ทันทีอยู่แล้ว ไม่ได้รอรอบนี้
        self._schedule_recompute()

    # -- เช็คของที่มีอยู่แล้วบน OpenProject ---------------------------------

    def _set_check_running(self, running: bool) -> None:
        set_button_busy(self.check_btn, running, "กำลังเช็ค...", "เช็คบน OpenProject")
        self.check_progress.setVisible(running)
        if not running:
            # set_button_busy เปิดปุ่มคืนให้เสมอ แต่ปุ่มนี้ยังต้องมีแถวและต้อง login ก่อนถึงกดได้
            self._recompute_filters_and_gating()

    def _start_check(self) -> None:
        """ถาม OpenProject ว่างานในตารางมีอยู่แล้วไหม และวันนั้นลงชั่วโมงไปแล้วหรือยัง

        เช็คทุกแถว ไม่ใช่แค่แถวที่ติ๊กไว้ — คนยังเปลี่ยนใจติ๊กเพิ่มทีหลังได้ และผลเช็คไม่มีวันหมดอายุ
        เร็วขนาดนั้น (ต่างจากตอน submit ที่สนใจเฉพาะแถวที่กำลังจะส่งจริง)

        ทั้งหมดเป็น GET — check_existing_work จับ error เองครบและไม่ raise ดู common.py
        """
        client = self.state.client
        rows = self.table_controller.rows
        if self._checking or client is None or not rows:
            return

        # (project_id, ชื่องาน) ที่ไม่ซ้ำกัน — ยิง 1 คำขอต่อคู่ เหมือนที่ path ของ submit ยุบก่อนส่ง
        pairs = sorted(
            {
                (r.new_project_id, r.new_subject.strip())
                for r in rows
                if r.mode == "new" and r.new_project_id and (r.new_subject or "").strip()
            },
            key=lambda p: str(p[1]),
        )
        dates = sorted({r.date for r in rows if r.date})
        # ไม่มีวันที่เลย = ขอแค่ชื่องานซ้ำ (check_existing_work คืนเฉพาะส่วนนั้นให้เอง)
        date_from, date_to = (dates[0], dates[-1]) if dates else ("", "")

        self._checking = True
        self._set_check_running(True)
        run_async(
            check_existing_work,
            client,
            pairs,
            date_from,
            date_to,
            on_success=self._on_check_done,
            on_error=self._on_check_error,
        )

    def _on_check_done(self, result: dict) -> None:
        self._checking = False
        self.table_controller.set_check_result(
            result.get("duplicates") or {}, result.get("logged_wp_dates") or {}
        )
        self.summary_dock.set_logged_by_date(result.get("logged_by_date") or {})
        flagged = self.table_controller.warning_count()
        errors = result.get("errors") or []
        note = f"เจอที่ต้องดู {flagged} แถว" if flagged else "ไม่เจออะไรที่ต้องดู"
        self.summary_dock.set_check_note(note + (" · เช็คได้ไม่ครบ" if errors else ""))
        # ทางนี้เรียก _recompute_filters_and_gating ให้ ซึ่งเป็นตัวที่วาดโน้ตข้างบนลง missing_label
        self._set_check_running(False)

        if errors:
            # ผู้ใช้กดปุ่มนี้เอง จึงควรรู้ว่าเช็คได้ไม่ครบ (ต่างจากรอบ auto ในแท็บสรุปงานที่เงียบไว้)
            QMessageBox.warning(self, "Timesheet Sync", "เช็คได้ไม่ครบ:\n\n" + "\n".join(errors))

    def _on_check_error(self, message: str) -> None:
        """check_existing_work จับ error เองหมดแล้ว ทางนี้เหลือแค่กรณีที่ worker พังจริงๆ"""
        self._checking = False
        self._set_check_running(False)
        QMessageBox.warning(self, "Timesheet Sync", f"เช็คกับ OpenProject ไม่สำเร็จ: {message}")

    def _on_submit_clicked(self) -> None:
        if not self.state.file_path or not self.state.client:
            return

        rows = [r for r in self.table_controller.rows if r.selected and r.is_resolved()]
        if not rows:
            return  # ปุ่มควร disabled อยู่แล้วถ้าไม่มีแถวที่เลือก แต่กันไว้

        if not self._confirm_duplicates_in_batch(rows):
            return

        new_rows = [r for r in rows if r.mode == "new" and (r.new_subject or "").strip()]
        if self.dup_check.isChecked() and new_rows:
            # (project_id, subject) ที่ไม่ซ้ำกัน — ยิง 1 คำขอต่อคู่ จึงต้องยุบก่อนเสมอ
            pairs = sorted({(r.new_project_id, r.new_subject.strip()) for r in new_rows},
                           key=lambda p: str(p[1]))
            self.summary_dock.set_submit_busy(True, f"กำลังตรวจชื่องานซ้ำ ({len(pairs)})...")
            run_async(
                find_duplicate_subjects,
                self.state.client,
                pairs,
                on_success=lambda found, rs=rows: self._on_dup_check_done(found, rs),
                on_error=lambda msg, rs=rows: self._on_dup_check_error(msg, rs),
            )
            return

        self._start_submit(rows)

    def _confirm_duplicates_in_batch(self, rows: list[RowState]) -> bool:
        """ชื่องานใหม่ที่ซ้ำกันเองภายในไฟล์เดียว — ตรวจได้ฟรี ไม่ต้องยิง API"""
        seen: dict[str, int] = {}
        for row in rows:
            if row.mode != "new":
                continue
            name = (row.new_subject or "").strip().lower()
            if name:
                seen[name] = seen.get(name, 0) + 1
        dupes = {name: n for name, n in seen.items() if n > 1}
        if not dupes:
            return True

        detail = "\n".join(f"“{name}” × {n} แถว" for name, n in list(dupes.items())[:MAX_PREVIEW_LINES])
        if len(dupes) > MAX_PREVIEW_LINES:
            detail += f"\n... และอีก {len(dupes) - MAX_PREVIEW_LINES} ชื่อ"
        answer = QMessageBox.question(
            self,
            "Timesheet Sync",
            f"มีชื่องานใหม่ซ้ำกันเองในไฟล์นี้ {len(dupes)} ชื่อ — แต่ละแถวจะสร้าง Work Package "
            f"แยกกันคนละอัน\n\n{detail}\n\nส่งต่อไปเลยหรือไม่?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def _on_dup_check_done(self, found: dict, rows: list[RowState]) -> None:
        if found:
            lines = []
            for name, matches in list(found.items())[:MAX_PREVIEW_LINES]:
                wp = matches[0]
                extra = f" (+{len(matches) - 1})" if len(matches) > 1 else ""
                lines.append(f"“{name}” ซ้ำกับ #{wp['id']} [{wp.get('project') or '-'}]{extra}")
            detail = "\n".join(lines)
            if len(found) > MAX_PREVIEW_LINES:
                detail += f"\n... และอีก {len(found) - MAX_PREVIEW_LINES} ชื่อ"

            answer = QMessageBox.question(
                self,
                "Timesheet Sync",
                f"มี {len(found)} ชื่องานที่ตรงกับ Work Package ที่มีอยู่แล้วบน OpenProject\n"
                "ถ้าส่งต่อ ระบบจะสร้างงานใหม่ซ้ำอีกอัน\n\n"
                f"{detail}\n\nส่งต่อไปเลยหรือไม่? (เลือก No เพื่อกลับไปสลับแถวเป็น “เลือกงานเดิม”)",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                self.summary_dock.set_submit_busy(False)
                return

        self._start_submit(rows)

    def _on_dup_check_error(self, message: str, rows: list[RowState]) -> None:
        # ตรวจซ้ำเป็นแค่ตัวช่วย ไม่ใช่ตัวบล็อก — ถามก่อนว่าจะส่งต่อทั้งที่ตรวจไม่สำเร็จไหม
        answer = QMessageBox.question(
            self,
            "Timesheet Sync",
            f"ตรวจชื่องานซ้ำไม่สำเร็จ: {message}\n\nส่งต่อโดยไม่ตรวจหรือไม่?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            self.summary_dock.set_submit_busy(False)
            return
        self._start_submit(rows)

    def _start_submit(self, rows: list[RowState]) -> None:
        self.summary_dock.set_submit_busy(True)
        self.summary_dock.set_submit_progress(0, len(rows))

        run_async(
            submit_rows,
            self.state.client,
            self.state.config,
            self.state.file_path,
            rows,
            on_success=self._on_submit_success,
            on_error=self._on_submit_error,
            on_progress=self.summary_dock.set_submit_progress,
        )

    def _on_submit_success(self, result: dict) -> None:
        self.summary_dock.set_submit_busy(False)
        # ยังกดไม่ได้จนกว่าจะโหลดแถวใหม่เสร็จแล้ว update_gating คิดใหม่ — กันกดส่งซ้ำด้วยข้อมูล
        # ชุดเก่าระหว่างรอ reload
        self.summary_dock.submit_btn.setEnabled(False)
        self.summary_dock.show_banner(result["success"], result["failed"])
        self._reload_rows_after_submit()

    def _on_submit_error(self, message: str) -> None:
        self.summary_dock.set_submit_busy(False)
        QMessageBox.warning(self, "Timesheet Sync", f"ส่งข้อมูลไม่สำเร็จ: {message}")

    def _reload_rows_after_submit(self) -> None:
        # ต่างจาก _load_file: ไม่แตะ banner/ป้ายชื่อไฟล์/save_last_file — โหลดแถวใหม่จากไฟล์เดิมเฉยๆ
        # (มาแทนที่ reloadState() ใน app.js — synced rows หายไปจาก get_unsynced_rows โดยอัตโนมัติ)
        run_async(
            load_rows_from_excel,
            self.state.client,
            self.state.file_path,
            self.state.config["excel"],
            on_success=self._on_reload_rows_success,
            on_error=self._on_reload_rows_error,
        )

    def _on_reload_rows_success(self, server_rows: list) -> None:
        rows = [build_row_state(r) for r in server_rows]
        self._apply_default_activities(rows)
        self.table_controller.set_rows(rows, picker_ctx=self._build_picker_context())
        self._recompute_filters_and_gating()

    def _on_reload_rows_error(self, message: str) -> None:
        QMessageBox.warning(self, "Timesheet Sync", f"โหลดข้อมูลใหม่ไม่สำเร็จ: {message}")

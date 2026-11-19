"""แท็บ "สรุปงาน" — ยุบสาย ต้นทาง -> แตก task -> ตารางหลัก ให้จบในหน้าเดียว

เดิมงานนี้กระจายอยู่ 3 ที่: session Claude ตัวหนึ่งอ่าน git เขียน .md, อีก session รัน
/timesheet แปลง .md เป็น .xlsx, แล้วค่อยมากด "อัปโหลดไฟล์ใหม่" ในแอปหาไฟล์นั้นอีกรอบ
แท็บนี้ทำทั้งสามอย่างจบในหน้าเดียว

ต้นทางสลับได้ 2 แบบผ่าน QStackedWidget: "git" อ่าน commit เอง (ไม่มี file dialog สักอัน) และ
"md" รับสรุปที่เขียนไว้แล้ว — ต่างกันแค่ที่มาของข้อมูลกับวิธีได้ชื่อโปรเจกต์ ตั้งแต่จุดที่เรียก
Claude เป็นต้นไปใช้สายเดียวกันทั้งหมด (ดู _prompt_for_source / _source_ready)

การเรียก Claude ใช้ QProcess ไม่ใช่ gui.threads.run_async เพราะรอ 30 วิ - 2 นาที และต้อง
ยกเลิกกลางคันได้ (run_async ฆ่างานที่วิ่งอยู่ไม่ได้ QThreadPool ไม่มี API สำหรับ preempt)
ส่วนการอ่าน git เร็วพอจะใช้ run_async ตามปกติ

ข้อมูลโปรเจกต์มาจาก PickerSharedState.projects_cache ที่หน้า login ดึงมาแล้ว แท็บนี้ยิง
OpenProject เองแค่ตอนเช็คว่างานที่จะสร้างมีอยู่แล้วหรือยัง (_start_check) ซึ่งเป็น GET ล้วน
ไม่สร้างและไม่แก้อะไรบนเซิร์ฟเวอร์ — ของจริงเกิดตอนกดยืนยันในตารางหลักเหมือนเดิม
"""
from __future__ import annotations

import os

from PySide6.QtCore import QDate, QProcess, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

import claude_tasks
import gitlog
from common import check_existing_work, generate_task_template, save_worklog_config
from gui.busy import make_busy_bar, set_button_busy
from gui.state import AppState
from gui.theme import COLORS, FONT_MONO, fit_combo_popup, tune_calendar_popup
from gui.threads import run_async

DATE_FORMAT = "yyyy-MM-dd"
RESULT_COLUMN_LABELS = ["ลบ", "วันที่", "ชม.", "ชื่องาน", "หมายเหตุ", "โปรเจกต์", "บน OpenProject"]
WP_CREATE_NEW = "สร้างใหม่"
LOGGED_DATES_SHOWN = 3  # จำนวนวันที่โชว์ในบรรทัดเตือนชั่วโมงซ้ำ ที่เหลือยุบเป็น "+อีก N วัน"
MAX_LOGGED_PREVIEW = 8  # จำนวนแถวที่โชว์ในกล่องยืนยันตอนกดส่ง
REPO_COLUMN_LABELS = ["ใช้", "repo", "path", "โปรเจกต์ OpenProject", "ลบ"]
REPO_ROW_HEIGHT = 34
# ความสูงแถวตารางผลลัพธ์ — ค่า default ของ Qt (30px) ตัดหางตัวอักษรที่อยู่ใต้เส้นบรรทัด
# ชื่อโปรเจกต์อย่าง "SDB_General" จึงแสดงเป็น "SDB General" ขีดล่างหายไปเฉยๆ
RESULT_ROW_HEIGHT = 34
PROJECT_PLACEHOLDER = "-- เลือกโปรเจกต์ --"
# claude แตก task ช้าได้ถึงหลักนาทีเมื่อ commit เยอะ แต่เกินนี้ถือว่าค้างแล้ว
CLAUDE_TIMEOUT_MS = 10 * 60 * 1000
HOURS_EPSILON = 0.01
MD_NO_FILE = "ยังไม่ได้เลือกไฟล์ — พิมพ์หรือวางสรุปในกล่องด้านล่างก็ได้"
MD_PATH_MAX_CHARS = 70  # path ยาวกว่านี้ตัดหัวทิ้ง เหลือท้ายที่บอกชื่อไฟล์
CLAUDE_MISSING_HINT = (
    "ไม่พบคำสั่ง claude บนเครื่อง — ติดตั้ง Claude Code หรือระบุ worklog.claude_path ใน config.yaml"
)


class WorklogTab(QWidget):
    """ต้องการ MainWindow เป็นปลายทางของปุ่ม "ส่งเข้าตารางหลัก" จึงส่ง callback เข้ามาแทนการ
    อ้างถึง MainWindow ตรงๆ (กัน import วน gui.main_window <-> gui.worklog_tab)"""

    file_ready = Signal(str)  # path ของ .xlsx ที่เพิ่งเขียน — MainWindow โหลดเข้าตารางหลักต่อ

    def __init__(self, state: AppState, picker_shared, parent=None):
        super().__init__(parent)
        self.state = state
        self._picker_shared = picker_shared
        self._repo_rows: list[dict] = []
        self._result_rows: list[dict] = []
        self._source = "git"       # "git" = อ่าน commit เอง · "md" = สรุปที่เขียนไว้แล้ว
        self._commits: dict | None = None
        self._summary_path = ""    # ไฟล์ .md ที่อ่านมาล่าสุด — ว่างได้ถ้าผู้ใช้พิมพ์/วางเอง
        self._md_ready = False     # กันไม่ให้ทุกตัวอักษรที่พิมพ์ไปคิดสถานะปุ่มใหม่ทั้งชุด
        self._summary_md = ""
        # ผลเช็คกับ OpenProject — ล้างทุกครั้งที่ได้ task ชุดใหม่และตอน logout
        self._duplicates: dict = {}        # ชื่องาน -> [wp ที่ชื่อตรงเป๊ะ]
        self._logged_by_date: dict = {}    # วันที่ -> {"hours", "count"} ที่ลงไปแล้ว
        self._logged_wp_dates: dict = {}   # (wp_id, วันที่) -> ชั่วโมงที่ลงไปแล้ว
        self._checking = False
        self._process: QProcess | None = None
        self._elapsed = 0
        self._stdout_buffer = ""
        self._stderr_buffer = ""
        self._stream_tail = ""     # เศษบรรทัดสุดท้ายของ stream-json ที่ยังมาไม่ครบ
        self._stream_note = ""     # ความคืบหน้าล่าสุดที่แกะได้จาก stream
        self._thinking_tokens = 0  # ยอดสะสม ไม่ใช่ค่าดิบจาก CLI ที่นับใหม่ทุก block

        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._on_tick)

        self._build_ui()
        self._load_from_config()

    # -- UI ---------------------------------------------------------------

    def _field_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    def _make_date_edit(self) -> QDateEdit:
        edit = QDateEdit()
        edit.setCalendarPopup(True)
        tune_calendar_popup(edit)
        edit.setDisplayFormat(DATE_FORMAT)
        edit.setDate(QDate.currentDate())
        edit.setMinimumWidth(140)  # 130 คือขั้นต่ำที่วันที่ครบพอดี เผื่อไว้อีก 10px
        return edit

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        layout.addLayout(self._build_range_bar())
        layout.addLayout(self._build_source_bar())
        layout.addWidget(self._build_source_stack())
        layout.addLayout(self._build_action_bar())
        layout.addWidget(self._build_result_table(), stretch=1)
        layout.addLayout(self._build_footer())

        # ต้องมาหลังสร้างครบทุกชิ้น — _set_source แตะทั้งปุ่มใน action bar และปุ่มใน footer
        self._set_source("git")

    def _build_range_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.addWidget(self._field_label("ช่วงวันที่"))

        # ปฏิทิน + ปุ่มขึ้นลงกินที่ทางขวาไปเกือบ 40px ถ้าไม่กันความกว้างไว้ "2026-07-03" จะโดนตัด
        self.date_from = self._make_date_edit()
        bar.addWidget(self.date_from)

        bar.addWidget(self._field_label("ถึง"))

        self.date_to = self._make_date_edit()
        bar.addWidget(self.date_to)

        for label, preset in (("วันนี้", "today"), ("เมื่อวาน", "yesterday"),
                              ("สัปดาห์นี้", "week"), ("เดือนนี้", "month")):
            btn = QPushButton(label)
            btn.setObjectName("outlineButton")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, p=preset: self._apply_preset(p))
            bar.addWidget(btn)

        bar.addStretch()
        bar.addWidget(self._field_label("ชม./วัน"))
        self.hours_per_day = QDoubleSpinBox()
        self.hours_per_day.setDecimals(1)
        self.hours_per_day.setSingleStep(0.5)
        self.hours_per_day.setRange(0.5, 24.0)
        self.hours_per_day.setValue(8.0)
        # 70 แคบไปจนเลข "8.0" ถูกตัดท้ายเหลือ "8." (padding 10px สองข้าง + ปุ่มลูกศร 16px
        # กินไปก่อนแล้ว) — เห็นได้จากภาพที่เรนเดอร์จริงเท่านั้น การวัด sub-control rect หลอก
        self.hours_per_day.setFixedWidth(88)
        bar.addWidget(self.hours_per_day)

        bar.addWidget(self._field_label("โมเดล"))
        self.model_combo = QComboBox()
        self.model_combo.setCursor(Qt.PointingHandCursor)
        self.model_combo.setMinimumWidth(230)
        for model_id, label in claude_tasks.MODEL_CHOICES:
            self.model_combo.addItem(label, model_id)
        self.model_combo.setToolTip(
            "โมเดลที่ใช้แตก task — วัดกับ commit ตัวอย่าง 1 วัน: Sonnet 5 ~20 วิ · Opus 5 ~23 วิ · "
            "Haiku 4.5 ~46 วิ และแตกได้หยาบกว่า"
        )
        self.model_combo.currentIndexChanged.connect(lambda _i: self._save_to_config())
        bar.addWidget(self.model_combo)
        return bar

    def _build_repo_header(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.addWidget(self._field_label("repo ที่จะอ่าน"))
        bar.addStretch()

        add_btn = QPushButton("+ เพิ่ม repo")
        add_btn.setObjectName("outlineButton")
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self._on_add_repo_clicked)
        bar.addWidget(add_btn)
        return bar

    def _build_repo_table(self) -> QWidget:
        self.repo_table = QTableWidget()
        self.repo_table.setColumnCount(len(REPO_COLUMN_LABELS))
        self.repo_table.setHorizontalHeaderLabels(REPO_COLUMN_LABELS)
        self.repo_table.verticalHeader().setVisible(False)
        self.repo_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.repo_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.repo_table.setShowGrid(False)
        header = self.repo_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.repo_table.setColumnWidth(0, 40)
        self.repo_table.setColumnWidth(1, 160)
        self.repo_table.setColumnWidth(3, 240)
        self.repo_table.setColumnWidth(4, 40)
        self._fit_repo_table()
        return self.repo_table

    def _fit_repo_table(self) -> None:
        """สูงพอดีจำนวนแถว ไม่ทิ้งพื้นที่ว่างไว้เฉยๆ แต่ไม่โตจนเบียดตารางผลลัพธ์"""
        header_height = self.repo_table.horizontalHeader().height()
        rows = max(1, len(self._repo_rows))
        self.repo_table.setFixedHeight(min(150, header_height + rows * REPO_ROW_HEIGHT + 4))

    def _build_action_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()

        self.read_git_btn = QPushButton("อ่าน git")
        self.read_git_btn.setObjectName("outlineButton")
        self.read_git_btn.setCursor(Qt.PointingHandCursor)
        self.read_git_btn.clicked.connect(self._on_read_git_clicked)
        bar.addWidget(self.read_git_btn)

        self.git_progress = make_busy_bar(90)
        bar.addWidget(self.git_progress)

        self.claude_btn = QPushButton("ให้ Claude แตกเป็น task")
        self.claude_btn.setObjectName("outlineButton")
        self.claude_btn.setCursor(Qt.PointingHandCursor)
        self.claude_btn.setEnabled(False)
        self.claude_btn.clicked.connect(self._on_claude_clicked)
        bar.addWidget(self.claude_btn)

        self.check_btn = QPushButton("เช็คบน OpenProject")
        self.check_btn.setObjectName("outlineButton")
        self.check_btn.setCursor(Qt.PointingHandCursor)
        self.check_btn.setEnabled(False)
        self.check_btn.setToolTip("ดูว่างานพวกนี้มีอยู่บน OpenProject แล้วหรือยัง และวันนั้นลงชั่วโมงไปหรือยัง")
        self.check_btn.clicked.connect(lambda: self._start_check(auto=False))
        bar.addWidget(self.check_btn)

        self.check_progress = make_busy_bar(90)
        bar.addWidget(self.check_progress)

        self.cancel_btn = QPushButton("ยกเลิก")
        self.cancel_btn.setObjectName("mutedButton")
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.hide()
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        bar.addWidget(self.cancel_btn)

        # claude ไม่บอกความคืบหน้าระหว่างทาง มีแค่ "ยังวิ่งอยู่นะ" ให้บอก จึงเป็นหลอดวิ่ง ไม่ใช่ %
        self.claude_progress = make_busy_bar()
        bar.addWidget(self.claude_progress)

        bar.addStretch()
        self.status_label = QLabel()
        self.status_label.setObjectName("fieldLabel")
        bar.addWidget(self.status_label)
        return bar

    def _build_result_table(self) -> QWidget:
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(len(RESULT_COLUMN_LABELS))
        self.result_table.setHorizontalHeaderLabels(RESULT_COLUMN_LABELS)
        self.result_table.verticalHeader().setVisible(False)
        self.result_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.result_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.result_table.setShowGrid(False)
        header = self.result_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        self.result_table.setColumnWidth(0, 40)
        # วันที่/ชั่วโมงต้องกว้างพอให้ปฏิทินกับปุ่มขึ้นลงไม่ทับตัวเลข
        self.result_table.setColumnWidth(1, 140)
        self.result_table.setColumnWidth(2, 90)
        # บีบชื่องาน/โปรเจกต์ลงจากของเดิมเพื่อแบ่งที่ให้คอลัมน์ "บน OpenProject" โดยไม่กิน
        # คอลัมน์หมายเหตุ (4) ซึ่งเป็นตัวยืดและมีรายละเอียดทางเทคนิคที่ต้องอ่านจริง
        self.result_table.setColumnWidth(3, 260)
        # 150 ไม่ใช่ 140 — ที่ 140 ข้อความ "-- เลือกโปรเจกต์ --" ล้นกรอบ 5px แล้วโดนตัดท้าย
        self.result_table.setColumnWidth(5, 150)
        self.result_table.setColumnWidth(6, 165)
        return self.result_table

    def _build_footer(self) -> QVBoxLayout:
        """สองบรรทัด: ยอดชั่วโมง+ปุ่ม กับบรรทัดเตือนชั่วโมงที่ลงไปแล้ว (แยก label กันเพราะ
        totals_label ถูกเขียนทับทุกครั้งที่แก้แถว ส่วนบรรทัดเตือนมาจากผลเช็คคนละชุด)"""
        outer = QVBoxLayout()
        outer.setSpacing(4)

        self.logged_label = QLabel()
        self.logged_label.setObjectName("fieldLabel")
        self.logged_label.setStyleSheet(f"color: {COLORS['warn']};")
        outer.addWidget(self.logged_label)

        bar = QHBoxLayout()

        self.totals_label = QLabel()
        self.totals_label.setObjectName("fieldLabel")
        bar.addWidget(self.totals_label)

        bar.addStretch()

        self.add_row_btn = QPushButton("+ เพิ่มแถว")
        self.add_row_btn.setObjectName("outlineButton")
        self.add_row_btn.setCursor(Qt.PointingHandCursor)
        self.add_row_btn.setEnabled(False)
        self.add_row_btn.clicked.connect(lambda: self._add_result_row({}))
        bar.addWidget(self.add_row_btn)

        self.save_md_btn = QPushButton("บันทึกสรุป .md")
        self.save_md_btn.setObjectName("outlineButton")
        self.save_md_btn.setCursor(Qt.PointingHandCursor)
        self.save_md_btn.setEnabled(False)
        self.save_md_btn.clicked.connect(self._on_save_md_clicked)
        bar.addWidget(self.save_md_btn)

        self.send_btn = QPushButton("ส่งเข้าตารางหลัก →")
        self.send_btn.setObjectName("primaryButton")
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setEnabled(False)
        self.send_btn.clicked.connect(self._on_send_clicked)
        bar.addWidget(self.send_btn)

        outer.addLayout(bar)
        return outer

    # -- แหล่งข้อมูล -------------------------------------------------------

    def _build_source_bar(self) -> QHBoxLayout:
        """ปุ่มสลับแหล่งข้อมูล — สไตล์ wpTab ชุดเดียวกับตัวสลับ existing/new ของตัวเลือก Work Package"""
        bar = QHBoxLayout()
        bar.addWidget(self._field_label("แหล่งข้อมูล"))

        self.git_source_btn = QPushButton("จาก git")
        self.md_source_btn = QPushButton("จากไฟล์ .md")
        for btn, mode in ((self.git_source_btn, "git"), (self.md_source_btn, "md")):
            btn.setObjectName("wpTab")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, m=mode: self._set_source(m))
            bar.addWidget(btn)

        bar.addStretch()
        return bar

    def _build_source_stack(self) -> QWidget:
        self.source_stack = QStackedWidget()
        self.source_stack.addWidget(self._build_git_panel())
        self.source_stack.addWidget(self._build_md_panel())
        return self.source_stack

    def _build_git_panel(self) -> QWidget:
        """หัวข้อ + ตาราง repo เดิม ห่อเป็น widget เดียวเพื่อใส่ลง stack ได้ — เนื้อในไม่เปลี่ยน"""
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(8)
        panel_layout.addLayout(self._build_repo_header())
        panel_layout.addWidget(self._build_repo_table())
        return panel

    def _build_md_panel(self) -> QWidget:
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(8)

        top = QHBoxLayout()
        pick_btn = QPushButton("เลือกไฟล์ .md")
        pick_btn.setObjectName("outlineButton")
        pick_btn.setCursor(Qt.PointingHandCursor)
        pick_btn.clicked.connect(self._on_pick_md_clicked)
        top.addWidget(pick_btn)

        self.md_file_label = QLabel(MD_NO_FILE)
        self.md_file_label.setStyleSheet(f"color: {COLORS['text_muted']}; font-family: {FONT_MONO};")
        self.md_file_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addWidget(self.md_file_label, stretch=1)

        top.addWidget(self._field_label("โปรเจกต์"))
        self.md_project = QComboBox()
        self.md_project.setCursor(Qt.PointingHandCursor)
        self.md_project.setMinimumWidth(220)
        self._fill_project_combo(self.md_project, None, "")
        top.addWidget(self.md_project)
        panel_layout.addLayout(top)

        self.summary_edit = QPlainTextEdit()
        self.summary_edit.setPlaceholderText(
            'วางหรือพิมพ์สรุปงานตรงนี้ได้เลย หรือกด "เลือกไฟล์ .md" เพื่ออ่านจากไฟล์'
        )
        self.summary_edit.setFont(QFont(FONT_MONO, 10))
        # สูงพอเห็นหัวเรื่องกับย่อหน้าแรก แต่ไม่เบียดตารางผลลัพธ์ซึ่งเป็นของที่ต้องนั่งตรวจจริง
        self.summary_edit.setMinimumHeight(110)
        self.summary_edit.setMaximumHeight(190)
        self.summary_edit.textChanged.connect(self._on_summary_changed)
        panel_layout.addWidget(self.summary_edit)
        return panel

    def _fit_stack_to_current(self) -> None:
        """QStackedWidget สูงตามหน้าที่สูงที่สุดเสมอ — ปล่อยไว้ตาราง repo จะโดนกล่องข้อความของหน้า
        .md ยืดให้สูงตาม ทั้งที่ไม่ได้แสดงอยู่ ให้หน้าที่ซ่อนคืนพื้นที่ด้วย QSizePolicy.Ignored"""
        current = self.source_stack.currentIndex()
        for i in range(self.source_stack.count()):
            page = self.source_stack.widget(i)
            policy = page.sizePolicy()
            policy.setVerticalPolicy(QSizePolicy.Preferred if i == current else QSizePolicy.Ignored)
            page.setSizePolicy(policy)
        self.source_stack.adjustSize()

    def _set_source(self, mode: str) -> None:
        self._source = mode
        self.git_source_btn.setProperty("active", mode == "git")
        self.md_source_btn.setProperty("active", mode == "md")
        for btn in (self.git_source_btn, self.md_source_btn):
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self.source_stack.setCurrentIndex(0 if mode == "git" else 1)
        self._fit_stack_to_current()
        # โหมด .md ไม่มีอะไรให้อ่านจาก git — ซ่อนปุ่มไปเลย ไม่ใช่แค่ปิดการกดให้เป็นปุ่มเทาค้างไว้
        self.read_git_btn.setVisible(mode == "git")
        if mode == "md":
            self.git_progress.hide()
        self._refresh_claude_enabled()
        self._recompute_totals()

    def _summary_text(self) -> str:
        return self.summary_edit.toPlainText().strip()

    def _source_ready(self) -> bool:
        """มีข้อมูลต้นทางพอจะให้ Claude แตก task แล้วหรือยัง"""
        if self._source == "md":
            return bool(self._summary_text())
        return bool((self._commits or {}).get("total"))

    def _refresh_claude_enabled(self) -> None:
        """เงื่อนไขปุ่มแตก task ใช้ร่วมกันทั้งสองโหมด: มีต้นทาง + มี claude + ไม่มีงานวิ่งอยู่"""
        has_claude = bool(self._claude_path())
        self.claude_btn.setEnabled(self._source_ready() and has_claude and self._process is None)
        self.claude_btn.setToolTip("" if has_claude else CLAUDE_MISSING_HINT)

    def _set_md_file_label(self, path: str) -> None:
        """โชว์แค่ท้าย path ถ้ายาว — QLabel ไม่ตัดข้อความให้เอง ปล่อยไว้จะดันหน้าต่างกว้างตาม path"""
        if not path:
            self.md_file_label.setText(MD_NO_FILE)
            self.md_file_label.setToolTip("")
            return
        shown = path if len(path) <= MD_PATH_MAX_CHARS else "..." + path[-MD_PATH_MAX_CHARS:]
        self.md_file_label.setText(shown)
        self.md_file_label.setToolTip(path)

    def _md_dialog_dir(self) -> str:
        """โฟลเดอร์ตั้งต้นของ file dialog — ไฟล์ที่เลือกล่าสุด (จำข้ามการเปิดแอปผ่าน config)
        แล้วค่อยตกไปที่โฟลเดอร์ปลายทางของไฟล์ที่แท็บนี้เขียน"""
        last = (self._summary_path or self._worklog_cfg().get("last_summary_file") or "").strip()
        directory = os.path.dirname(last)
        return directory if directory and os.path.isdir(directory) else self._output_dir()

    def _on_pick_md_clicked(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "เลือกไฟล์สรุปงาน", self._md_dialog_dir(), "Markdown (*.md *.markdown);;ทุกไฟล์ (*)"
        )
        if not path:
            return
        try:
            # errors="replace" ไม่ใช่ strict — ไฟล์ที่ผู้ใช้เขียนเองอาจไม่ใช่ utf-8 ล้วน ยอมให้เพี้ยน
            # บางตัวดีกว่าปฏิเสธทั้งไฟล์ เพราะเนื้อโผล่ในกล่องแล้วแก้เองได้ก่อนส่ง
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as e:
            QMessageBox.warning(self, "Timesheet Sync", f"อ่านไฟล์สรุปไม่สำเร็จ: {e}")
            return
        if not text.strip():
            QMessageBox.information(self, "Timesheet Sync", "ไฟล์นี้ว่าง — ไม่มีเนื้อสรุปให้แตก task")
            return

        self._summary_path = os.path.normpath(path)
        self._set_md_file_label(self._summary_path)
        self.summary_edit.setPlainText(text)  # ยิง textChanged ให้ _on_summary_changed เอง
        self._save_to_config()

    def _on_summary_changed(self) -> None:
        """เช็คเฉพาะตอนความพร้อมพลิก — ไม่งั้นทุกตัวอักษรที่พิมพ์จะไปเรียก shutil.which หา claude"""
        ready = self._source_ready()
        if ready == self._md_ready:
            return
        self._md_ready = ready
        self._refresh_claude_enabled()
        self._recompute_totals()

    # -- config -----------------------------------------------------------

    def _worklog_cfg(self) -> dict:
        return self.state.config.setdefault("worklog", {})

    def _load_from_config(self) -> None:
        cfg = self._worklog_cfg()
        try:
            self.hours_per_day.setValue(float(cfg.get("hours_per_day") or 8))
        except (TypeError, ValueError):
            self.hours_per_day.setValue(8.0)
        # โมเดลที่บันทึกไว้อาจไม่อยู่ในรายการ (ผู้ใช้พิมพ์เองใน config.yaml) — ใส่เป็นตัวเลือก
        # เพิ่มให้ ไม่ใช่เงียบๆ เด้งกลับไปค่าตั้งต้นแล้วเขียนทับค่าที่เขาตั้งมา
        # ว่าง/ไม่ได้ตั้ง = ใช้ค่าตั้งต้น ไม่ใช่ "ปล่อยให้ CLI เลือก" (ดู claude_tasks.resolve_model)
        saved_model = str(cfg.get("claude_model") or "").strip() or claude_tasks.DEFAULT_MODEL
        index = self.model_combo.findData(saved_model)
        if index < 0:
            self.model_combo.addItem(saved_model, saved_model)
            index = self.model_combo.count() - 1
        self.model_combo.blockSignals(True)
        self.model_combo.setCurrentIndex(index)
        self.model_combo.blockSignals(False)

        for repo in cfg.get("repos") or []:
            self._add_repo_row(repo)
        self._recompute_totals()

    def _save_to_config(self) -> None:
        cfg = self._worklog_cfg()
        cfg["hours_per_day"] = self.hours_per_day.value()
        cfg["claude_model"] = self.model_combo.currentData() or ""
        # เขียนเฉพาะตอนมีไฟล์อยู่ในมือ ไม่งั้นการแก้ repo เฉยๆ จะไปล้างไฟล์ล่าสุดที่จำไว้ทิ้ง
        if self._summary_path:
            cfg["last_summary_file"] = self._summary_path
        cfg["repos"] = [
            {
                "path": row["path"],
                "project_id": row["project"].currentData(),
                "project_name": row["project"].currentText() if row["project"].currentData() else "",
                "enabled": row["enabled"].isChecked(),
            }
            for row in self._repo_rows
        ]
        try:
            save_worklog_config(self.state.config_path, cfg)
        except Exception as e:
            # บันทึกไม่ได้ไม่ควรขวางการทำงาน แค่ครั้งหน้าต้องเลือก repo ใหม่
            QMessageBox.warning(self, "Timesheet Sync", f"บันทึกการตั้งค่า repo ไม่สำเร็จ: {e}")

    # -- ตาราง repo --------------------------------------------------------

    def _add_repo_row(self, values: dict) -> None:
        path = (values.get("path") or "").strip()
        if not path or any(r["path"] == path for r in self._repo_rows):
            return

        enabled = QCheckBox()
        enabled.setChecked(bool(values.get("enabled", True)))
        enabled.setCursor(Qt.PointingHandCursor)
        enabled.stateChanged.connect(lambda _s: self._save_to_config())

        project = QComboBox()
        project.setCursor(Qt.PointingHandCursor)
        self._fill_project_combo(project, values.get("project_id"), values.get("project_name") or "")
        project.currentIndexChanged.connect(lambda _i: self._save_to_config())

        remove_btn = QPushButton("✕")
        remove_btn.setObjectName("linkButton")
        remove_btn.setCursor(Qt.PointingHandCursor)

        row = {"path": path, "enabled": enabled, "project": project}
        remove_btn.clicked.connect(lambda _=False, r=row: self._remove_repo_row(r))

        index = len(self._repo_rows)
        self._repo_rows.append(row)
        self.repo_table.setRowCount(len(self._repo_rows))

        name_label = QLabel(gitlog.repo_display_name(path))
        path_label = QLabel(path)
        path_label.setStyleSheet(f"color: {COLORS['text_muted']}; font-family: {FONT_MONO};")
        # path ยาวกว่าคอลัมน์ต้องถูกตัด ไม่ใช่ดันคอลัมน์อื่นจนตารางล้น
        path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        path_label.setToolTip(path)

        self.repo_table.setCellWidget(index, 0, self._padded(enabled))
        self.repo_table.setCellWidget(index, 1, name_label)
        self.repo_table.setCellWidget(index, 2, path_label)
        self.repo_table.setCellWidget(index, 3, project)
        # ปุ่มลบอยู่คอลัมน์สุดท้าย ไม่ใช่ติดกับ checkbox — ของเดิมมันไปชนชื่อ repo จนอ่านเป็น "✕ERP"
        self.repo_table.setCellWidget(index, 4, remove_btn)
        self.repo_table.setRowHeight(index, REPO_ROW_HEIGHT)
        self._fit_repo_table()

    def _padded(self, widget: QWidget) -> QWidget:
        """checkbox ชิดขอบซ้ายเกินไปถ้าใส่ลง cell ตรงๆ"""
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(10, 0, 0, 0)
        layout.addWidget(widget)
        layout.addStretch()
        return wrapper

    def _remove_repo_row(self, row: dict) -> None:
        if row not in self._repo_rows:
            return
        index = self._repo_rows.index(row)
        self._repo_rows.pop(index)
        self.repo_table.removeRow(index)  # removeRow แถวเดียว ให้ Qt เก็บกวาด cell widget เอง
        self._fit_repo_table()
        self._save_to_config()

    def _fill_project_combo(self, combo: QComboBox, project_id, project_name: str) -> None:
        """เติมรายชื่อโปรเจกต์จาก cache ที่หน้า login ดึงมาแล้ว — ไม่ยิง API เพิ่ม

        เก็บเป็น project id เพราะ gui/loader._resolve_new_projects จับคู่ชื่อแบบตรงเป๊ะเท่านั้น
        ชื่อพิมพ์ผิดตัวเดียวคือแอปปล่อยคอลัมน์โปรเจกต์ว่างโดยไม่มี error ให้เห็น
        """
        combo.clear()
        combo.addItem(PROJECT_PLACEHOLDER, None)
        projects = getattr(self._picker_shared, "projects_cache", None) or []
        for p in projects:
            combo.addItem(p["name"], p["id"])

        if project_id is not None:
            index = combo.findData(project_id)
            if index >= 0:
                combo.setCurrentIndex(index)
                return
        # ยังไม่ login (cache ว่าง) — คงชื่อที่บันทึกไว้ไว้ก่อน จะได้ไม่ดูเหมือนค่าหาย
        if project_name and not projects:
            combo.addItem(project_name, project_id)
            combo.setCurrentIndex(combo.count() - 1)

    def refresh_projects(self) -> None:
        """เรียกหลัง login สำเร็จ — cache โปรเจกต์เพิ่งมี ต้องเติม combo ที่สร้างไว้ตอนยังว่าง"""
        for combo in [row["project"] for row in self._repo_rows] + [self.md_project]:
            combo.blockSignals(True)
            self._fill_project_combo(combo, combo.currentData(), combo.currentText())
            combo.blockSignals(False)
            fit_combo_popup(combo)  # ชื่อโปรเจกต์ยาวกว่าคอมโบได้ ดรอปดาวน์ต้องอ่านออกครบ

    def _on_add_repo_clicked(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "เลือกโฟลเดอร์ git repo")
        if not path:
            return
        path = os.path.normpath(path)
        if not gitlog.is_git_repo(path):
            QMessageBox.warning(self, "Timesheet Sync", f"โฟลเดอร์นี้ไม่ใช่ git repo (ไม่พบ .git):\n{path}")
            return
        if any(r["path"] == path for r in self._repo_rows):
            QMessageBox.information(self, "Timesheet Sync", "repo นี้อยู่ในรายการแล้ว")
            return
        self._add_repo_row({"path": path, "enabled": True})
        self._save_to_config()

    # -- อ่าน git ----------------------------------------------------------

    def _apply_preset(self, preset: str) -> None:
        date_from, date_to = gitlog.date_range(preset)
        self.date_from.setDate(QDate.fromString(date_from, DATE_FORMAT))
        self.date_to.setDate(QDate.fromString(date_to, DATE_FORMAT))

    def _selected_repos(self) -> list[dict]:
        return [
            {"path": r["path"], "project": r["project"].currentText() if r["project"].currentData() else ""}
            for r in self._repo_rows
            if r["enabled"].isChecked()
        ]

    def _project_names_by_repo(self) -> dict:
        """ชื่อ repo -> ชื่อโปรเจกต์แบบอ่านออก ใช้ใน prompt และ combo ของตารางผลลัพธ์

        ตัว id ที่จะลงไฟล์จริงอ่านจาก combo ตอน _collect_result_rows แทน — ผู้ใช้แก้โปรเจกต์
        รายแถวได้หลัง Claude ตอบกลับมาแล้ว ค่าที่ mapping ไว้จึงเป็นแค่ค่าตั้งต้น
        """
        return {
            gitlog.repo_display_name(row["path"]): row["project"].currentText()
            for row in self._repo_rows
            if row["enabled"].isChecked() and row["project"].currentData() is not None
        }

    def _on_read_git_clicked(self) -> None:
        repos = self._selected_repos()
        if not repos:
            QMessageBox.information(self, "Timesheet Sync", 'ยังไม่ได้เลือก repo — กด "+ เพิ่ม repo" ก่อน')
            return

        date_from = self.date_from.date().toString(DATE_FORMAT)
        date_to = self.date_to.date().toString(DATE_FORMAT)
        try:
            gitlog.validate_range(date_from, date_to)
        except gitlog.GitLogError as e:
            QMessageBox.warning(self, "Timesheet Sync", str(e))
            return

        self._save_to_config()
        self._set_git_running(True)
        self.status_label.setText("")

        authors = [a for a in (self._worklog_cfg().get("authors") or []) if str(a).strip()]
        run_async(
            gitlog.collect_commits,
            repos,
            date_from,
            date_to,
            authors,
            on_success=self._on_git_loaded,
            on_error=self._on_git_error,
        )

    def _set_git_running(self, running: bool) -> None:
        set_button_busy(self.read_git_btn, running, "กำลังอ่าน...", "อ่าน git")
        self.git_progress.setVisible(running)

    def _on_git_loaded(self, commits: dict) -> None:
        self._set_git_running(False)
        self._commits = commits

        if commits.get("errors"):
            QMessageBox.warning(
                self, "Timesheet Sync", "อ่านบาง repo ไม่สำเร็จ:\n\n" + "\n".join(commits["errors"])
            )

        total = commits.get("total", 0)
        if not total:
            self.status_label.setText("ไม่พบ commit ในช่วงวันที่ที่เลือก")
            self._refresh_claude_enabled()
            return

        days = len(commits.get("days") or {})
        self.status_label.setText(f"พบ {total} commit · {len(commits.get('repos') or [])} repo · {days} วัน")
        self._refresh_claude_enabled()

    def _on_git_error(self, message: str) -> None:
        self._set_git_running(False)
        QMessageBox.warning(self, "Timesheet Sync", f"อ่าน git ไม่สำเร็จ: {message}")

    # -- เรียก Claude ------------------------------------------------------

    def _claude_path(self) -> str:
        return claude_tasks.find_claude(self._worklog_cfg().get("claude_path") or "")

    def _prompt_for_source(self) -> str:
        """prompt ตามแหล่งข้อมูลที่เลือกอยู่ — สาย QProcess ที่เหลือใช้ร่วมกันทั้งสองทาง

        โหมด .md ส่งชื่อโปรเจกต์ไปให้ Claude แค่ใช้ขึ้นต้น comment เท่านั้น ค่าที่จะลงไฟล์จริง
        _on_claude_finished เติมเองจาก combo ไม่ได้อ่านจากคำตอบ
        """
        hours = self.hours_per_day.value()
        if self._source == "md":
            project = self.md_project.currentText() if self.md_project.currentData() is not None else ""
            return claude_tasks.build_summary_prompt(self._summary_text(), hours, project)
        return claude_tasks.build_prompt(self._commits, hours, self._project_names_by_repo())

    def _on_claude_clicked(self) -> None:
        if not self._source_ready() or self._process is not None:
            return

        claude_path = self._claude_path()
        if not claude_path:
            QMessageBox.warning(
                self,
                "Timesheet Sync",
                "ไม่พบคำสั่ง claude บนเครื่อง\n\n"
                "ติดตั้ง Claude Code ให้อยู่ใน PATH หรือระบุ path เต็มไว้ที่ worklog.claude_path "
                "ใน config.yaml แล้วเปิดโปรแกรมใหม่",
            )
            return

        # โหมด git ได้โปรเจกต์จากการแมป repo อยู่แล้ว แต่โหมด .md มีที่เลือกอยู่ที่เดียว ถ้าข้ามไป
        # ทุกแถวจะไม่มีโปรเจกต์ — เตือนแบบเดียวกับตอนกดส่งเข้าตารางหลัก แต่ไม่บล็อก
        if self._source == "md" and self.md_project.currentData() is None:
            answer = QMessageBox.question(
                self,
                "Timesheet Sync",
                "ยังไม่ได้เลือกโปรเจกต์ — ทุกแถวที่ได้จะยังไม่มีโปรเจกต์ ต้องไปเลือกเองในตารางหลัก"
                "\n\nแตก task ต่อไปเลยหรือไม่?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        try:
            prompt = self._prompt_for_source()
        except claude_tasks.ClaudeTaskError as e:
            QMessageBox.warning(self, "Timesheet Sync", str(e))
            return

        self._stdout_buffer = ""
        self._stderr_buffer = ""
        self._stream_tail = ""
        self._stream_note = ""
        self._thinking_tokens = 0
        self._elapsed = 0

        process = QProcess(self)
        process.readyReadStandardOutput.connect(self._on_stdout)
        process.readyReadStandardError.connect(self._on_stderr)
        process.finished.connect(self._on_claude_finished)
        process.errorOccurred.connect(self._on_claude_process_error)
        self._process = process

        self._set_claude_running(True)
        self._refresh_claude_status()
        cfg = self._worklog_cfg()
        # claude_fast: false = กลับไปใช้คำสั่งชุดเดิม เผื่อ claude บนเครื่องเก่าจนไม่รู้จักธงใหม่
        cmd = claude_tasks.build_command(
            claude_path,
            claude_tasks.resolve_model(self.model_combo.currentData()),
            str(cfg.get("claude_effort") or claude_tasks.DEFAULT_EFFORT),
            fast=cfg.get("claude_fast", True) is not False,
        )
        process.start(cmd[0], cmd[1:])
        # prompt ยาวหลักหมื่นอักษร ยัดเป็น argv ไม่ได้บน Windows (จำกัดราว 32k) ส่งทาง stdin แทน
        process.write(prompt.encode("utf-8"))
        process.closeWriteChannel()

        self._tick.start()
        QTimer.singleShot(CLAUDE_TIMEOUT_MS, self._on_timeout)

    def _set_claude_running(self, running: bool) -> None:
        set_button_busy(self.claude_btn, running, "กำลังแตก task... 0:00", "ให้ Claude แตกเป็น task")
        # set_button_busy เปิดปุ่มคืนให้เสมอตอนจบ แต่ปุ่มนี้ยังต้องมีข้อมูลต้นทางอยู่ก่อนถึงกดได้
        if running:
            self.claude_btn.setEnabled(False)
        else:
            self._refresh_claude_enabled()
        self.claude_progress.setVisible(running)
        self.cancel_btn.setVisible(running)
        self.read_git_btn.setEnabled(not running)
        # เปลี่ยนโมเดลกลางคันไม่มีผลกับ process ที่วิ่งอยู่ แต่ทำให้เข้าใจผิดว่าผลมาจากตัวใหม่
        self.model_combo.setEnabled(not running)
        # สลับแหล่งกลางคันแล้วผลที่ตกมาจะไม่ตรงกับหน้าที่เห็นอยู่ — ล็อกไว้จนกว่าจะจบ
        for btn in (self.git_source_btn, self.md_source_btn):
            btn.setEnabled(not running)

    def _on_tick(self) -> None:
        self._elapsed += 1
        self.claude_btn.setText(f"กำลังแตก task... {self._elapsed // 60}:{self._elapsed % 60:02d}")
        self._refresh_claude_status()

    def _on_stdout(self) -> None:
        if not self._process:
            return
        chunk = bytes(self._process.readAllStandardOutput()).decode("utf-8", "replace")
        self._stdout_buffer += chunk

        # stream-json ส่ง event มาทีละบรรทัด แต่ readyRead ตัดที่ขอบ buffer ไม่ใช่ขอบบรรทัด
        # เศษท้ายที่ยังไม่จบบรรทัดต้องเก็บไว้ต่อกับรอบหน้า ไม่งั้นจะแกะ JSON ไม่ออกทั้งบรรทัด
        self._stream_tail += chunk
        lines = self._stream_tail.split("\n")
        self._stream_tail = lines.pop()
        for line in lines:
            event = claude_tasks.parse_stream_line(line)
            if event:
                self._apply_stream_event(event)

    def _apply_stream_event(self, event: dict) -> None:
        """แปลง event เป็นข้อความสั้นๆ ที่ _on_tick จะเอาไปต่อท้ายนาฬิกา"""
        kind = event["kind"]
        if kind == "started":
            model = event.get("model") or ""
            self._stream_note = f"เชื่อมต่อแล้ว · {model}" if model else "เชื่อมต่อแล้ว"
        elif kind == "thinking":
            # ตัวเดียวที่ขยับถี่พอจะบอกได้ว่ายังไม่ค้าง — ระหว่างคิดคือช่วงที่กินเวลามากที่สุด
            # บวกสะสมเอง เพราะ CLI นับใหม่ทุก thinking block (ดู parse_stream_line)
            self._thinking_tokens += event.get("delta", 0)
            self._stream_note = f"กำลังคิด · {self._thinking_tokens:,} tokens"
        elif kind == "writing":
            self._stream_note = "กำลังเขียนคำตอบ"
        elif kind == "done":
            self._stream_note = "กำลังสรุปผล"
        self._refresh_claude_status()

    def _refresh_claude_status(self) -> None:
        base = self._stream_note or "กำลังเรียก Claude — ปกติใช้เวลา 30 วิ – 2 นาที"
        self.status_label.setText(f"{base} · {self._elapsed // 60}:{self._elapsed % 60:02d}")

    def _on_stderr(self) -> None:
        if self._process:
            self._stderr_buffer += bytes(self._process.readAllStandardError()).decode("utf-8", "replace")

    def _on_cancel_clicked(self) -> None:
        if self._process:
            self.status_label.setText("ยกเลิกแล้ว")
            process, self._process = self._process, None  # กัน _on_claude_finished ทำงานซ้ำ
            process.kill()
            process.deleteLater()
        self._teardown_claude()

    def _on_timeout(self) -> None:
        if self._process is None:
            return  # จบไปแล้วตามปกติ — singleShot ที่ตั้งไว้ยิงมาทีหลังเฉยๆ
        self._on_cancel_clicked()
        self._notify_done()  # ไม่ใช่การกดยกเลิกเอง — ผู้ใช้ยังรออยู่ ต้องบอกว่าจบแล้ว
        QMessageBox.warning(
            self, "Timesheet Sync", f"claude ใช้เวลาเกิน {CLAUDE_TIMEOUT_MS // 60000} นาที — ยกเลิกให้แล้ว"
        )

    def _teardown_claude(self) -> None:
        self._tick.stop()
        self._set_claude_running(False)

    def _notify_done(self) -> None:
        """เรียกผู้ใช้กลับมาเมื่องานจบเอง — รอ 30 วิ - 2 นาที คนส่วนใหญ่สลับไปทำอย่างอื่นแล้ว

        alert() กะพริบ taskbar เฉพาะตอนหน้าต่างไม่ได้ active อยู่แล้ว ถ้านั่งดูอยู่ก็ไม่กวน
        (ไม่ใช้ QSystemTrayIcon เพราะต้องมี icon ค้างอยู่ตลอดการทำงานของแอป)
        ไม่เรียกจากทางกดยกเลิก — ผู้ใช้เพิ่งกดเอง ไม่ต้องเรียกกลับมา
        """
        QApplication.alert(self.window())

    def _on_claude_process_error(self, _error) -> None:
        if self._process is None:
            return
        message = self._process.errorString()
        self._process.deleteLater()
        self._process = None
        self._teardown_claude()
        self.status_label.setText("")  # กัน "กำลังเรียก Claude..." ค้างหลังปิด dialog
        self._notify_done()
        QMessageBox.warning(self, "Timesheet Sync", f"เรียก claude ไม่สำเร็จ: {message}")

    def _on_claude_finished(self, exit_code: int, _status) -> None:
        if self._process is None:
            return  # ถูกยกเลิกไปแล้ว
        self._process.deleteLater()
        self._process = None
        self._teardown_claude()
        self._notify_done()

        if exit_code != 0:
            self._show_claude_failure(f"claude จบด้วย exit code {exit_code}")
            return

        try:
            summary_md, tasks = claude_tasks.parse_response(self._stdout_buffer)
        except claude_tasks.ClaudeTaskError as e:
            self._show_claude_failure(str(e))
            return

        if self._source == "md":
            # ไม่มี repo ให้จับคู่ — โปรเจกต์เดียวที่เลือกไว้ข้างบนใช้กับทุกแถว (แก้รายแถวได้ทีหลัง)
            project = self.md_project.currentText() if self.md_project.currentData() is not None else ""
            for task in tasks:
                task["project"] = project
        else:
            claude_tasks.apply_project_mapping(tasks, self._project_names_by_repo())
        # โหมด .md ไม่ได้ขอ summary_md กลับมา — เก็บเนื้อต้นทางไว้แทน ปุ่ม "บันทึกสรุป .md" จะได้
        # ยังใช้ได้ตอนผู้ใช้พิมพ์/วางสรุปเองโดยไม่มีไฟล์ต้นทางอยู่ก่อน
        self._summary_md = summary_md or self._summary_text()
        self._set_result_rows(tasks)
        self.status_label.setText(f"ได้ {len(tasks)} task จาก Claude — ตรวจและแก้ได้ก่อนส่ง")
        # เช็คต่อเลยว่างานพวกนี้มีอยู่บน OpenProject แล้วหรือยัง — เงียบถ้ายังไม่ได้ login
        self._start_check(auto=True)

    def _show_claude_failure(self, message: str) -> None:
        """คำตอบดิบต้องดูได้เสมอ ไม่งั้น debug ไม่ออกว่า Claude ตอบอะไรมา"""
        # ทางนี้ไม่ได้เขียน status_label ทับเหมือนทางสำเร็จ/ทางยกเลิก ถ้าไม่ล้างเองข้อความ
        # "กำลังเรียก Claude..." จะค้างอยู่ทั้งที่จบไปแล้ว
        self.status_label.setText("แตก task ไม่สำเร็จ")
        box = QMessageBox(self)
        box.setWindowTitle("Timesheet Sync")
        box.setIcon(QMessageBox.Warning)
        box.setText(f"แตก task ไม่สำเร็จ: {message}")
        detail = (self._stderr_buffer or "").strip()
        # เอาเนื้อคำตอบ ไม่ใช่ event ของ stream-json ทั้งกอง — ดู result_text_for_display
        raw = claude_tasks.result_text_for_display(self._stdout_buffer or "").strip()
        box.setDetailedText(f"--- stderr ---\n{detail}\n\n--- คำตอบของ Claude ---\n{raw[:8000]}")
        box.exec()

    # -- เช็คของที่มีอยู่แล้วบน OpenProject ---------------------------------

    def _set_check_running(self, running: bool) -> None:
        set_button_busy(self.check_btn, running, "กำลังเช็ค...", "เช็คบน OpenProject")
        self.check_progress.setVisible(running)
        if running:
            self.status_label.setText("กำลังเช็คบน OpenProject...")
        else:
            # set_button_busy เปิดปุ่มคืนให้เสมอ แต่ปุ่มนี้ยังต้องมีแถวและต้อง login ก่อนถึงกดได้
            self._recompute_totals()

    def _start_check(self, auto: bool) -> None:
        """ถาม OpenProject ว่างานพวกนี้มีอยู่แล้วไหม และวันนั้นลงชั่วโมงไปหรือยัง

        auto=True คือรอบที่ยิงเองหลัง Claude ตอบ — เงียบกว่ารอบที่ผู้ใช้กดปุ่มเอง (ไม่เด้งกล่อง
        บอกว่าเช็คไม่ครบ ไม่บ่นเมื่อยังไม่ได้ login) เพราะผู้ใช้ไม่ได้ขอเอง
        """
        if self._checking:
            return
        rows = self._collect_result_rows()
        if not rows:
            return

        client = getattr(self.state, "client", None)
        if client is None:
            if not auto:
                QMessageBox.information(
                    self, "Timesheet Sync", "ยังไม่ได้ login — เช็คกับ OpenProject ไม่ได้"
                )
            return

        # (project_id, ชื่องาน) ที่ไม่ซ้ำกัน — ยิง 1 คำขอต่อคู่ เหมือนที่ตารางหลักยุบก่อน submit
        # แถวที่ยังไม่เลือกโปรเจกต์ค้นไม่ได้ (ต้องมี scope) ข้ามไป ยังเช็คชั่วโมงของวันนั้นได้อยู่
        pairs = sorted({(int(r["project"]), r["subject"]) for r in rows if r["project"]},
                       key=lambda p: p[1])
        dates = sorted({r["date"] for r in rows})

        self._checking = True
        self._set_check_running(True)
        run_async(
            check_existing_work,
            client,
            pairs,
            dates[0],
            dates[-1],
            on_success=lambda result, a=auto: self._on_check_done(result, a),
            on_error=lambda message, a=auto: self._on_check_error(message, a),
        )

    def _on_check_done(self, result: dict, auto: bool) -> None:
        self._checking = False
        self._duplicates = result.get("duplicates") or {}
        self._logged_by_date = result.get("logged_by_date") or {}
        self._logged_wp_dates = result.get("logged_wp_dates") or {}
        for row in self._result_rows:
            self._refresh_wp_combo(row)
        self._set_check_running(False)

        found = sum(1 for row in self._result_rows if row["wp"].currentData() is not None)
        errors = result.get("errors") or []
        note = (f"เจองานเดิม {found} จาก {len(self._result_rows)} task" if found
                else "ไม่พบงานเดิมบน OpenProject — สร้างใหม่ทั้งหมด")
        self.status_label.setText(note + (" · เช็คได้ไม่ครบ" if errors else ""))
        if errors and not auto:
            QMessageBox.warning(self, "Timesheet Sync", "เช็คได้ไม่ครบ:\n\n" + "\n".join(errors))

    def _on_check_error(self, message: str, auto: bool) -> None:
        """check_existing_work จับ error เองหมดแล้ว ทางนี้เหลือแค่กรณีที่ worker พังจริงๆ"""
        self._checking = False
        self._set_check_running(False)
        self.status_label.setText("เช็คกับ OpenProject ไม่สำเร็จ")
        if not auto:
            QMessageBox.warning(self, "Timesheet Sync", f"เช็คกับ OpenProject ไม่สำเร็จ: {message}")

    def _refresh_wp_combo(self, row: dict) -> None:
        """เติมตัวเลือกคอลัมน์ "บน OpenProject" ของแถวเดียวจากผลเช็คล่าสุด

        เจอแล้วเลือกงานเดิมไว้ให้เลย — จุดประสงค์ของการเช็คคือไม่สร้างซ้ำ แต่ถ้าผู้ใช้เคยเลือกเอง
        (wp_user_set) ต้องคงค่านั้นไว้ ไม่งั้นกดเช็คซ้ำทีเดียวการตัดสินใจของคนหายหมด
        """
        combo = row["wp"]
        matches = self._duplicates.get(row["subject"].text().strip()) or []

        combo.blockSignals(True)
        combo.clear()
        combo.addItem(WP_CREATE_NEW, None)
        for wp in matches:
            combo.addItem(f"#{wp['id']} {(wp.get('subject') or '').strip()}".strip(), wp["id"])

        fit_combo_popup(combo)  # "#1234 <ชื่องาน>" ยาวกว่าคอลัมน์เสมอ
        wanted = row["wp_choice"] if row["wp_user_set"] else (matches[0]["id"] if matches else None)
        index = combo.findData(wanted) if wanted is not None else 0
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

        row["wp_choice"] = combo.currentData()
        self._mark_wp_logged(row)

    def _mark_wp_logged(self, row: dict) -> None:
        """งานนี้ + วันนี้ ลงชั่วโมงไปแล้ว = ซ้ำแน่ๆ ไม่ใช่แค่ชื่อพ้องกัน — ย้อมสีให้เห็นตั้งแต่ในตาราง"""
        combo = row["wp"]
        wp_id = combo.currentData()
        date = row["date"].date().toString(DATE_FORMAT)
        hours = self._logged_wp_dates.get((wp_id, date)) if wp_id is not None else None
        if hours:
            combo.setStyleSheet(f"color: {COLORS['warn']};")
            combo.setToolTip(f"งานนี้ลงชั่วโมงวันที่ {date} ไปแล้ว {hours:g} ชม. — ส่งอีกคือลงซ้ำ")
        else:
            combo.setStyleSheet("")
            combo.setToolTip("")

    def _on_wp_choice(self, row: dict) -> None:
        """ผู้ใช้เลือกเอง — activated ยิงเฉพาะตอนคนกด ไม่ยิงตอนโค้ดตั้งค่า จึงใช้แยกสองกรณีได้"""
        row["wp_user_set"] = True
        row["wp_choice"] = row["wp"].currentData()
        self._mark_wp_logged(row)
        self._recompute_totals()

    def _on_row_date_changed(self, row: dict) -> None:
        self._mark_wp_logged(row)  # ย้ายวันแล้วอาจไม่ซ้ำแล้ว (หรือกลับกัน)
        self._recompute_totals()

    def _refresh_logged_label(self, rows: list[dict]) -> None:
        """วันไหนในตารางที่มีชั่วโมงอยู่บน OpenProject แล้ว — เตือนเฉยๆ ไม่บล็อก แนวเดียวกับ
        ยอดชั่วโมงต่อวันที่ไม่ครบ เพราะมีคนนั่งตรวจอยู่ตรงนี้"""
        dates = sorted({r["date"] for r in rows} & set(self._logged_by_date))
        if not dates:
            self.logged_label.setText("")
            return
        shown = dates[:LOGGED_DATES_SHOWN]
        detail = " · ".join(
            f"{d} = {self._logged_by_date[d]['hours']:g} ชม. ({self._logged_by_date[d]['count']} รายการ)"
            for d in shown
        )
        more = f" (+อีก {len(dates) - len(shown)} วัน)" if len(dates) > len(shown) else ""
        self.logged_label.setText(f"⚠ วันที่ลงชั่วโมงไปแล้ว: {detail}{more}")

    # -- ตารางผลลัพธ์ -------------------------------------------------------

    def _set_result_rows(self, tasks: list[dict]) -> None:
        # ชุดใหม่ = ชื่องานคนละชุด ผลเช็คเดิมใช้ไม่ได้แล้ว (ไม่ล้างใน _add_result_row เพราะปุ่ม
        # "+ เพิ่มแถว" ไม่ควรทำให้ผลเช็คของแถวอื่นหายไป)
        self._duplicates = {}
        self._logged_by_date = {}
        self._logged_wp_dates = {}
        self._result_rows = []
        self.result_table.setRowCount(0)
        for task in tasks:
            self._add_result_row(task)
        self._recompute_totals()

    def _add_result_row(self, values: dict) -> None:
        date_edit = self._make_date_edit()
        parsed = QDate.fromString(values.get("date") or "", DATE_FORMAT)
        date_edit.setDate(parsed if parsed.isValid() else self.date_from.date())
        # ต่อ signal ทีหลัง (ต้องอ้างถึง row dict ที่ยังไม่ได้สร้าง) — ดูใต้บล็อก row = {...}

        hours = QDoubleSpinBox()
        hours.setDecimals(2)
        hours.setSingleStep(0.5)
        hours.setRange(0.0, 24.0)
        hours.setValue(float(values.get("hours") or 0))
        hours.valueChanged.connect(lambda _v: self._recompute_totals())

        subject = QLineEdit(values.get("subject") or "")
        subject.textChanged.connect(lambda _t: self._recompute_totals())

        comment = QLineEdit(values.get("comment") or "")
        # QLineEdit วางเคอร์เซอร์ไว้ท้ายข้อความ ทำให้ข้อความยาวโชว์ท่อนหลังแล้วอ่านไม่รู้เรื่อง
        for field in (subject, comment):
            field.setCursorPosition(0)

        project = QComboBox()
        self._fill_result_project_combo(project, values.get("project") or "")

        wp = QComboBox()
        wp.addItem(WP_CREATE_NEW, None)

        remove_btn = QPushButton("✕")
        remove_btn.setObjectName("linkButton")
        remove_btn.setCursor(Qt.PointingHandCursor)

        row = {
            "date": date_edit, "hours": hours, "subject": subject,
            "comment": comment, "project": project, "wp": wp,
            # ผู้ใช้เลือกงานเดิม/สร้างใหม่เองแล้วหรือยัง — กันการเช็คซ้ำทับการตัดสินใจของคน
            "wp_user_set": False, "wp_choice": None,
        }
        remove_btn.clicked.connect(lambda _=False, r=row: self._remove_result_row(r))
        date_edit.dateChanged.connect(lambda _d, r=row: self._on_row_date_changed(r))
        wp.activated.connect(lambda _i, r=row: self._on_wp_choice(r))

        index = len(self._result_rows)
        self._result_rows.append(row)
        self.result_table.setRowCount(len(self._result_rows))

        self.result_table.setCellWidget(index, 0, remove_btn)
        self.result_table.setCellWidget(index, 1, date_edit)
        self.result_table.setCellWidget(index, 2, hours)
        self.result_table.setCellWidget(index, 3, subject)
        self.result_table.setCellWidget(index, 4, comment)
        self.result_table.setCellWidget(index, 5, project)
        self.result_table.setCellWidget(index, 6, wp)
        self.result_table.setRowHeight(index, RESULT_ROW_HEIGHT)
        self._refresh_wp_combo(row)  # แถวที่เพิ่มหลังเช็คแล้วต้องได้ตัวเลือกชุดเดียวกัน
        self._recompute_totals()

    def _fill_result_project_combo(self, combo: QComboBox, current_name: str) -> None:
        combo.addItem(PROJECT_PLACEHOLDER, None)
        for p in getattr(self._picker_shared, "projects_cache", None) or []:
            combo.addItem(p["name"], p["id"])
        fit_combo_popup(combo)
        if current_name:
            index = combo.findText(current_name)
            if index >= 0:
                combo.setCurrentIndex(index)

    def _remove_result_row(self, row: dict) -> None:
        if row not in self._result_rows:
            return
        index = self._result_rows.index(row)
        self._result_rows.pop(index)
        self.result_table.removeRow(index)
        self._recompute_totals()

    def _collect_result_rows(self) -> list[dict]:
        """แถวที่กรอกครบพอจะเขียนลงไฟล์ได้ — ชื่องานว่างหรือชั่วโมง 0 ถือว่ายังไม่พร้อม"""
        rows = []
        activity = self._worklog_cfg().get("default_activity") or "Normal"
        for row in self._result_rows:
            subject = row["subject"].text().strip()
            hours = row["hours"].value()
            if not subject or hours <= 0:
                continue
            project_id = row["project"].currentData()
            rows.append({
                "date": row["date"].date().toString(DATE_FORMAT),
                "hours": hours,
                "activity": activity,
                # ลงเป็น id ไม่ใช่ชื่อ — _resolve_new_projects จับคู่ by_id ก่อน by_name
                "project": str(project_id) if project_id is not None else "",
                "subject": subject,
                "comment": row["comment"].text().strip(),
                # มีค่า = ผูกกับงานเดิม generate_task_template จะเขียนเป็น wp_mode="existing"
                "wp_id": row["wp"].currentData(),
            })
        return rows

    def _recompute_totals(self) -> None:
        rows = self._collect_result_rows()
        has_rows = bool(rows)
        self.add_row_btn.setEnabled(bool(self._result_rows) or self._source_ready())
        self.send_btn.setEnabled(has_rows)
        self.save_md_btn.setEnabled(bool(self._summary_md))
        self.check_btn.setEnabled(
            has_rows and getattr(self.state, "client", None) is not None and not self._checking
        )
        self._refresh_logged_label(rows)

        if not has_rows:
            self.totals_label.setText("")
            return

        target = self.hours_per_day.value()
        totals: dict[str, float] = {}
        for r in rows:
            totals[r["date"]] = totals.get(r["date"], 0) + r["hours"]

        off = {d: h for d, h in sorted(totals.items()) if abs(h - target) > HOURS_EPSILON}
        if not off:
            self.totals_label.setText(f"{len(totals)} วัน × {target:g} ชม. ครบพอดี ({len(rows)} task)")
            self.totals_label.setStyleSheet(f"color: {COLORS['success']};")
            return

        detail = " · ".join(f"{d} = {h:g} ชม." for d, h in list(off.items())[:4])
        more = f" (+อีก {len(off) - 4} วัน)" if len(off) > 4 else ""
        self.totals_label.setText(f"ชั่วโมงไม่ครบ {target:g} ชม./วัน: {detail}{more}")
        self.totals_label.setStyleSheet(f"color: {COLORS['warn']};")

    # -- ปลายทาง -----------------------------------------------------------

    def _range_suffix(self) -> str:
        """ช่วงวันที่ที่เลือกไว้ — ใช้ตั้งชื่อไฟล์สรุป .md ซึ่งครอบคลุมช่วงที่อ่าน git มา"""
        date_from = self.date_from.date().toString(DATE_FORMAT)
        date_to = self.date_to.date().toString(DATE_FORMAT)
        return date_from if date_from == date_to else f"{date_from}_to_{date_to}"

    def _rows_suffix(self, rows: list[dict]) -> str:
        """ช่วงวันที่ของแถวจริง — ชื่อไฟล์ .xlsx ต้องบอกเนื้อในไฟล์ ไม่ใช่ค่าที่ค้างอยู่ในช่องวันที่
        (ผู้ใช้แก้วันที่รายแถวได้ และช่องด้านบนอาจถูกเลื่อนไปแล้วหลังอ่าน git)"""
        dates = sorted({r["date"] for r in rows})
        if not dates:
            return self._range_suffix()
        return dates[0] if dates[0] == dates[-1] else f"{dates[0]}_to_{dates[-1]}"

    def _output_dir(self) -> str:
        """โฟลเดอร์ที่ตั้งไว้ -> โฟลเดอร์ของไฟล์ล่าสุด -> Documents

        โฟลเดอร์ที่ตั้งไว้ชนะเสมอแม้ยังไม่มีอยู่จริง (ผู้เรียกสร้างให้ด้วย makedirs) — ถ้าเช็ค
        isdir ก่อนแล้วตกไป fallback ไฟล์จะไปโผล่ใน Documents เงียบๆ ทั้งที่ตั้งค่าไว้แล้ว
        """
        configured = (self._worklog_cfg().get("output_dir") or "").strip()
        if configured:
            return configured
        last_file = (self.state.config.get("last_file") or "").strip()
        if last_file and os.path.isdir(os.path.dirname(last_file)):
            return os.path.dirname(last_file)
        return os.path.join(os.path.expanduser("~"), "Documents")

    def _unique_path(self, directory: str, filename: str, ext: str) -> str:
        path = os.path.join(directory, f"{filename}{ext}")
        counter = 2
        while os.path.exists(path):
            path = os.path.join(directory, f"{filename} ({counter}){ext}")
            counter += 1
        return path

    def _on_save_md_clicked(self) -> None:
        if not self._summary_md:
            return
        directory = self._output_dir()
        try:
            os.makedirs(directory, exist_ok=True)
            # โหมด .md ช่องวันที่ด้านบนไม่เกี่ยวกับเนื้อในสรุป — ใช้ช่วงวันที่ของแถวจริงแทน
            suffix = self._rows_suffix(self._collect_result_rows()) if self._source == "md" else self._range_suffix()
            path = self._unique_path(directory, f"Work_Summary_{suffix}", ".md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._summary_md)
        except OSError as e:
            QMessageBox.warning(self, "Timesheet Sync", f"บันทึกไฟล์สรุปไม่สำเร็จ: {e}")
            return
        QMessageBox.information(self, "Timesheet Sync", f"บันทึกสรุปแล้ว:\n{path}")

    def _confirm_already_logged(self, rows: list[dict]) -> bool:
        """แถวที่งานนั้นวันนั้นลงชั่วโมงไปแล้ว = ส่งอีกคือลงซ้ำ — ถามก่อน ไม่บล็อก (บางทีตั้งใจลง
        เพิ่มจริง) แนวเดียวกับกล่องเตือนชื่องานซ้ำก่อน submit ในตารางหลัก"""
        logged = [r for r in rows
                  if r.get("wp_id") is not None and self._logged_wp_dates.get((r["wp_id"], r["date"]))]
        if not logged:
            return True

        lines = [f"{r['date']} · {r['subject']} (#{r['wp_id']} ลงแล้ว "
                 f"{self._logged_wp_dates[(r['wp_id'], r['date'])]:g} ชม.)"
                 for r in logged[:MAX_LOGGED_PREVIEW]]
        detail = "\n".join(lines)
        if len(logged) > MAX_LOGGED_PREVIEW:
            detail += f"\n... และอีก {len(logged) - MAX_LOGGED_PREVIEW} แถว"

        answer = QMessageBox.question(
            self,
            "Timesheet Sync",
            f"มี {len(logged)} แถวที่งานนั้นลงชั่วโมงในวันนั้นไปแล้วบน OpenProject — "
            f"ส่งต่อคือลงชั่วโมงซ้ำ\n\n{detail}\n\nส่งต่อไปเลยหรือไม่?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def _on_send_clicked(self) -> None:
        rows = self._collect_result_rows()
        if not rows:
            return

        # แถวที่ผูกกับงานเดิมแล้วไม่ต้องมีโปรเจกต์ — ตัวโหลดไฟล์ใช้ new_project เฉพาะแถว wp_mode=new
        missing = [r for r in rows if not r["project"] and r.get("wp_id") is None]
        if missing:
            answer = QMessageBox.question(
                self,
                "Timesheet Sync",
                f"มี {len(missing)} แถวที่ยังไม่ได้เลือกโปรเจกต์ — ต้องไปเลือกเองในตารางหลัก\n\nส่งต่อไปเลยหรือไม่?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        if not self._confirm_already_logged(rows):
            return

        directory = self._output_dir()
        try:
            os.makedirs(directory, exist_ok=True)
            path = self._unique_path(directory, f"timesheet_{self._rows_suffix(rows)}", ".xlsx")
            # ต้องเขียนไฟล์จริงเสมอ ไม่ใช่ส่ง rows เข้า memory เฉยๆ — submit_rows เขียน
            # synced/sync_note กลับไฟล์นี้หลังส่งสำเร็จ ไม่มีไฟล์คือ submit พัง
            generate_task_template(rows, self.state.config["excel"], path)
        except Exception as e:
            QMessageBox.warning(self, "Timesheet Sync", f"สร้างไฟล์ไม่สำเร็จ: {e}")
            return

        self._save_to_config()
        self.file_ready.emit(path)

    # -- lifecycle ---------------------------------------------------------

    def reset(self) -> None:
        """logout = คนละเซิร์ฟเวอร์ได้ project id ชุดเก่าใช้ไม่ได้ ต้องล้างผลลัพธ์ทิ้ง
        (ตัว repo กับช่วงวันที่เก็บไว้ ไม่เกี่ยวกับ credential)"""
        self._on_cancel_clicked()
        self._commits = None
        self._summary_md = ""
        self._summary_path = ""
        self._duplicates = {}
        self._logged_by_date = {}
        self._logged_wp_dates = {}
        self.summary_edit.clear()
        self._set_md_file_label("")
        self._set_result_rows([])
        self.claude_btn.setEnabled(False)
        self.status_label.setText("")
        for combo in [row["project"] for row in self._repo_rows] + [self.md_project]:
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)

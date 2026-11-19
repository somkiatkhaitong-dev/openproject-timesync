"""History/report tab — fetch the logged-in user's OpenProject time entries for a chosen month
or custom date range, and optionally export them.

Nothing loads until the user presses "ค้นหา" (matches the app-wide rule that nothing fetches
automatically). The current user's id/email is resolved once per login via get_current_user()
and cached on the instance, then reused for both filtering and the export's user_email column.

export_time_entries_to_excel() writes the same Excel schema the upload tab reads (excel.columns
from config.yaml), so an exported file can be re-opened via "⬆ อัปโหลดไฟล์ใหม่" if needed — rows
are already marked synced=True since they came from OpenProject in the first place.

The status-change button lives here rather than in the sync tab because the sync table only loads
rows where synced != True — work packages just created by a submit drop out of it immediately,
while this tab finds them again by date range.
"""
from __future__ import annotations

from PySide6.QtCore import QDate, QPointF, QTimer, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap,
                           QPolygonF)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from common import bulk_update_status, export_time_entries_to_excel, summarize_coverage
from gui.busy import make_busy_bar, set_button_busy
from gui.flow_layout import FlowWidget
from gui.state import AppState
from gui.theme import COLORS, fit_combo_popup, tune_calendar_popup
from gui.threads import run_async

DATE_FORMAT = "yyyy-MM-dd"
# ความยาวชื่องานสูงสุดในชิปสรุป — ยาวกว่านี้ Qt จะย่อทิ้งท้ายเองจนจำนวนชั่วโมงหายไป
CHIP_LABEL_MAX_CHARS = 42
# เครื่องหมายนำหน้าแถวย่อย — ใช้ตัวอักษรแทนการเยื้องด้วยช่องว่าง เพราะคอลัมน์ "งาน" ตัด
# ข้อความจากท้าย ช่องว่างนำหน้าจึงกินความกว้างที่ควรเป็นของชื่องาน
# พื้นแถวย่อย — อ่อนกว่าขาวนิดเดียว บอกว่าอยู่ในกลุ่มโดยไม่ต้องตีเส้น และยังจางกว่า
# bg_subtle ที่แถบวันที่ใช้ ลำดับความเข้มจึงเป็น แถบวันที่ > แถวงาน > แถวย่อย > แถวเดี่ยว
NESTED_ROW_BG = "#FBFAF7"
# เส้นแนวตั้งที่ลากลงมาจากแถวงาน แทนช่องวันที่/โปรเจกต์ที่ว่างอยู่ของแถวย่อย
RAIL_WIDTH = 2
SUBROW_INDENT = 14
# ความกว้างคอลัมน์คงที่ (คอลัมน์ 2 "งาน" เป็น Stretch จึงไม่อยู่ในนี้) — ไม่ตั้งไว้ Qt จะบีบ
# คอลัมน์หมายเหตุจนข้อความโดนตัดตลอด
COLUMN_WIDTHS = {0: 92, 1: 118, 3: 84, 4: 64, 5: 190, 6: 116}
COLUMN_LABELS = ["วันที่", "โปรเจกต์", "งาน (Work Package)", "กิจกรรม", "ชั่วโมง", "หมายเหตุ", "สถานะ"]
STATUS_COLUMN = COLUMN_LABELS.index("สถานะ")
FILTER_DEBOUNCE_MS = 250
# กว้างพอแสดง "2026-08-01" ครบพร้อมปุ่มปฏิทิน (ไม่ตั้งไว้ จะถูกบีบเหลือ "2026-08-")
# ตัวเลขนี้ต้องวัดจากภาพจริง — QDateEdit.sizeHint() คืน 106 ซึ่งยังตัดข้อความอยู่ เพราะ Qt
# ไม่นับ padding จาก stylesheet ในการคำนวณ sizeHint ของ widget ตระกูล spinbox
DATE_FIELD_MIN_WIDTH = 132
# ชื่อสถานะที่จะเลือกให้เป็นค่าตั้งต้น ไล่ตามลำดับ — ชื่อสถานะตั้งเองได้ในแต่ละ instance
# (เช่นของ softdebut ใช้ "IN PROCESS" ไม่ใช่ "In progress" และยังมี "IN PROGRESS RESLOVED"
# กับ "IN PROGRESS INTERNAL TEST" ที่ห้ามเผลอไปโดน)
DEFAULT_STATUS_NAMES = ("in process", "in progress")
DEFAULT_FROM_STATUS_NAMES = ("to do", "todo", "new")
MAX_PREVIEW_LINES = 10  # จำนวนบรรทัดตัวอย่างสูงสุดในกล่องยืนยัน/รายงานผล


_CHEVRON_CACHE: dict = {}


def _chevron_icon(expanded: bool) -> QIcon:
    """ลูกศรพับ/กางของแถวงาน วาดเองด้วย QPainter

    ไม่ใช้ตัวอักษร ▾/▸ เพราะความกว้างและตำแหน่งของมันขึ้นกับฟอนต์ที่เครื่องปลายทาง fallback
    ไปใช้ และไม่ใช้ไฟล์ .svg ในโฟลเดอร์ assets เพราะจะต้องไปเพิ่มใน .spec ของ PyInstaller ด้วย

    เรนเดอร์ที่ 2 เท่าแล้วตั้ง devicePixelRatio — วาดที่ 16px ตรงๆ จะเบลอบนจอ HiDPI
    """
    cached = _CHEVRON_CACHE.get(expanded)
    if cached is not None:
        return cached

    size = 16
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.setDevicePixelRatio(2.0)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(COLORS["accent_text"]))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        points = (
            [QPointF(4.5, 6.5), QPointF(8, 10), QPointF(11.5, 6.5)] if expanded
            else [QPointF(6.5, 4.5), QPointF(10, 8), QPointF(6.5, 11.5)]
        )
        painter.drawPolyline(QPolygonF(points))
    finally:
        painter.end()

    icon = QIcon(pixmap)
    _CHEVRON_CACHE[expanded] = icon
    return icon


class HistoryTab(QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self._entries: list[dict] = []
        # รายการหลังกรอง = ชุดที่ปุ่ม "ปรับสถานะงาน" จะทำงานด้วย (กรองเหลืองานเดียวก็ปรับตัวเดียว)
        self._filtered: list[dict] = []
        self._statuses: list[dict] | None = None  # None = ยังไม่เคยโหลด
        # wp_id -> dict จาก _transform_wp (status/author) โหลดหนึ่งครั้งต่อการค้นหา ไม่ใช่ต่อแถว
        self._wp_status: dict[int, dict] = {}
        self._user_id: int | None = None
        self._user_email: str | None = None
        # ช่วงวันที่ของ "การค้นหาครั้งล่าสุด" ไม่ใช่ค่าในช่องวันที่ตอนนี้ — ผู้ใช้ขยับช่องได้โดยยัง
        # ไม่กดค้นหาใหม่ ถ้าอ่านจากช่องตอนวาด คำเตือนจะไม่ตรงกับข้อมูลที่แสดงอยู่
        self._searched_range: tuple[str, str] | None = None
        # (วันที่, wp_id) ของหัวกลุ่มที่ถูกพับอยู่ — เก็บเป็นคีย์ของข้อมูล ไม่ใช่เลขแถว เพื่อให้
        # รอดการ re-render (กรอง/ค้นหา/ปรับสถานะ วาดตารางใหม่ทั้งใบทุกครั้ง)
        self._collapsed: set[tuple] = set()
        # เลขแถวหัวกลุ่ม -> (คีย์, [เลขแถวย่อยของมัน]) สร้างใหม่ทุกครั้งที่วาดตาราง
        self._task_rows: dict[int, tuple] = {}

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(FILTER_DEBOUNCE_MS)
        self._filter_timer.timeout.connect(self._apply_filters)

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        toolbar = QHBoxLayout()

        toolbar.addWidget(QLabel("ช่วงเดือน:"))
        self.month_combo = QComboBox()
        self.month_combo.currentIndexChanged.connect(self._on_month_combo_changed)
        toolbar.addWidget(self.month_combo)

        toolbar.addWidget(QLabel("จาก:"))
        self.from_date = QDateEdit()
        self.from_date.setCalendarPopup(True)
        tune_calendar_popup(self.from_date)
        self.from_date.setDisplayFormat(DATE_FORMAT)
        # ตั้ง min-width ที่ตัวนี้ตรงๆ ไม่ใช่ที่ theme.py เพราะ QDateEdit ตัวเดียวกันถูกใช้ใน
        # เซลล์ตารางของแท็บสร้าง Template (คอลัมน์กว้าง 120px) การใส่ min-width ระดับ global
        # จะไปดันคอลัมน์นั้นเพี้ยน — ไม่ใส่แล้ววันที่จะถูกตัดเหลือ "2026-08-"
        self.from_date.setMinimumWidth(DATE_FIELD_MIN_WIDTH)
        toolbar.addWidget(self.from_date)

        toolbar.addWidget(QLabel("ถึง:"))
        self.to_date = QDateEdit()
        self.to_date.setCalendarPopup(True)
        tune_calendar_popup(self.to_date)
        self.to_date.setDisplayFormat(DATE_FORMAT)
        self.to_date.setMinimumWidth(DATE_FIELD_MIN_WIDTH)
        toolbar.addWidget(self.to_date)

        self.search_btn = QPushButton("ค้นหา")
        self.search_btn.setObjectName("primaryButton")
        self.search_btn.setCursor(Qt.PointingHandCursor)
        self.search_btn.clicked.connect(self._on_search_clicked)
        toolbar.addWidget(self.search_btn)

        self.export_btn = QPushButton("⬇ ส่งออก Excel")
        self.export_btn.setObjectName("outlineButton")
        self.export_btn.setCursor(Qt.PointingHandCursor)
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._on_export_clicked)
        toolbar.addWidget(self.export_btn)

        # หลอดเดียวใช้ร่วมกันทั้งค้นหาและส่งออก — สองปุ่มนี้ทำงานพร้อมกันไม่ได้อยู่แล้ว
        self.toolbar_progress = make_busy_bar(110)
        toolbar.addWidget(self.toolbar_progress)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {COLORS['border']}; border: none;")
        layout.addWidget(divider)

        # แถวที่สอง = กลุ่มแก้สถานะ แยกจากแถวบนที่ไว้ดูข้อมูลอย่างเดียว
        # (แถวเดียวกันหมดเคยยาว 9 ตัวควบคุมจนช่องวันที่ถูกบีบจนอ่านไม่ออก)
        status_bar = QHBoxLayout()

        status_bar.addWidget(QLabel("ปรับสถานะ:"))
        self.from_status_combo = QComboBox()
        self.from_status_combo.setEnabled(False)
        status_bar.addWidget(self.from_status_combo)

        status_bar.addWidget(QLabel("→"))
        self.status_combo = QComboBox()
        self.status_combo.setEnabled(False)
        status_bar.addWidget(self.status_combo)

        self.status_btn = QPushButton("↻ ปรับสถานะงาน")
        self.status_btn.setObjectName("outlineButton")
        self.status_btn.setCursor(Qt.PointingHandCursor)
        self.status_btn.setEnabled(False)
        self.status_btn.clicked.connect(self._on_change_status_clicked)
        status_bar.addWidget(self.status_btn)

        self.status_progress = make_busy_bar(110)
        status_bar.addWidget(self.status_progress)

        # ติ๊กไว้ตั้งต้น — งานเดือนเดียวกันมักมีของเพื่อนร่วมทีมปนอยู่ (เคสจริง: 81 อันของเดือน ก.ค.
        # มีของคนอื่น 13 อัน และหนึ่งในนั้นอยู่สถานะเดียวกับของเราเป๊ะ)
        self.mine_only_check = QCheckBox("เฉพาะงานที่ฉันสร้าง")
        self.mine_only_check.setChecked(True)
        self.mine_only_check.setCursor(Qt.PointingHandCursor)
        self.mine_only_check.stateChanged.connect(lambda _s: self._render_rows(self._filtered))
        status_bar.addWidget(self.mine_only_check)

        status_bar.addStretch()
        layout.addLayout(status_bar)

        filter_bar = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("ค้นหางาน, โปรเจกต์, หมายเหตุ...")
        self.search_edit.textChanged.connect(self._filter_timer.start)
        filter_bar.addWidget(self.search_edit, stretch=1)

        self.wp_filter_combo = QComboBox()
        self.wp_filter_combo.addItem("ทั้งหมด", None)
        self.wp_filter_combo.currentIndexChanged.connect(self._apply_filters)
        filter_bar.addWidget(self.wp_filter_combo, stretch=1)

        self.status_filter_combo = QComboBox()
        self.status_filter_combo.addItem("ทุกสถานะ", None)
        self.status_filter_combo.currentIndexChanged.connect(self._apply_filters)
        filter_bar.addWidget(self.status_filter_combo, stretch=1)
        layout.addLayout(filter_bar)

        self.summary_totals_label = QLabel("")
        self.summary_totals_label.setObjectName("fieldLabel")
        self.summary_totals_label.setWordWrap(True)
        layout.addWidget(self.summary_totals_label)

        # ชั่วโมงแยกตามโปรเจกต์ของชุดที่แสดงอยู่ — ซ่อนเมื่อมีโปรเจกต์เดียว
        self.project_totals_label = QLabel("")
        self.project_totals_label.setObjectName("fieldLabel")
        self.project_totals_label.setWordWrap(True)
        self.project_totals_label.hide()
        layout.addWidget(self.project_totals_label)

        # คำเตือนวันที่ลงไม่ครบ/ลงเกิน — ซ่อนไว้จนกว่าจะมีอะไรต้องเตือนจริง
        self.coverage_label = QLabel("")
        self.coverage_label.setWordWrap(True)
        self.coverage_label.hide()
        layout.addWidget(self.coverage_label)

        # ชิปสรุปงานยาวไม่เท่ากันและรวมกันเกินความกว้างหน้าต่างบ่อย — ให้ไหลลงบรรทัดใหม่เอง
        # แทนที่จะถูก QHBoxLayout บีบจนชื่องานและจำนวนชั่วโมงถูกตัดหายไปทั้งแถว
        self.breakdown_widget = FlowWidget(spacing=6)
        self.breakdown_layout = self.breakdown_widget.flow
        layout.addWidget(self.breakdown_widget)

        self.table = QTableWidget()
        self.table.setColumnCount(len(COLUMN_LABELS))
        self.table.setHorizontalHeaderLabels(COLUMN_LABELS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        for col, width in COLUMN_WIDTHS.items():
            self.table.setColumnWidth(col, width)
        # คลิกที่ไหนก็ได้ในแถวหัวกลุ่มเพื่อพับ/กาง — คอลัมน์สถานะมี QComboBox กินคลิกไปเอง
        # จึงไม่ชนกับการเปลี่ยนสถานะ
        self.table.cellClicked.connect(self._on_cell_clicked)
        layout.addWidget(self.table, stretch=1)

        self._set_default_range()
        self._show_empty_message('เลือกช่วงวันที่แล้วกด "ค้นหา" เพื่อดึงข้อมูล')

    def _set_default_range(self) -> None:
        today = QDate.currentDate()
        self.from_date.setDate(QDate(today.year(), today.month(), 1))
        self.to_date.setDate(today)
        self._populate_month_combo()

    def _populate_month_combo(self) -> None:
        self.month_combo.blockSignals(True)
        self.month_combo.clear()
        today = QDate.currentDate()
        for i in range(12):
            d = today.addMonths(-i)
            self.month_combo.addItem(d.toString("MMMM yyyy"), (d.year(), d.month()))
        self.month_combo.addItem("กำหนดเอง", None)
        self.month_combo.setCurrentIndex(0)  # เดือนปัจจุบัน
        self.month_combo.blockSignals(False)

    def _on_month_combo_changed(self, _index: int) -> None:
        data = self.month_combo.currentData()
        if not data:
            return  # เลือก "กำหนดเอง" — ปล่อยให้แก้ from/to เอง
        year, month = data
        first = QDate(year, month, 1)
        last = QDate(year, month, first.daysInMonth())
        self.from_date.setDate(first)
        self.to_date.setDate(last)

    def _on_search_clicked(self) -> None:
        if not self.state.client:
            return
        if self.from_date.date() > self.to_date.date():
            QMessageBox.warning(self, "Timesheet Sync", "วันที่เริ่มต้นต้องไม่เกินวันที่สิ้นสุด")
            return

        self._set_search_busy(True)
        self._show_empty_message("กำลังโหลดข้อมูล...")

        # โหลดรายชื่อสถานะครั้งเดียวต่อ login แยก worker ไปเลย ไม่ต้องรอ entries (และล้มเหลวก็ไม่
        # กระทบการค้นหา — แค่ปุ่มปรับสถานะยังกดไม่ได้)
        if self._statuses is None:
            run_async(
                self.state.client.get_statuses,
                on_success=self._on_statuses_loaded,
                on_error=self._on_statuses_error,
            )

        if self._user_id is None:
            run_async(
                self.state.client.get_current_user,
                on_success=self._on_user_loaded,
                on_error=self._on_search_error,
            )
        else:
            self._fetch_entries()

    def _on_user_loaded(self, user: dict) -> None:
        self._user_id = user.get("id")
        self._user_email = user.get("email")
        if not self._user_id:
            self._on_search_error("ไม่พบข้อมูลผู้ใช้ปัจจุบันจาก OpenProject")
            return
        self._fetch_entries()

    def _fetch_entries(self) -> None:
        from_str = self.from_date.date().toString(DATE_FORMAT)
        to_str = self.to_date.date().toString(DATE_FORMAT)
        self._searched_range = (from_str, to_str)
        run_async(
            self.state.client.get_my_time_entries,
            self._user_id,
            from_str,
            to_str,
            on_success=self._on_load_success,
            on_error=self._on_search_error,
        )

    def _on_wp_status_loaded(self, wps: dict) -> None:
        self._wp_status = wps or {}
        # สถานะมาถึงหลังผลค้นหา (คนละ worker) ต้องเติม dropdown แล้วกรองใหม่ ไม่ใช่แค่วาดตาราง
        self._rebuild_status_filter_options()
        self._apply_filters()

    def _on_wp_status_error(self, message: str) -> None:
        # ตารางยังใช้งานได้ปกติ แค่คอลัมน์สถานะจะว่าง — ไม่เด้ง dialog รบกวนการดูประวัติ
        self._wp_status = {}
        print(f"โหลดสถานะ Work Package ไม่สำเร็จ: {message}")
        self._rebuild_status_filter_options()
        self._apply_filters()

    def _rebuild_status_filter_options(self) -> None:
        """เติมเฉพาะสถานะที่โผล่จริงในผลค้นหา พร้อมจำนวน — ไม่เอาทั้ง 17 สถานะของ instance มาให้เลือก"""
        counts: dict[str, int] = {}
        for entry in self._entries:
            wp = self._wp_status.get(entry["work_package_id"])
            name = (wp or {}).get("status")
            if name:
                counts[name] = counts.get(name, 0) + 1

        previous = self.status_filter_combo.currentData()
        self.status_filter_combo.blockSignals(True)
        self.status_filter_combo.clear()
        self.status_filter_combo.addItem("ทุกสถานะ", None)
        for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            self.status_filter_combo.addItem(f"{name} ({n})", name)
        fit_combo_popup(self.status_filter_combo)
        index = self.status_filter_combo.findData(previous) if previous else 0
        self.status_filter_combo.setCurrentIndex(max(index, 0))
        self.status_filter_combo.blockSignals(False)

    def _on_statuses_loaded(self, statuses: list) -> None:
        self._statuses = statuses or []

        self.status_combo.blockSignals(True)
        self.status_combo.clear()
        for status in self._statuses:
            self.status_combo.addItem(status["name"], status["id"])
        fit_combo_popup(self.status_combo)
        self.status_combo.setCurrentIndex(self._match_status_index(DEFAULT_STATUS_NAMES, 0))
        self.status_combo.blockSignals(False)

        # "จากสถานะ" มีตัวเลือก "ทุกสถานะ" ด้วย แต่ตั้งต้นเป็น TO DO เพื่อไม่ให้กดทีเดียวแล้ว
        # ลากงานที่ปิดไปแล้วกลับมา (เคสจริง: WP เดือนหนึ่งมีทั้ง TO DO และ COMPLETE ปนกัน)
        self.from_status_combo.blockSignals(True)
        self.from_status_combo.clear()
        self.from_status_combo.addItem("ทุกสถานะ", None)
        for status in self._statuses:
            self.from_status_combo.addItem(status["name"], status["id"])
        fit_combo_popup(self.from_status_combo)
        from_index = self._match_status_index(DEFAULT_FROM_STATUS_NAMES, -1)
        self.from_status_combo.setCurrentIndex(0 if from_index < 0 else from_index + 1)
        self.from_status_combo.blockSignals(False)

        self.status_combo.setEnabled(bool(self._statuses))
        self.from_status_combo.setEnabled(bool(self._statuses))
        self._update_status_btn_enabled()
        self._render_rows(self._filtered)  # คอลัมน์สถานะในตารางต้องรอรายชื่อนี้ถึงจะสร้าง combo ได้

    def _match_status_index(self, wanted_names: tuple[str, ...], fallback: int) -> int:
        """หา index ใน self._statuses ที่ชื่อตรงกับ wanted_names — ตรงเป๊ะก่อน แล้วค่อยชื่อที่ขึ้นต้นด้วย

        ที่ต้องเทียบแบบขึ้นต้นและเลือก "ชื่อสั้นที่สุด" เพราะ instance จริงมีทั้ง "IN PROCESS",
        "IN PROGRESS RESLOVED" และ "IN PROGRESS INTERNAL TEST" — ถ้าใช้ substring เฉยๆ
        จะไปโดนสถานะเฉพาะทางที่ยาวกว่าแทนตัวกลางๆ ที่ต้องการ
        """
        names = [s["name"].strip().lower() for s in self._statuses or []]
        for wanted in wanted_names:
            if wanted in names:
                return names.index(wanted)
        candidates = [i for i, n in enumerate(names) if n.startswith(wanted_names)]
        if candidates:
            return min(candidates, key=lambda i: len(names[i]))
        return fallback

    def _on_statuses_error(self, message: str) -> None:
        # ปล่อยเป็น None ไว้เพื่อให้กด "ค้นหา" ครั้งถัดไปลองโหลดใหม่ได้เอง
        self._statuses = None
        self.status_combo.setEnabled(False)
        self._update_status_btn_enabled()
        print(f"โหลดรายชื่อสถานะไม่สำเร็จ: {message}")

    def _update_status_btn_enabled(self) -> None:
        self.status_btn.setEnabled(bool(self._statuses) and bool(self._filtered))

    # -- สถานะ "กำลังทำงาน" ของแต่ละปุ่ม (ดู gui/busy.py) ------------------------

    def _set_search_busy(self, running: bool) -> None:
        set_button_busy(self.search_btn, running, "กำลังค้นหา...", "ค้นหา")
        self.toolbar_progress.setVisible(running)

    def _set_status_busy(self, running: bool, text: str = "กำลังปรับสถานะ...") -> None:
        set_button_busy(self.status_btn, running, text, "↻ ปรับสถานะงาน")
        self.status_progress.setVisible(running)
        if not running:
            # ปุ่มนี้กดได้เฉพาะตอนมีสถานะกับผลค้นหาอยู่ — set_button_busy เปิดคืนให้เสมอ ต้องคิดใหม่
            self._update_status_btn_enabled()

    def _set_export_busy(self, running: bool) -> None:
        set_button_busy(self.export_btn, running, "กำลังบันทึก...", "⬇ ส่งออก Excel")
        self.toolbar_progress.setVisible(running)
        if not running:
            self.export_btn.setEnabled(bool(self._entries))

    def _on_load_success(self, entries: list) -> None:
        self._set_search_busy(False)
        self._entries = entries or []
        # ข้อมูลคนละชุดแล้ว คีย์ที่พับไว้ไม่มีความหมายอีก (ต่างจากการกรอง/ค้นหาที่ต้องคงไว้)
        self._collapsed.clear()
        self._render_coverage()
        self.export_btn.setEnabled(bool(self._entries))

        # ดึงสถานะ/ผู้สร้างของทุก WP ที่โผล่ในผลค้นหา ยิงชุดเดียว แล้วค่อย re-render ตารางเมื่อได้ผล
        # (เช็ค client เผื่อ logout ระหว่างที่ผลค้นหายังค้างอยู่ — แบบเดียวกับ _on_activities_loaded)
        self._wp_status = {}
        wp_ids = sorted({e["work_package_id"] for e in self._entries if e["work_package_id"]})
        if wp_ids and self.state.client:
            run_async(
                self.state.client.get_work_packages_by_ids,
                wp_ids,
                on_success=self._on_wp_status_loaded,
                on_error=self._on_wp_status_error,
            )

        self._rebuild_wp_filter_options()
        self.search_edit.blockSignals(True)
        self.search_edit.clear()
        self.search_edit.blockSignals(False)
        self.wp_filter_combo.blockSignals(True)
        self.wp_filter_combo.setCurrentIndex(0)
        self.wp_filter_combo.blockSignals(False)
        # ค้นหาใหม่ = เริ่มที่ "ทุกสถานะ" เสมอ (รายการจะถูกเติมอีกทีเมื่อสถานะโหลดเสร็จ)
        self.status_filter_combo.blockSignals(True)
        self.status_filter_combo.clear()
        self.status_filter_combo.addItem("ทุกสถานะ", None)
        self.status_filter_combo.blockSignals(False)
        self._filter_timer.stop()
        self._apply_filters()

    def _on_search_error(self, message: str) -> None:
        self._set_search_busy(False)
        self._entries = []
        self._filtered = []
        self._wp_status = {}
        self.export_btn.setEnabled(False)
        self._update_status_btn_enabled()
        self._show_empty_message(f"โหลดข้อมูลไม่สำเร็จ: {message}")
        self.summary_totals_label.setText("")
        self.project_totals_label.hide()
        self.coverage_label.hide()
        self._clear_breakdown_chips()
        QMessageBox.warning(self, "Timesheet Sync", f"โหลดข้อมูลไม่สำเร็จ: {message}")

    def _rebuild_wp_filter_options(self) -> None:
        """สร้างรายชื่องานใน dropdown ใหม่จาก self._entries เรียงตามชั่วโมงรวมมากไปน้อย"""
        per_wp = self._aggregate_by_wp(self._entries)
        self.wp_filter_combo.blockSignals(True)
        self.wp_filter_combo.clear()
        self.wp_filter_combo.addItem("ทั้งหมด", None)
        for wp_id, bucket in sorted(per_wp.items(), key=lambda kv: -kv[1]["hours"]):
            if wp_id is None:
                continue  # ไม่มี WP ให้ระบุ กรองเฉพาะเจาะจงไม่ได้ ปล่อยให้รวมอยู่ใน "ทั้งหมด"
            self.wp_filter_combo.addItem(bucket["label"], wp_id)
        fit_combo_popup(self.wp_filter_combo)
        self.wp_filter_combo.blockSignals(False)


    @staticmethod
    def _aggregate_by_project(entries: list[dict]) -> list[tuple[str, float]]:
        """[(ชื่อโปรเจกต์, ชั่วโมงรวม)] เรียงมากไปน้อย — โครงเดียวกับ _aggregate_by_wp คนละแกน"""
        totals: dict[str, float] = {}
        for entry in entries:
            name = (entry.get("project") or "").strip() or "(ไม่ระบุโปรเจกต์)"
            totals[name] = totals.get(name, 0.0) + (entry.get("hours") or 0)
        return sorted(totals.items(), key=lambda kv: -kv[1])

    def _render_project_totals(self, entries: list[dict]) -> None:
        """ชั่วโมงแยกตามโปรเจกต์ของชุดที่แสดงอยู่ — เดินตามตัวกรองเหมือนบรรทัดยอดรวมข้างบน

        (ต่างจาก _render_coverage ที่เป็นเรื่องของปฏิทินทั้งช่วง จึงไม่สนตัวกรอง)
        """
        totals = self._aggregate_by_project(entries)
        if len(totals) < 2:
            # โปรเจกต์เดียวไม่มีอะไรให้ "แยก" บรรทัดยอดรวมข้างบนบอกครบแล้ว
            self.project_totals_label.hide()
            return
        parts = " · ".join(f"{name} {hours:.1f} ชม." for name, hours in totals)
        self.project_totals_label.setText(f"โปรเจกต์: {parts}")
        self.project_totals_label.show()

    def _render_coverage(self) -> None:
        """วันทำงานไหนในช่วงที่ค้นหายังลงไม่ครบ และวันไหนลงเกิน

        คิดจาก _entries **ทั้งชุด** กับช่วงที่ค้นหาจริง ไม่ใช่ชุดหลังกรอง — ถ้าใช้ชุดหลังกรอง
        พอผู้ใช้กรองเหลืองานเดียวมันจะประกาศว่าเกือบทุกวันขาด ทั้งที่ข้อมูลครบดีอยู่

        จึงเรียกที่ _on_load_success ครั้งเดียวต่อการค้นหา ไม่ได้เรียกใน _render_rows
        """
        if not self._searched_range:
            self.coverage_label.hide()
            return

        date_from, date_to = self._searched_range
        target = (self.state.config.get("worklog") or {}).get("hours_per_day") or 8
        result = summarize_coverage(self._entries, date_from, date_to, target)

        lines = []
        missing, over = result["missing"], result["over"]
        if missing:
            days = self._preview_days(
                [f"{d['date']} ({d['weekday']})" for d in missing], len(missing)
            )
            lines.append(
                f'<span style="color: {COLORS["warn"]};">'
                f"⚠ ยังลงไม่ครบ {len(missing)} วัน: {days}</span>"
            )
        if over:
            days = self._preview_days(
                [f"{d['date']} ({d['hours']:g} ชม.)" for d in over], len(over)
            )
            lines.append(
                f'<span style="color: {COLORS["error"]};">'
                f"⚠ ลงเกิน {result['target']:g} ชม. {len(over)} วัน: {days}</span>"
            )

        if not lines:
            self.coverage_label.hide()
            return
        self.coverage_label.setText("<br>".join(lines))
        self.coverage_label.show()

    @staticmethod
    def _preview_days(labels: list[str], total: int) -> str:
        """เดือนที่ลงไม่ครบทั้งเดือนจะได้ 20 กว่าวัน ยาวเกินกว่าจะอ่านออกทั้งแถบ"""
        text = " · ".join(labels[:MAX_PREVIEW_LINES])
        if total > MAX_PREVIEW_LINES:
            text += f" … และอีก {total - MAX_PREVIEW_LINES} วัน"
        return text

    def _aggregate_by_wp(self, entries: list[dict]) -> dict:
        per_wp: dict = {}
        for entry in entries:
            wp_id = entry["work_package_id"]
            label = "(ไม่ระบุงาน)"
            if wp_id:
                label = f"#{wp_id} {entry['work_package_subject'] or ''}".strip()
            bucket = per_wp.setdefault(wp_id, {"label": label, "hours": 0.0})
            bucket["hours"] += entry["hours"] or 0

        return per_wp

    def _apply_filters(self) -> None:
        query = self.search_edit.text().strip().lower()
        wp_filter = self.wp_filter_combo.currentData()
        status_filter = self.status_filter_combo.currentData()
        filtered = []
        for entry in self._entries:
            if wp_filter is not None and entry["work_package_id"] != wp_filter:
                continue
            if status_filter is not None:
                wp = self._wp_status.get(entry["work_package_id"])
                if (wp or {}).get("status") != status_filter:
                    continue
            if query:
                haystack = " ".join(
                    str(v) for v in (
                        entry.get("work_package_id"),
                        entry.get("work_package_subject"),
                        entry.get("project"),
                        entry.get("comment"),
                    ) if v
                ).lower()
                if query not in haystack:
                    continue
            filtered.append(entry)
        self._filtered = filtered
        self._update_status_btn_enabled()
        self._render_rows(filtered)

    def _render_rows(self, entries: list[dict]) -> None:
        if not entries:
            if not self._entries:
                self._show_empty_message("ไม่พบข้อมูลในช่วงวันที่ที่เลือก")
            else:
                self._show_empty_message("ไม่พบรายการที่ตรงกับตัวกรอง")
            self.summary_totals_label.setText("")
            # ไม่แตะ coverage_label — มันเป็นเรื่องของทั้งช่วงที่ค้นหา ไม่ใช่ของชุดหลังกรอง
            self.project_totals_label.hide()
            self._clear_breakdown_chips()
            return

        per_date: dict[str, list[dict]] = {}
        for entry in entries:
            per_date.setdefault(entry["date"] or "", []).append(entry)
        date_keys = sorted(per_date.keys())  # ไม่พึ่งลำดับที่ API ส่งมา ถึงจะขอ sortBy=spentOn asc แล้วก็ตาม
        per_date_tasks = {d: self._group_by_task(es) for d, es in per_date.items()}

        # รีเซ็ต row count เป็น 0 ก่อนเสมอ ไม่งั้น cell widget ของ empty-state message (ที่ฝัง
        # ด้วย setCellWidget ในแถว 0) จะค้างทับแถวแรกอยู่ เพราะแถวข้อมูลจริงใช้ setItem ซึ่งไม่ไป
        # แทนที่ cell widget เดิม
        self.table.clearSpans()
        self.table.setRowCount(0)
        self._task_rows = {}
        self.table.setRowCount(
            sum(
                1 + sum(1 if len(g) == 1 else 1 + len(g) for g in groups)
                for groups in per_date_tasks.values()
            )
        )

        total_hours = 0.0
        self.table.setUpdatesEnabled(False)
        try:
            row = 0
            for date_key in date_keys:
                day_entries = per_date[date_key]
                day_total = sum(e["hours"] or 0 for e in day_entries)
                total_hours += day_total

                self.table.setSpan(row, 0, 1, len(COLUMN_LABELS))
                # วันที่เป็นสีตัวอักษรปกติ ยอดรวมเป็นสีจาง — เดิมทั้งแถบเป็นพื้นชมพูเข้มกว่าทุกอย่าง
                # ในตาราง เลยแย่งสายตาไปจากเนื้อหาที่มันควรจะแค่คั่น
                header = QLabel(
                    f'<span style="color: {COLORS["text"]}; font-weight: 600;">'
                    f"{date_key or '(ไม่ระบุวันที่)'}</span>"
                    f'<span style="color: {COLORS["text_muted"]};">'
                    f"    รวม {day_total:.2f} ชม. · {len(day_entries)} รายการ</span>"
                )
                header.setStyleSheet(
                    f"background: {COLORS['bg_subtle']}; padding: 6px 14px; font-size: 13px; "
                    f"border-bottom: 1px solid {COLORS['grid']};"
                )
                self.table.setCellWidget(row, 0, header)
                self.table.setRowHeight(row, 32)
                row += 1

                for group in per_date_tasks[date_key]:
                    if len(group) == 1:
                        # งานที่ลงครั้งเดียวในวันนั้น ไม่มีอะไรให้ยุบ — แถวเดียวเหมือนเดิมทุกอย่าง
                        self._put_entry_row(row, group[0])
                        self._install_status_cell(row, group[0]["work_package_id"])
                        row += 1
                        continue

                    key = (date_key, group[0]["work_package_id"])
                    task_row = row
                    self._put_task_row(row, group, key in self._collapsed)
                    self._install_status_cell(row, group[0]["work_package_id"])
                    # ย้อมทีหลังเสมอ — _install_status_cell เขียนทับ item ของคอลัมน์สถานะได้
                    self._tint_row(row, COLORS["accent_bg"])
                    row += 1
                    sub_rows = []
                    for entry in group:
                        self._put_subrow(row, entry)
                        self._tint_row(row, NESTED_ROW_BG)
                        sub_rows.append(row)
                        row += 1
                    self._task_rows[task_row] = (key, sub_rows)
        finally:
            self.table.setUpdatesEnabled(True)

        # ซ่อนหลัง setUpdatesEnabled — setRowHidden ระหว่างที่ปิด update ไว้ Qt ไม่คิด geometry
        # ใหม่ให้ ผลคือแถวที่พับยังกินที่อยู่จนกว่าจะมีอะไรมากระตุ้นให้วาดใหม่
        for key, sub_rows in self._task_rows.values():
            hidden = key in self._collapsed
            for r in sub_rows:
                self.table.setRowHidden(r, hidden)

        self.summary_totals_label.setText(f"รวม {total_hours:.1f} ชั่วโมง จาก {len(entries)} รายการ")
        self._render_project_totals(entries)
        self._render_breakdown_chips(entries)


    # -- การจัดกลุ่มตามงาน --------------------------------------------------

    @staticmethod
    def _group_by_task(entries: list[dict]) -> list[list[dict]]:
        """รวม time entry ของวันเดียวกันที่เป็นงานเดียวกันไว้ด้วยกัน เรียงกลุ่มตามชั่วโมงมากไปน้อย

        คีย์เป็น work_package_id ตรงๆ ไม่ใช่ชื่องาน — ชื่อซ้ำกันข้ามโปรเจกต์ได้ แต่ id ไม่ซ้ำ
        แถวที่ไม่มี WP (id เป็น None) แยกกลุ่มกันเองทีละแถว ยุบรวมไม่ได้เพราะไม่รู้ว่าเป็นงานเดียวกันไหม
        """
        groups: dict = {}
        loose: list[list[dict]] = []
        for entry in entries:
            wp_id = entry["work_package_id"]
            if wp_id is None:
                loose.append([entry])
            else:
                groups.setdefault(wp_id, []).append(entry)
        return sorted(
            list(groups.values()) + loose,
            key=lambda g: -sum(e["hours"] or 0 for e in g),
        )

    @staticmethod
    def _wp_label(entry: dict) -> str:
        if not entry["work_package_id"]:
            return ""
        return f"#{entry['work_package_id']} {entry['work_package_subject'] or ''}".strip()

    def _put_entry_row(self, row: int, entry: dict) -> None:
        """แถวปกติ 1 time entry = 1 แถว (เหมือนก่อนจะมีการจัดกลุ่มตามงาน)"""
        values = [
            entry["date"] or "",
            entry["project"] or "",
            self._wp_label(entry),
            entry["activity"] or "",
            f"{entry['hours']:.2f}",
            entry["comment"] or "",
        ]
        for col, text in enumerate(values):
            self.table.setItem(row, col, QTableWidgetItem(text))

    def _put_task_row(self, row: int, group: list[dict], collapsed: bool) -> None:
        """หัวกลุ่มของงานที่ลงหลายครั้งในวันเดียว — โชว์ชั่วโมงรวมของงานนั้นทั้งวัน

        ตัวเลขในคอลัมน์ชั่วโมงคือผลรวม ไม่ใช่ค่าของ entry ใดตัวหนึ่ง จึงทำตัวหนาให้ต่างจาก
        แถวย่อยข้างล่างที่เป็นตัวเลขของแต่ละครั้ง
        """
        first = group[0]
        hours = sum(e["hours"] or 0 for e in group)
        activities = {(e["activity"] or "").strip() for e in group if (e["activity"] or "").strip()}
        activity = activities.pop() if len(activities) == 1 else "หลายกิจกรรม"
        values = [
            first["date"] or "",
            first["project"] or "",
            self._wp_label(first),
            activity,
            f"{hours:.2f}",
            f"{len(group)} ครั้ง",
        ]
        bold = QFont()
        bold.setBold(True)
        clay = QBrush(QColor(COLORS["accent_text"]))
        tip = "คลิกเพื่อกางรายละเอียด" if collapsed else "คลิกเพื่อพับรายละเอียด"
        for col, text in enumerate(values):
            item = QTableWidgetItem(text)
            if col in (2, 4):  # ชื่องานกับชั่วโมงรวม คือสองอย่างที่ต้องเด่นกว่าแถวย่อย
                item.setFont(bold)
            if col == 2:
                # สี clay + ลูกศร = สองสัญญาณว่าแถวนี้กดได้ ตัวหนาอย่างเดียวแยกจากแถวเดี่ยวไม่ออก
                item.setForeground(clay)
                item.setIcon(_chevron_icon(not collapsed))
            item.setToolTip(tip)
            self.table.setItem(row, col, item)

    def _put_subrow(self, row: int, entry: dict) -> None:
        """รายละเอียดใต้หัวกลุ่ม — ครั้งนี้ทำอะไร กี่ชั่วโมง

        เว้นวันที่/โปรเจกต์/สถานะไว้ว่าง เพราะซ้ำกับหัวกลุ่มทุกตัว การเขียนซ้ำทำให้ตาไล่ยาก
        (สถานะยังเว้นเพราะเป็น dropdown ของ WP เดียวกัน ถ้าวางทุกแถวจะได้ dropdown ซ้ำ N ตัว
        ที่แก้ค่าตัวเดียวกัน — สับสนและเปลืองด้วย)
        """
        muted = QBrush(QColor(COLORS["text_muted"]))
        for col, text in enumerate(["", "", "", entry["activity"] or "",
                                    f"{entry['hours']:.2f}", ""]):
            item = QTableWidgetItem(text)
            item.setForeground(muted)
            self.table.setItem(row, col, item)
        self.table.setItem(row, STATUS_COLUMN, QTableWidgetItem(""))

        # วันที่กับโปรเจกต์ซ้ำกับแถวงานทุกตัว เขียนซ้ำแล้วตาไล่ยาก — ยุบสองช่องเป็นช่องเดียว
        # แล้ววางเส้นที่ลากลงมาจากแถวงานแทน
        self.table.setSpan(row, 0, 1, 2)
        self.table.setCellWidget(row, 0, self._rail_widget())

        # รายละเอียดไปอยู่ในคอลัมน์ที่กว้างที่สุด (คอลัมน์เดียวที่ Stretch) ไม่ใช่คอลัมน์หมายเหตุ
        # ที่แคบ — ข้อความจริงยาวกว่านั้นเสมอ ใช้ QLabel ไม่ใช่ item เพราะต้องเยื้องเป็น px
        detail = QLabel(entry["comment"] or "—")
        detail.setContentsMargins(SUBROW_INDENT, 0, 10, 0)
        detail.setStyleSheet(f"color: {COLORS['text_muted']};")
        detail.setToolTip(entry["comment"] or "")
        self.table.setCellWidget(row, 2, detail)

    @staticmethod
    def _rail_widget() -> QWidget:
        """เส้นแนวตั้งชิดขวาของช่องวันที่+โปรเจกต์ที่ยุบไว้ — ชิดขวาเพื่อให้เส้นอยู่ติดคอลัมน์งาน"""
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch()
        rail = QWidget()
        rail.setFixedWidth(RAIL_WIDTH)
        rail.setStyleSheet(f"background: {COLORS['accent_border']};")
        layout.addWidget(rail)
        return wrapper

    def _tint_row(self, row: int, color: str) -> None:
        """ย้อมพื้นทั้งแถว — cell widget ในแถวโปร่งอยู่แล้ว (ดูกฎ QTableWidget QWidget ใน theme)
        พื้นของ item ข้างล่างจึงทะลุขึ้นมาเห็น ส่วน QComboBox สถานะมีพื้นขาวของมันเอง"""
        brush = QBrush(QColor(color))
        for col in range(len(COLUMN_LABELS)):
            item = self.table.item(row, col)
            if item is None:
                item = QTableWidgetItem("")
                self.table.setItem(row, col, item)
            item.setBackground(brush)

    def _on_cell_clicked(self, row: int, _column: int) -> None:
        """พับ/กางแถวย่อยของหัวกลุ่มที่ถูกคลิก — แถวอื่นคลิกแล้วไม่ทำอะไร

        ซ่อนด้วย setRowHidden ไม่ใช่วาดตารางใหม่ เพราะการวาดใหม่ต้องสร้าง QComboBox สถานะ
        ของทุกแถวขึ้นมาใหม่หมด ทั้งที่ข้อมูลไม่ได้เปลี่ยนอะไรเลย
        """
        entry = self._task_rows.get(row)
        if entry is None:
            return
        key, sub_rows = entry
        collapsed = key not in self._collapsed
        if collapsed:
            self._collapsed.add(key)
        else:
            self._collapsed.discard(key)
        for r in sub_rows:
            self.table.setRowHidden(r, collapsed)

        item = self.table.item(row, 2)
        if item:
            item.setIcon(_chevron_icon(not collapsed))
        tip = "คลิกเพื่อกางรายละเอียด" if collapsed else "คลิกเพื่อพับรายละเอียด"
        for col in range(len(COLUMN_LABELS)):
            cell = self.table.item(row, col)
            if cell:
                cell.setToolTip(tip)

    def _install_status_cell(self, row: int, wp_id: int | None) -> None:
        """วาง dropdown สถานะของ WP ลงในแถวนั้น (หรือข้อความบอกเหตุผลถ้าแก้ไม่ได้)"""
        wp = self._wp_status.get(wp_id) if wp_id else None
        if not wp or not self._statuses:
            self.table.setItem(row, STATUS_COLUMN, QTableWidgetItem(""))
            return

        combo = QComboBox()
        combo.blockSignals(True)  # กันไม่ให้ยิง signal ระหว่างเติมค่าตั้งต้น
        for status in self._statuses:
            combo.addItem(status["name"], status["id"])
        current = (wp.get("status") or "").strip().lower()
        index = next(
            (i for i, s in enumerate(self._statuses) if s["name"].strip().lower() == current), -1
        )
        if index < 0:  # สถานะปัจจุบันไม่อยู่ในรายการ (ไม่ควรเกิด) — โชว์ไว้แต่ห้ามแก้
            combo.addItem(wp.get("status") or "?", None)
            index = combo.count() - 1
        combo.setCurrentIndex(index)
        combo.blockSignals(False)
        fit_combo_popup(combo)

        not_mine = self.mine_only_check.isChecked() and wp.get("author_id") != self._user_id
        if not_mine:
            combo.setEnabled(False)
            combo.setToolTip(f"งานนี้สร้างโดย {wp.get('author') or 'คนอื่น'} "
                             'ติ๊ก "เฉพาะงานที่ฉันสร้าง" ออกถ้าต้องการแก้')
        else:
            combo.setCursor(Qt.PointingHandCursor)
            # ใช้ activated (ผู้ใช้เลือกเองเท่านั้น) ไม่ใช่ currentIndexChanged ซึ่งยิงตอนเติมค่า
            # ด้วย — ไม่งั้นแค่โหลดตารางก็จะ PATCH ทุกแถวโดยไม่ได้ตั้งใจ
            combo.activated.connect(lambda i, w=wp, c=combo: self._on_row_status_activated(w, c, i))

        self.table.setCellWidget(row, STATUS_COLUMN, combo)

    def _on_row_status_activated(self, wp: dict, combo: QComboBox, index: int) -> None:
        status_id = combo.itemData(index)
        new_name = combo.itemText(index)
        old_name = wp.get("status") or "-"
        if status_id is None or new_name.strip().lower() == old_name.strip().lower():
            return

        answer = QMessageBox.question(
            self,
            "Timesheet Sync",
            f"เปลี่ยนสถานะงานนี้หรือไม่?\n\n"
            f"#{wp['id']} {wp.get('subject') or ''}\n{old_name} → {new_name}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            self._render_rows(self._filtered)  # ดีดค่าใน combo กลับเป็นของเดิม
            return

        combo.setEnabled(False)
        run_async(
            self.state.client.update_work_package_status,
            wp["id"],
            status_id,
            on_success=lambda updated, wid=wp["id"]: self._on_row_status_updated(wid, updated),
            on_error=self._on_row_status_error,
        )

    def _on_row_status_updated(self, wp_id: int, updated: dict) -> None:
        # เขียนทับด้วยของที่ API ส่งกลับมา แล้ว render ใหม่ — แถวอื่นที่ผูก WP เดียวกันอัปเดตตามด้วย
        self._wp_status[wp_id] = updated
        self._render_rows(self._filtered)

    def _on_row_status_error(self, message: str) -> None:
        self._render_rows(self._filtered)  # ดีดค่ากลับก่อน แล้วค่อยแจ้ง
        QMessageBox.warning(self, "Timesheet Sync", f"ปรับสถานะไม่สำเร็จ: {message}")

    def _clear_breakdown_chips(self) -> None:
        while self.breakdown_layout.count():
            item = self.breakdown_layout.takeAt(0)
            widget = item.widget()
            if widget:
                # takeAt() ตัดออกจาก layout แต่ widget ยังโผล่ค้างที่ตำแหน่งเดิมจนกว่า deleteLater()
                # จะเก็บจริง (รอบ event loop ถัดไป) — hide() ทันทีกันแถบเก่าค้างทับตอน render รัวๆ
                # (พิมพ์ในช่องค้นหาเร็วๆ เรียก render ใหม่ทุกตัวอักษร)
                widget.hide()
                widget.deleteLater()

    def _render_breakdown_chips(self, entries: list[dict]) -> None:
        self._clear_breakdown_chips()
        per_wp = self._aggregate_by_wp(entries)
        active_wp = self.wp_filter_combo.currentData()
        top = sorted(
            ((wp_id, b) for wp_id, b in per_wp.items() if wp_id is not None),
            key=lambda kv: -kv[1]["hours"],
        )[:5]
        for wp_id, bucket in top:
            full = f"{bucket['label']}: {bucket['hours']:.1f} ชม."
            chip = QPushButton(self._chip_text(bucket["label"], bucket["hours"]))
            chip.setObjectName("filterButton")
            chip.setProperty("active", wp_id == active_wp)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setToolTip(full)  # ชื่อเต็มยังต้องอ่านได้ ต่อให้ชิปตัดไป
            chip.clicked.connect(lambda _=False, wid=wp_id: self._select_wp_filter(wid))
            self.breakdown_layout.addWidget(chip)
        # ไม่มี addStretch — FlowLayout ชิดซ้ายอยู่แล้ว และ QLayout ฐานไม่มีเมธอดนี้

    @staticmethod
    def _chip_text(label: str, hours: float) -> str:
        """ตัดเฉพาะส่วนชื่องาน ไม่ตัดทั้งก้อน — จำนวนชั่วโมงคือสาระของชิป ต้องเห็นเสมอ

        แม้จะไหลลงบรรทัดใหม่ได้แล้ว ชื่องานเดี่ยวๆ ที่ยาวกว่าความกว้างหน้าต่างก็ยังทำให้ Qt
        ย่อข้อความทิ้งท้ายจนชั่วโมงหายอยู่ดี จึงต้องคุมความยาวที่ต้นทางด้วย"""
        if len(label) > CHIP_LABEL_MAX_CHARS:
            label = label[: CHIP_LABEL_MAX_CHARS - 1].rstrip() + "…"
        return f"{label}: {hours:.1f} ชม."

    def _select_wp_filter(self, wp_id: int) -> None:
        index = self.wp_filter_combo.findData(wp_id)
        if index >= 0:
            self.wp_filter_combo.setCurrentIndex(index)  # triggers _apply_filters via signal

    def _show_empty_message(self, message: str) -> None:
        # รีเซ็ตเป็น 0 ก่อนเสมอ กัน QTableWidgetItem เดิมจากแถวข้อมูลจริง (คอลัมน์ 1 เป็นต้นไป ซึ่งไม่มี
        # cell widget ทับ) โผล่ผ่านมาให้เห็นใต้ label ของแถวว่าง
        self.table.setRowCount(0)
        self.table.setRowCount(1)
        self._task_rows = {}  # เลขแถวเก่าไม่ตรงกับตารางนี้แล้ว
        self.table.setSpan(0, 0, 1, len(COLUMN_LABELS))
        label = QLabel(message)
        label.setObjectName("fieldLabel")
        label.setAlignment(Qt.AlignCenter)
        self.table.setCellWidget(0, 0, label)
        self.table.setRowHeight(0, 60)

    # -- ปรับสถานะ Work Package -------------------------------------------------

    def _on_change_status_clicked(self) -> None:
        if not self.state.client or not self._filtered:
            return
        status_id = self.status_combo.currentData()
        if status_id is None:
            return

        wp_ids = sorted({e["work_package_id"] for e in self._filtered if e["work_package_id"]})
        if not wp_ids:
            QMessageBox.information(
                self, "Timesheet Sync", "รายการที่แสดงอยู่ไม่มีรายการไหนผูกกับ Work Package"
            )
            return

        self._set_status_busy(True, "กำลังตรวจสอบ...")
        # ดึงสถานะปัจจุบันของทุก WP ในคำขอเดียว เพื่อเอาไปแสดงในกล่องยืนยันและข้ามตัวที่ตรงอยู่แล้ว
        run_async(
            self.state.client.get_work_packages_by_ids,
            wp_ids,
            on_success=lambda wps, sid=status_id: self._on_wps_loaded(wps, sid),
            on_error=self._on_status_error,
        )

    def _on_wps_loaded(self, wps: dict, status_id: int) -> None:
        self._set_status_busy(False)

        target_name = self.status_combo.currentText()
        from_name = self.from_status_combo.currentData() and self.from_status_combo.currentText()

        def status_of(wp):
            return (wp.get("status") or "").strip().lower()

        candidates = list(wps.values())
        not_mine = 0
        if self.mine_only_check.isChecked():
            mine = [wp for wp in candidates if wp.get("author_id") == self._user_id]
            not_mine = len(candidates) - len(mine)
            candidates = mine

        targets = [wp for wp in candidates if status_of(wp) != target_name.strip().lower()]
        if from_name:  # None = "ทุกสถานะ"
            targets = [wp for wp in targets if status_of(wp) == from_name.strip().lower()]
        skipped = len(candidates) - len(targets)

        if not targets:
            if from_name:
                reason = f'ไม่มีงานที่อยู่สถานะ "{from_name}" ในรายการที่แสดงอยู่'
            else:
                reason = f'งานที่แสดงอยู่ทั้งหมดอยู่สถานะ "{target_name}" แล้ว ไม่มีอะไรต้องเปลี่ยน'
            if not_mine:
                reason += f"\n(กันงานของคนอื่นออกไป {not_mine} รายการ)"
            QMessageBox.information(self, "Timesheet Sync", reason)
            return

        targets.sort(key=lambda wp: wp["id"])
        preview = "\n".join(
            f"#{wp['id']} {wp['subject'] or ''} ({wp.get('status') or '-'} → {target_name})"
            for wp in targets[:MAX_PREVIEW_LINES]
        )
        if len(targets) > MAX_PREVIEW_LINES:
            preview += f"\n... และอีก {len(targets) - MAX_PREVIEW_LINES} รายการ"

        detail = f'จะปรับสถานะงาน {len(targets)} รายการเป็น "{target_name}"'
        if from_name:
            detail += f' (เฉพาะที่อยู่สถานะ "{from_name}")'
        if skipped:
            detail += f"\nข้าม {skipped} รายการที่สถานะไม่เข้าเงื่อนไข"
        if not_mine:
            detail += f"\nกันงานที่คนอื่นสร้างออกไป {not_mine} รายการ"

        answer = QMessageBox.question(
            self,
            "Timesheet Sync",
            f"{detail}\n\n{preview}\n\nยืนยันหรือไม่?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        self._set_status_busy(True)
        run_async(
            bulk_update_status,
            self.state.client,
            [wp["id"] for wp in targets],
            status_id,
            on_success=self._on_status_updated,
            on_error=self._on_status_error,
        )

    def _on_status_updated(self, result: dict) -> None:
        self._set_status_busy(False)

        success = result["success"]
        failed = result["failed"]
        if not failed:
            QMessageBox.information(
                self,
                "Timesheet Sync",
                f'ปรับสถานะสำเร็จ {success} รายการ\n\nกด "ค้นหา" อีกครั้งเพื่อโหลดข้อมูลล่าสุด',
            )
            return

        errors = [r for r in result["results"] if not r["ok"]]
        detail = "\n".join(f"#{r['wp_id']}: {r['message']}" for r in errors[:MAX_PREVIEW_LINES])
        if len(errors) > MAX_PREVIEW_LINES:
            detail += f"\n... และอีก {len(errors) - MAX_PREVIEW_LINES} รายการ"
        QMessageBox.warning(
            self,
            "Timesheet Sync",
            f"ปรับสถานะสำเร็จ {success} รายการ ล้มเหลว {failed} รายการ\n\n{detail}",
        )

    def _on_status_error(self, message: str) -> None:
        self._set_status_busy(False)
        QMessageBox.warning(self, "Timesheet Sync", f"ปรับสถานะไม่สำเร็จ: {message}")

    def _on_export_clicked(self) -> None:
        if not self._entries:
            return
        from_str = self.from_date.date().toString(DATE_FORMAT)
        to_str = self.to_date.date().toString(DATE_FORMAT)
        default_name = f"timesheet_export_{from_str}_to_{to_str}.xlsx"
        path, _ = QFileDialog.getSaveFileName(
            self, "บันทึกไฟล์ Excel", default_name, "Excel files (*.xlsx)"
        )
        if not path:
            return

        self._set_export_busy(True)
        run_async(
            export_time_entries_to_excel,
            self._entries,
            self.state.config["excel"],
            path,
            self._user_email,
            on_success=lambda _r, p=path: self._on_export_success(p),
            on_error=self._on_export_error,
        )

    def _on_export_success(self, path: str) -> None:
        self._set_export_busy(False)
        QMessageBox.information(self, "Timesheet Sync", f"บันทึกไฟล์สำเร็จ: {path}")

    def _on_export_error(self, message: str) -> None:
        self._set_export_busy(False)
        QMessageBox.warning(self, "Timesheet Sync", f"ส่งออกไฟล์ไม่สำเร็จ: {message}")

    def reset(self) -> None:
        self._filter_timer.stop()
        self._entries = []
        self._filtered = []
        # รายชื่อสถานะผูกกับ instance ที่ login อยู่ ต้องโหลดใหม่หลังสลับบัญชี/เซิร์ฟเวอร์
        self._statuses = None
        self._wp_status = {}
        self.mine_only_check.setChecked(True)
        self._user_id = None
        self._user_email = None
        self.export_btn.setEnabled(False)
        self.status_combo.clear()
        self.status_combo.setEnabled(False)
        self.from_status_combo.clear()
        self.from_status_combo.setEnabled(False)
        self.status_btn.setEnabled(False)
        self._set_default_range()
        self.search_edit.blockSignals(True)
        self.search_edit.clear()
        self.search_edit.blockSignals(False)
        self.wp_filter_combo.blockSignals(True)
        self.wp_filter_combo.clear()
        self.wp_filter_combo.addItem("ทั้งหมด", None)
        self.wp_filter_combo.blockSignals(False)
        self.status_filter_combo.blockSignals(True)
        self.status_filter_combo.clear()
        self.status_filter_combo.addItem("ทุกสถานะ", None)
        self.status_filter_combo.blockSignals(False)
        self.summary_totals_label.setText("")
        self.project_totals_label.hide()
        self.coverage_label.hide()
        self._searched_range = None
        self._clear_breakdown_chips()
        self._show_empty_message('เลือกช่วงวันที่แล้วกด "ค้นหา" เพื่อดึงข้อมูล')

"""Modal สำหรับค้นหา Work Package เดิม — แทนช่องค้นหา inline + dropdown Qt.Popup ที่เคยยัดอยู่
ในเซลล์ของตาราง (gui/wp_search_popup.py เดิม)

ที่ย้ายมาเป็น dialog เพราะเซลล์ในคอลัมน์ "งาน" กว้างไม่พอ: dropdown เดิมถูกบีบไว้ ~320px กว้าง
260px สูง (เห็นทีละ ~6 รายการ) ชื่องานจริงยาว 45-55 ตัวอักษรจึงโดนตัดเกือบทุกอัน และไม่มีที่ให้
วางตัวกรองโปรเจกต์/ปุ่มโหลดเพิ่ม

ต่างจาก Qt.Popup ตรงที่ dialog ไม่แย่ง key event ไปจาก QLineEdit — eventFilter บนช่องค้นหาจึง
ใช้ได้จริง (ในเคส popup มันไม่เห็น Up/Down/Enter เลย จนต้องไปดักที่ popup แทน)
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from gui.row_model import RowState
from gui.threads import run_async

SEARCH_DEBOUNCE_MS = 300
PAGE_SIZE = 30
ALL_PROJECTS_LABEL = "— ทุกโปรเจกต์ —"


def _wp_item_text(wp: dict) -> str:
    meta = " · ".join(filter(None, [wp.get("project"), wp.get("type"), wp.get("status")]))
    return f"#{wp['id']}  {wp.get('subject') or ''}\n{meta}"


def _row_context_text(row: RowState) -> str:
    """บรรทัดบอกว่ากำลังเลือกงานให้แถวไหน — modal บังตารางไว้ ผู้ใช้จึงมองแถวเดิมไม่เห็น

    ใช้ row.activity (ชื่อกิจกรรมดิบจาก Excel) ไม่ใช่ชื่อจาก dropdown เพราะ picker ไม่มีรายการ
    activity ของเซิร์ฟเวอร์อยู่ในมือ — ค่านี้พอให้ระบุแถวได้แล้ว
    """
    hours = f"{row.hours:g} ชม." if row.hours else None
    parts = [row.date, hours, row.activity or None, row.comment or None]
    return " · ".join(p for p in parts if p)


class WpSearchDialog(QDialog):
    def __init__(self, row: RowState, ctx, parent=None):
        super().__init__(parent)
        self.row = row
        self.ctx = ctx
        self.selected_wp: Optional[dict] = None

        self._results: list[dict] = []
        self._total = 0
        self._page = 1
        self._search_token = 0
        self._pending_query = ""

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(lambda: self._start_search(reset=True))

        self.setWindowTitle("เลือกงาน (Work Package)")
        self.setModal(True)
        self.resize(760, 560)
        self._build_ui()

        self._populate_projects()
        # กด "เปลี่ยน" บนแถวที่เลือกงานไว้แล้ว — เติมชื่อเดิมให้ค้นต่อได้ทันที และ select ไว้
        # ทั้งหมดเพื่อให้พิมพ์ทับได้เลยโดยไม่ต้องลบเอง
        if self.row.wp_subject:
            self.search_input.setText(self.row.wp_subject)
            self.search_input.selectAll()
            self._pending_query = self.row.wp_subject
            self._start_search(reset=True)
        else:
            self._set_placeholder("พิมพ์ชื่องานเพื่อค้นหา")

    # -- ui -------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        context = QLabel(_row_context_text(self.row))
        context.setObjectName("fieldLabel")
        context.setWordWrap(True)
        outer.addWidget(context)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("ค้นหาชื่องาน...")
        self.search_input.textEdited.connect(self._on_search_text_changed)
        self.search_input.installEventFilter(self)
        controls.addWidget(self.search_input, stretch=2)

        self.project_combo = QComboBox()
        self.project_combo.setToolTip("จำกัดผลการค้นหาให้อยู่ในโปรเจกต์เดียว")
        self.project_combo.currentIndexChanged.connect(self._on_project_changed)
        controls.addWidget(self.project_combo, stretch=1)
        outer.addLayout(controls)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("wpResults")
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._commit())
        self.list_widget.currentRowChanged.connect(self._sync_select_enabled)
        outer.addWidget(self.list_widget, stretch=1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.count_label = QLabel("")
        self.count_label.setObjectName("fieldLabel")
        footer.addWidget(self.count_label)

        self.more_btn = QPushButton("โหลดเพิ่ม")
        self.more_btn.setObjectName("mutedButton")
        self.more_btn.setCursor(Qt.PointingHandCursor)
        self.more_btn.hide()
        self.more_btn.clicked.connect(lambda: self._start_search(reset=False))
        footer.addWidget(self.more_btn)
        footer.addStretch()

        self.buttons = QDialogButtonBox()
        self.select_btn = self.buttons.addButton("เลือก", QDialogButtonBox.AcceptRole)
        self.select_btn.setObjectName("primaryButton")
        self.select_btn.setEnabled(False)
        cancel_btn = self.buttons.addButton("ยกเลิก", QDialogButtonBox.RejectRole)
        cancel_btn.setObjectName("mutedButton")
        self.buttons.accepted.connect(self._commit)
        self.buttons.rejected.connect(self.reject)
        footer.addWidget(self.buttons)
        outer.addLayout(footer)

    # -- projects ---------------------------------------------------------------

    def _populate_projects(self) -> None:
        # request_projects กันการยิงซ้ำ + แชร์ cache กับ picker ทุกแถว (ดู PickerSharedState)
        # ถ้ามี cache อยู่แล้วมันจะ callback กลับมาทันทีแบบ synchronous ไม่ต้องโชว์ "กำลังโหลด"
        if self.ctx.shared.projects_cache is None:
            self.project_combo.addItem("กำลังโหลดโปรเจกต์...", None)
            self.project_combo.setEnabled(False)
        self.ctx.shared.request_projects(self.ctx.get_projects, self._on_projects_ready)

    def _on_projects_ready(self, projects) -> None:
        if projects is None:
            self._on_projects_error()
        else:
            self._apply_projects(projects)

    def _on_projects_error(self) -> None:
        # ค้นแบบไม่กรองโปรเจกต์ยังทำงานได้ — แค่ปิดตัวกรองไว้ ไม่ต้องล้ม dialog ทั้งอัน
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        self.project_combo.addItem(ALL_PROJECTS_LABEL, None)
        self.project_combo.blockSignals(False)
        self.project_combo.setEnabled(False)
        self.project_combo.setToolTip("โหลดรายชื่อโปรเจกต์ไม่สำเร็จ — ค้นได้ทุกโปรเจกต์เท่านั้น")

    def _apply_projects(self, projects: list) -> None:
        shared = self.ctx.shared
        default_id = shared.last_search_project_id
        if default_id is None:
            default_id = shared.last_project_id  # ฝั่ง "สร้างงานใหม่" จำไว้ให้แล้ว

        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        self.project_combo.addItem(ALL_PROJECTS_LABEL, None)
        selected_index = 0
        for i, p in enumerate(projects, start=1):
            self.project_combo.addItem(p["name"], p["id"])
            if default_id is not None and str(p["id"]) == str(default_id):
                selected_index = i
        self.project_combo.setCurrentIndex(selected_index)
        self.project_combo.blockSignals(False)
        self.project_combo.setEnabled(True)

    def _current_project_id(self):
        return self.project_combo.itemData(self.project_combo.currentIndex())

    def _on_project_changed(self, index: int) -> None:
        self.ctx.shared.last_search_project_id = self.project_combo.itemData(index)
        if self._pending_query.strip():
            self._start_search(reset=True)

    # -- search -----------------------------------------------------------------

    def _on_search_text_changed(self, text: str) -> None:
        self._pending_query = text
        self._search_timer.stop()
        if text.strip():
            self._search_timer.start()
        else:
            self._search_token += 1  # ทิ้งผลที่ยังค้างอยู่ ไม่งั้นมันจะเด้งกลับมาทับช่องว่าง
            self._reset_results()
            self._set_placeholder("พิมพ์ชื่องานเพื่อค้นหา")

    def _start_search(self, reset: bool) -> None:
        query = self._pending_query
        if not query.strip():
            return
        self._search_timer.stop()
        page = 1 if reset else self._page + 1
        if reset:
            self._reset_results()
            self._set_placeholder("กำลังค้นหา...")
        else:
            self.more_btn.setEnabled(False)

        self._search_token += 1
        token = self._search_token
        project_id = self._current_project_id()
        run_async(
            self.ctx.search_work_packages_page,
            query,
            project_id,
            page,
            on_success=lambda data: self._on_results(token, page, data),
            on_error=lambda msg: self._on_error(token, msg),
        )

    def _on_results(self, token: int, page: int, data: dict) -> None:
        if token != self._search_token:
            return  # มีคำค้น/หน้าใหม่แซงไปแล้ว
        results = data.get("results") or []
        self._page = page
        self._total = data.get("total") or 0
        if page == 1:
            self._results = list(results)
            self.list_widget.clear()
        else:
            self._results.extend(results)

        for wp in results:
            self.list_widget.addItem(QListWidgetItem(_wp_item_text(wp)))

        if not self._results:
            hint = ""
            if self._current_project_id():
                hint = " — ลองเลือก “ทุกโปรเจกต์”"
            self._set_placeholder(f"ไม่พบงานที่ตรงกับ “{self._pending_query.strip()}”{hint}")
        elif page == 1:
            self.list_widget.setCurrentRow(0)

        self._sync_footer()

    def _on_error(self, token: int, message: str) -> None:
        if token != self._search_token:
            return
        # เดิม error ถูกกลืนเป็น list ว่าง ผู้ใช้แยกไม่ออกว่า "ไม่พบงาน" หรือ "ต่อเซิร์ฟเวอร์ไม่ได้"
        self._reset_results()
        self._set_placeholder(f"ค้นหาไม่สำเร็จ: {message}")
        self._sync_footer()

    def _reset_results(self) -> None:
        self._results = []
        self._total = 0
        self._page = 1
        self.list_widget.clear()
        self._sync_footer()

    def _set_placeholder(self, text: str) -> None:
        """ข้อความสถานะใช้ item ที่เลือกไม่ได้ในลิสต์เดียวกัน จะได้ไม่ต้องมี stacked widget แค่บอกสถานะ"""
        self.list_widget.clear()
        item = QListWidgetItem(text)
        item.setFlags(Qt.NoItemFlags)
        item.setTextAlignment(Qt.AlignCenter)
        self.list_widget.addItem(item)
        self._sync_select_enabled()

    def _sync_footer(self) -> None:
        if self._results:
            self.count_label.setText(f"แสดง {len(self._results)} จาก {self._total}")
        else:
            self.count_label.setText("")
        self.more_btn.setEnabled(True)
        self.more_btn.setVisible(bool(self._results) and len(self._results) < self._total)
        self._sync_select_enabled()

    def _sync_select_enabled(self, *_args) -> None:
        self.select_btn.setEnabled(self._current_wp() is not None)

    # -- selection ---------------------------------------------------------------

    def _current_wp(self) -> Optional[dict]:
        row = self.list_widget.currentRow()
        if 0 <= row < len(self._results):
            return self._results[row]
        return None

    def _commit(self) -> None:
        wp = self._current_wp()
        if wp is None:
            return
        self.selected_wp = wp
        self.accept()

    # -- keyboard ------------------------------------------------------------------

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self.search_input and event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Down, Qt.Key_Up):
                self._move_highlight(1 if key == Qt.Key_Down else -1)
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                if self._current_wp() is not None:
                    self._commit()
                else:
                    self._start_search(reset=True)  # ยังไม่มีผล — ค้นทันทีโดยไม่รอ debounce
                return True
        return super().eventFilter(obj, event)

    def _move_highlight(self, delta: int) -> None:
        if not self._results:
            return
        row = self.list_widget.currentRow()
        self.list_widget.setCurrentRow(max(0, min(len(self._results) - 1, row + delta)))

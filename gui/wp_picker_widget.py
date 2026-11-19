"""Per-row work-package picker — the hardest widget in the rewrite. Ported from renderWpCell /
renderExistingPanel / renderNewPanel / setMode / onTbodyKeydown / onSearchInput in
static/app.js. Built and iterated against a fake delayed search function in a standalone
isolation harness first, before being wired into TableController.

ฝั่ง "งานเดิม" ไม่ได้ค้นในเซลล์เองแล้ว — ปุ่มทุกตัวเปิด WpSearchDialog แทน (ดูเหตุผลใน
gui/wp_search_dialog.py) เซลล์เหลือหน้าที่แค่แสดงงานที่เลือกไว้กับเป็นปุ่มเรียก dialog
ส่วนฝั่ง "สร้างงานใหม่" ยังเป็นฟอร์มในเซลล์เหมือนเดิม
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.combo_model import build_combo_model, make_item, set_combo_model
from gui.row_model import RowState
from gui.theme import fit_combo_popup
from gui.threads import run_async
from gui.wp_search_dialog import WpSearchDialog


PROJECT_PLACEHOLDER = ("-- เลือกโปรเจกต์ --", None)


@dataclass
class PickerSharedState:
    """Shared across every row's picker in one table — mirrors app.js's single state.lastProjectId
    plus its per-project types cache, so later rows switched to "new" mode default sensibly.

    อยู่ยาวทั้ง session (MainWindow ถือไว้ ล้างตอน logout) ไม่ใช่ต่อการเปิดไฟล์หนึ่งครั้ง —
    เดิมถูกสร้างใหม่ทุกครั้งที่ _build_picker_context ถูกเรียก ทำให้ cache ที่เพิ่งได้มาถูกทิ้ง
    ทั้งตอนเปิดไฟล์ใหม่และตอน reload หลัง submit
    """

    last_project_id: Optional[object] = None
    last_project_name: Optional[str] = None
    projects_cache: Optional[list] = None
    types_cache: dict = field(default_factory=dict)
    # project_id -> ข้อความ error ของการโหลดประเภทงาน (ไม่ cache เป็น list ว่าง ไม่งั้นจะไม่มีวันลองใหม่)
    types_error: dict = field(default_factory=dict)
    # โปรเจกต์ที่เลือกไว้ในตัวกรองของ WpSearchDialog ครั้งล่าสุด — จำข้ามแถวเหมือน last_project_id
    # แต่แยกกันคนละตัว เพราะ "โปรเจกต์ที่จะสร้างงานใหม่" กับ "โปรเจกต์ที่กำลังค้น" ไม่จำเป็นต้องตรงกัน
    last_search_project_id: Optional[object] = None

    # QStandardItemModel ที่ทุก combo ในตารางใช้ร่วมกัน — สร้างพร้อม cache (ดู gui/combo_model.py)
    projects_model: Optional[object] = None
    types_models: dict = field(default_factory=dict)

    # คิวผู้รอผลที่ยังยิงไม่กลับ: None = ยังไม่มีใครยิง, list = กำลังยิงอยู่ ห้ามยิงซ้ำ
    # จำเป็นเพราะ cache ถูกเขียนใน callback เท่านั้น ระหว่างลูปสร้างแถวใน set_rows ยังไม่มี
    # response ไหนกลับมาเลย ทุกแถวจึงเห็น cache เป็น None แล้วยิงของตัวเอง (100 แถว = 100 request)
    projects_waiters: Optional[list] = None
    types_waiters: dict = field(default_factory=dict)

    # -- projects ------------------------------------------------------------

    def set_projects(self, projects: list) -> None:
        """เติม cache + สร้าง model ไว้ล่วงหน้า ใช้ตอน seed ผลจากหน้า login ที่ยิง get_projects
        เป็นตัวตรวจ credential อยู่แล้ว"""
        self.projects_cache = projects
        self.projects_model = build_combo_model(
            [(p["name"], p["id"]) for p in projects], placeholder=PROJECT_PLACEHOLDER
        )

    def request_projects(self, fetch: Callable[[], list], on_ready: Callable[[Optional[list]], None]) -> None:
        """on_ready(projects) เมื่อพร้อม หรือ on_ready(None) ถ้าโหลดไม่สำเร็จ"""
        if self.projects_cache is not None:
            on_ready(self.projects_cache)
            return
        if self.projects_waiters is not None:
            self.projects_waiters.append(on_ready)
            return
        self.projects_waiters = [on_ready]

        def done(projects: list) -> None:
            self.set_projects(projects)
            self._flush(self._take_project_waiters(), projects)

        def failed(_message: str) -> None:
            # ปล่อย waiters ทิ้งไว้ไม่ได้ แถวที่รออยู่จะค้างตลอด และต้องคืน waiters เป็น None
            # เพื่อให้ครั้งหน้ายิงใหม่ได้
            self._flush(self._take_project_waiters(), None)

        run_async(fetch, on_success=done, on_error=failed)

    def _take_project_waiters(self) -> list:
        waiters = self.projects_waiters or []
        self.projects_waiters = None
        return waiters

    # -- types ---------------------------------------------------------------

    def request_types(
        self, project_id, fetch: Callable[[object], list], on_ready: Callable[[Optional[list]], None]
    ) -> None:
        cached = self.types_cache.get(project_id)
        if cached is not None:
            on_ready(cached)
            return
        waiters = self.types_waiters.get(project_id)
        if waiters is not None:
            waiters.append(on_ready)
            return
        self.types_waiters[project_id] = [on_ready]
        self.types_error.pop(project_id, None)

        def done(types: list) -> None:
            self.types_error.pop(project_id, None)
            self.types_cache[project_id] = types
            self.types_models[project_id] = build_combo_model([(t["name"], t["id"]) for t in types])
            self._flush(self.types_waiters.pop(project_id, []), types)

        def failed(message: str) -> None:
            # ห้าม cache เป็น list ว่าง ไม่งั้นการเลือกโปรเจกต์นี้ครั้งต่อไปจะใช้ค่าว่างนั้นตลอด
            # session โดยไม่ยิงใหม่ ผู้ใช้จะเจอช่องประเภทงานว่างเปล่าแบบไม่มีทางแก้
            self.types_error[project_id] = message
            self._flush(self.types_waiters.pop(project_id, []), None)

        run_async(fetch, project_id, on_success=done, on_error=failed)

    @staticmethod
    def _flush(waiters: list, payload) -> None:
        for callback in waiters:
            callback(payload)


@dataclass
class PickerContext:
    # (query, project_id, page) -> {"results": [...], "total": int}
    search_work_packages_page: Callable[[str, Optional[object], int], dict]
    get_projects: Callable[[], list]
    get_types: Callable[[object], list]
    shared: PickerSharedState


class WorkPackagePickerWidget(QWidget):
    # WP selection/mode/subject changed — caller should recompute status badge/summary/filter/gating
    row_changed = Signal()

    def __init__(self, row: RowState, ctx: PickerContext, parent=None):
        super().__init__(parent)
        self.row = row
        self.ctx = ctx
        self.new_panel = None  # สร้างเมื่อเข้าโหมด "new" ครั้งแรกเท่านั้น — ดู _ensure_new_panel
        self._type_placeholder_model = None
        # โปรเจกต์ที่ widget "ตัวนี้" ขอประเภทงานไปแล้ว — กัน _refresh ที่ถูกเรียกซ้ำๆ ต่อคิว
        # callback ซ้ำให้ request เดิม โดยไม่ไปพึ่ง shared.types_waiters (ดูเหตุผลใน _refresh_new_panel)
        self._types_requested: set = set()

        self._build_ui()
        self._refresh()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(4)

        tabs = QHBoxLayout()
        tabs.setSpacing(4)
        self.existing_tab_btn = QPushButton("เลือกงานเดิม")
        self.existing_tab_btn.setObjectName("wpTab")
        self.existing_tab_btn.clicked.connect(self._on_existing_tab_clicked)
        tabs.addWidget(self.existing_tab_btn)

        self.new_tab_btn = QPushButton("+ สร้างงานใหม่")
        self.new_tab_btn.setObjectName("wpTab")
        self.new_tab_btn.clicked.connect(lambda: self._set_mode("new"))
        tabs.addWidget(self.new_tab_btn)
        tabs.addStretch()
        outer.addLayout(tabs)

        # แผงงานเดิมวางแบนอยู่ใน outer layout ตรงๆ แล้วสลับด้วย setVisible แทนการซ้อน
        # QStackedWidget สองชั้น (stack > existing_panel > selected_view/empty_view)
        # การแทรก widget ลงตารางต้อง polish + layout ทั้ง subtree ความลึกจึงแพงกว่าจำนวน widget:
        # setCellWidget ของ QLineEdit ธรรมดา 700 ตัวใช้แค่ 56 ms แต่ตารางจริง 100 แถวใช้ ~1,100 ms
        self.existing_panel = QWidget()
        existing_layout = QHBoxLayout(self.existing_panel)
        existing_layout.setContentsMargins(0, 0, 0, 0)
        existing_layout.setSpacing(4)

        self.selected_label = QLabel()
        self.selected_label.setWordWrap(True)
        existing_layout.addWidget(self.selected_label, stretch=1)

        self.change_btn = QPushButton("เปลี่ยน")
        self.change_btn.setObjectName("linkButton")
        self.change_btn.clicked.connect(self._open_search_dialog)
        existing_layout.addWidget(self.change_btn)

        self.pick_btn = QPushButton("🔍 คลิกเพื่อเลือกงาน")
        self.pick_btn.setObjectName("outlineButton")
        self.pick_btn.setCursor(Qt.PointingHandCursor)
        self.pick_btn.clicked.connect(self._open_search_dialog)
        existing_layout.addWidget(self.pick_btn, stretch=1)

        outer.addWidget(self.existing_panel)

    def _ensure_new_panel(self) -> None:
        """สร้างแผง "สร้างงานใหม่" ตอนต้องใช้จริงเท่านั้น

        แถวส่วนใหญ่อยู่โหมด existing และไม่เคยเปิดแผงนี้เลย แต่เดิม _build_ui สร้างให้ทุกแถว
        (5 widget + 2 layout) ซึ่งเป็นส่วนที่แพงที่สุดของการวาดตาราง — picker กินไป 73% ของ
        เวลาสร้าง cell ทั้งหมด

        วางต่อท้าย outer layout แล้วสลับกับ existing_panel ด้วย setVisible เหมือนกัน
        """
        if self.new_panel is not None:
            return

        # จัดเป็น 2 แถว: ชื่องานได้เต็มความกว้างแถวบน เพราะเป็นช่องเดียวที่ต้องพิมพ์เอง และ
        # ข้อมูลจริงยาว 45-55 ตัวอักษร ส่วนโปรเจกต์/ประเภทงานเป็น dropdown ที่ PickerSharedState
        # เติมค่าจากแถวก่อนหน้าให้อยู่แล้ว ส่วนใหญ่ไม่ต้องแตะ จึงแบ่งกันอยู่แถวล่างได้
        # (เดิมวาง label+field เรียงลงมา 3 ชุด ทำให้แถวสูง ~215px เทียบกับแถวปกติ ~72px)
        self.new_panel = QWidget()
        new_layout = QVBoxLayout(self.new_panel)
        new_layout.setContentsMargins(0, 0, 0, 0)
        new_layout.setSpacing(4)

        self.subject_input = QLineEdit()
        self.subject_input.setPlaceholderText("ชื่องานใหม่...")
        self.subject_input.textEdited.connect(self._on_subject_changed)
        new_layout.addWidget(self.subject_input)

        fields = QHBoxLayout()
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setSpacing(6)

        # หัวข้อของแต่ละช่องย้ายไปอยู่ใน placeholder/tooltip แทน QLabel เพื่อประหยัดความสูง
        self.project_combo = QComboBox()
        self.project_combo.setToolTip("โปรเจกต์ที่จะสร้างงานใหม่")
        self.project_combo.currentIndexChanged.connect(self._on_project_changed)
        fields.addWidget(self.project_combo, stretch=2)

        self.type_combo = QComboBox()
        self.type_combo.setToolTip("ประเภทงาน")
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        fields.addWidget(self.type_combo, stretch=2)

        self.type_retry_btn = QPushButton("↻ ลองใหม่")
        self.type_retry_btn.setObjectName("linkButton")
        self.type_retry_btn.setCursor(Qt.PointingHandCursor)
        self.type_retry_btn.hide()
        self.type_retry_btn.clicked.connect(self._on_retry_types_clicked)
        fields.addWidget(self.type_retry_btn)

        new_layout.addLayout(fields)

        self.layout().addWidget(self.new_panel)

    # -- mode / tab handling --------------------------------------------------

    def _set_mode(self, mode: str) -> None:
        if self.row.mode == mode:
            return
        self.row.mode = mode

        if mode == "new" and not self.row.new_project_id and self.ctx.shared.last_project_id:
            self.row.new_project_id = self.ctx.shared.last_project_id
            self.row.new_project_name = self.ctx.shared.last_project_name
            self.row.new_project_autofilled = True
            cached = self.ctx.shared.types_cache.get(self.row.new_project_id)
            if cached is not None:
                self.row.new_type_id = cached[0]["id"] if cached else None
            else:
                self._fetch_types(self.row.new_project_id, then_refresh=True)

        self._refresh()
        self.row_changed.emit()

    # -- existing: เรียก dialog ค้นหา -------------------------------------------

    def _on_existing_tab_clicked(self) -> None:
        """เปิด dialog เสมอ ไม่ใช่แค่สลับโหมด — แถวส่วนใหญ่อยู่โหมด existing อยู่แล้ว ถ้าปล่อยให้
        _set_mode คุมอย่างเดียว การกดปุ่มนี้จะไม่เกิดอะไรขึ้นเลย"""
        if self.row.mode != "existing":
            self._set_mode("existing")
        self._open_search_dialog()

    def _open_search_dialog(self) -> None:
        dialog = WpSearchDialog(self.row, self.ctx, self)
        if dialog.exec() != QDialog.Accepted or not dialog.selected_wp:
            return  # ยกเลิก — ไม่แตะ state เดิมของแถว
        wp = dialog.selected_wp
        self.row.wp_id = wp["id"]
        self.row.wp_subject = wp.get("subject")
        self.row.wp_project = wp.get("project")
        self.row.wp_type = wp.get("type")
        self.row.wp_status = wp.get("status")
        self.row.auto_matched = False
        self._refresh()
        self.row_changed.emit()

    # -- new panel --------------------------------------------------------------

    def _ensure_projects_loaded(self) -> None:
        # request_projects กันการยิงซ้ำให้แล้ว — ทุกแถวที่เรียกพร้อมกันจะได้ผลจาก request เดียว
        self.ctx.shared.request_projects(self.ctx.get_projects, self._populate_projects)

    def _populate_projects(self, projects: Optional[list]) -> None:
        if not projects or self.new_panel is None:
            return  # โหลดไม่สำเร็จ หรือแถวนี้กลับไปโหมด existing ก่อน response จะมาถึง
        selected_index = 0
        for i, p in enumerate(projects, start=1):
            if self.row.new_project_id is not None and str(p["id"]) == str(self.row.new_project_id):
                selected_index = i
                break
        set_combo_model(self.project_combo, self.ctx.shared.projects_model, selected_index)
        fit_combo_popup(self.project_combo)  # ชื่อโปรเจกต์ยาวกว่าช่อง ดรอปดาวน์ต้องอ่านออกครบ

    def _on_project_changed(self, index: int) -> None:
        project_id = self.project_combo.itemData(index)
        if project_id == self.row.new_project_id:
            return
        self.row.new_project_id = project_id
        self.row.new_project_name = self.project_combo.itemText(index) if project_id else None
        self.row.new_project_autofilled = False
        self.row.new_type_id = None
        self._set_type_placeholder("")
        self.type_combo.setEnabled(False)

        if project_id:
            self.ctx.shared.last_project_id = project_id
            self.ctx.shared.last_project_name = self.row.new_project_name
            self._fetch_types(project_id, then_refresh=True)

        self._refresh()
        self.row_changed.emit()

    def _fetch_types(self, project_id, then_refresh: bool) -> None:
        self._types_requested.add(project_id)
        self.ctx.shared.request_types(
            project_id,
            self.ctx.get_types,
            lambda types: self._apply_types(project_id, types, then_refresh),
        )

    def _on_retry_types_clicked(self) -> None:
        if self.row.new_project_id:
            # ต้องล้าง error ก่อน ไม่งั้น request_types เห็นว่ายังไม่มี waiters แต่ _refresh_new_panel
            # จะยังอ่านเจอ error เดิมแล้วโชว์ "โหลดไม่สำเร็จ" ค้างไว้
            self.ctx.shared.types_error.pop(self.row.new_project_id, None)
            self._types_requested.discard(self.row.new_project_id)
            self._fetch_types(self.row.new_project_id, then_refresh=True)
            self._refresh()

    def _apply_types(self, project_id, types: Optional[list], then_refresh: bool) -> None:
        if self.row.new_project_id != project_id:
            return  # user already switched to a different project before this response landed
        if types:
            self.row.new_type_id = types[0]["id"]
        if then_refresh:
            self._refresh()
            self.row_changed.emit()

    def _set_type_placeholder(self, text: str, tooltip: str = "") -> None:
        """ช่องประเภทงานตอนยังไม่มีรายการจริง (กำลังโหลด / error / โปรเจกต์ไม่มีประเภทงาน)

        ต้องใช้ model ส่วนตัวของ widget นี้ ห้ามไปแตะ types_models ที่แถวอื่นใช้ร่วมอยู่
        """
        if self._type_placeholder_model is None:
            self._type_placeholder_model = build_combo_model([], parent=self)
        model = self._type_placeholder_model
        model.clear()
        if text:
            model.appendRow(make_item(text, None))
        set_combo_model(self.type_combo, model, 0)
        fit_combo_popup(self.type_combo)
        self.type_combo.setToolTip(tooltip or "ประเภทงาน")

    def _on_subject_changed(self, text: str) -> None:
        self.row.new_subject = text
        self.subject_input.setToolTip(text)  # ชื่อยาวเกินช่อง — ให้ hover อ่านเต็มได้เสมอ
        self.row_changed.emit()

    def _on_type_changed(self, index: int) -> None:
        self.row.new_type_id = self.type_combo.itemData(index)

    # -- rendering ----------------------------------------------------------

    def _refresh(self) -> None:
        self.existing_tab_btn.setProperty("active", self.row.mode == "existing")
        self.new_tab_btn.setProperty("active", self.row.mode == "new")
        for btn in (self.existing_tab_btn, self.new_tab_btn):
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        existing = self.row.mode == "existing"
        self.existing_panel.setVisible(existing)
        if existing:
            self._refresh_existing_panel()
            if self.new_panel is not None:
                self.new_panel.setVisible(False)
        else:
            self._ensure_new_panel()
            self.new_panel.setVisible(True)
            self._refresh_new_panel()

    def _refresh_existing_panel(self) -> None:
        has_wp = bool(self.row.wp_id)
        self.selected_label.setVisible(has_wp)
        self.change_btn.setVisible(has_wp)
        self.pick_btn.setVisible(not has_wp)
        if not has_wp:
            return
        prefix = "พบงานเดิม · " if self.row.auto_matched else ""
        text = f"{prefix}#{self.row.wp_id} {self.row.wp_subject or ''} ({self.row.wp_project or ''})"
        self.selected_label.setText(text)
        self.selected_label.setToolTip(text)  # ชื่องานยาวเกินความกว้างเซลล์ — ให้ hover อ่านเต็มได้

    def _refresh_new_panel(self) -> None:
        self._ensure_projects_loaded()

        subject = self.row.new_subject or ""
        if self.subject_input.text() != subject:
            self.subject_input.blockSignals(True)
            self.subject_input.setText(subject)
            # ชื่องานจริงยาว 45-55 ตัวอักษร เกินความกว้างช่อง — QLineEdit จะโชว์ "ท้ายชื่อ" หลัง
            # setText ซึ่งอ่านไม่รู้เรื่อง ต้องดีดเคอร์เซอร์กลับไปต้นข้อความให้เห็นหัวชื่อก่อน
            self.subject_input.setCursorPosition(0)
            self.subject_input.blockSignals(False)
        self.subject_input.setToolTip(subject)

        # เดิมข้อความนี้อยู่บน QLabel เหนือช่อง — ย้ายมาเป็น tooltip ตอนตัด label ออกเพื่อลด
        # ความสูงแถว ข้อมูล "จำจากแถวก่อนหน้า" ยังต้องบอกผู้ใช้อยู่ ไม่ใช่ตัดทิ้ง
        self.project_combo.setToolTip(
            "โปรเจกต์ที่จะสร้างงานใหม่"
            + ("  (จำจากแถวก่อนหน้า)" if self.row.new_project_autofilled else "")
        )

        project_id = self.row.new_project_id
        shared = self.ctx.shared
        # แถวที่มาจาก Excel เป็นโหมด "new" พร้อมโปรเจกต์อยู่แล้ว ไม่มีใครคลิกให้ _fetch_types ทำงาน
        # จึงต้องเริ่มโหลดเองตรงนี้
        #
        # กันยิงซ้ำด้วย _types_requested ของ widget ตัวเอง ไม่ใช่ shared.types_waiters — เดิมใช้
        # types_waiters แล้วทุกแถวที่วาดหลังแถวแรก (ซึ่งเป็นคนตั้งคิว) จะ "ข้าม" การขอไปเลย
        # จึงไม่มี callback เป็นของตัวเอง พอผลกลับมาก็ไม่มีใคร _refresh ให้ ช่องประเภทงานค้างที่
        # "กำลังโหลด..." และ new_type_id เป็น None ตลอด ทั้งไฟล์จึงขึ้น "ต้องเลือก" ยกเว้นแถวแรก
        # (request_types รวบผู้รอให้อยู่แล้ว การขอพร้อมกันหลายแถวจึงยังยิงเน็ตครั้งเดียว)
        if project_id and project_id not in shared.types_cache \
                and project_id not in shared.types_error \
                and project_id not in self._types_requested:
            self._fetch_types(project_id, then_refresh=True)

        types = self.ctx.shared.types_cache.get(project_id) if project_id else None
        types_error = self.ctx.shared.types_error.get(project_id) if project_id else None

        if types:
            match_index = next((i for i, t in enumerate(types) if t["id"] == self.row.new_type_id), 0)
            set_combo_model(self.type_combo, self.ctx.shared.types_models[project_id], match_index)
            fit_combo_popup(self.type_combo)
            # ช่องแสดงประเภทงานที่ match_index อยู่แล้ว แต่ set_combo_model บล็อก signal ไว้
            # (ตั้งใจ — กันไม่ให้ handler เขียนทับ state ระหว่างวาดตาราง) ค่าที่ "เห็น" กับที่ "เก็บ"
            # จึงหลุดกันได้: แถวแรกของโปรเจกต์ได้ new_type_id จาก _apply_types ส่วนแถวที่เหลือ
            # ไปรอคิวใน types_waiters แล้วไม่มีใครเรียก _apply_types ให้ ทำให้ช่องโชว์ประเภทงาน
            # แต่ new_type_id ยังเป็น None แถวขึ้น "ต้องเลือก" ทั้งที่ดูเหมือนเลือกไว้แล้ว
            new_type_id = types[match_index]["id"]
            if self.row.new_type_id != new_type_id:
                # เขียน state เฉยๆ ไม่พอ badge/checkbox ถูกคำนวณไปก่อนหน้านี้แล้ว ต้องบอกให้ตาราง
                # คิดใหม่ด้วย (เช็ค != ก่อน กัน row_changed -> re-render -> _refresh วนไม่จบ)
                self.row.new_type_id = new_type_id
                self.row_changed.emit()
            self.type_combo.setToolTip("ประเภทงาน")
        elif project_id and types_error:
            self._set_type_placeholder("โหลดประเภทงานไม่สำเร็จ", types_error)
        elif project_id and types is not None:
            self._set_type_placeholder("โปรเจกต์นี้ไม่มีประเภทงานให้เลือก")
        elif project_id:
            # ยิงไปแล้วแต่ยังไม่กลับมา — บอกให้รู้ว่ากำลังโหลด ไม่ใช่ช่องว่างเปล่าที่ดูเหมือนพัง
            self._set_type_placeholder("กำลังโหลดประเภทงาน...")
        else:
            self._set_type_placeholder("")
        self.type_combo.setEnabled(bool(types))
        self.type_retry_btn.setVisible(bool(project_id and types_error))

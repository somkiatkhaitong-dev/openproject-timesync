"""Owns the QTableWidget + list[RowState].

date/hours/comment are live editors that mutate RowState in place (mirrors static/app.js
mutating its row objects directly from input listeners) and notify the caller via
on_row_edited so the summary dock can recompute.

Activity is a dropdown of the real activities on the OpenProject server (set_activities);
the Excel column only supplies the initial pick and the tooltip. Picking is required before
a row counts as resolved, so a name that has no counterpart on the server is caught here
instead of failing every row at submit time.
"""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from gui.combo_model import build_combo_model, set_combo_model
from gui.row_model import RowState
from gui.theme import COLORS, fit_combo_popup, tune_calendar_popup
from gui.wp_picker_widget import PickerContext, WorkPackagePickerWidget

COLUMN_LABELS = ["ส่ง", "สถานะ", "วันที่", "ชั่วโมง", "กิจกรรม", "งาน (Work Package)", "หมายเหตุ"]
DATE_FORMAT = "yyyy-MM-dd"
COMPACT_ROW_HEIGHT = 72
# "+ สร้างงานใหม่" lays the subject out on its own full-width line with project/type sharing the
# line below, so it needs a bit more height than the other cells in the row (badge/date/hours/
# comment) — but nowhere near the 210 it took when every field had its own stacked label.
EXPANDED_ROW_HEIGHT = 110
# บรรทัดคำเตือนใต้ช่อง "งาน" หลังกดเช็คกับ OpenProject — แถวที่ไม่มีคำเตือนสูงเท่าเดิมเป๊ะ
WARN_LINE_HEIGHT = 22


class TableController:
    def __init__(
        self,
        table: QTableWidget,
        on_row_edited: Optional[Callable[[RowState], None]] = None,
        on_wp_changed: Optional[Callable[[int], None]] = None,
        on_selection_changed: Optional[Callable[[], None]] = None,
    ):
        self.table = table
        self.rows: list[RowState] = []
        self.on_row_edited = on_row_edited
        self.on_wp_changed = on_wp_changed
        self.on_selection_changed = on_selection_changed
        self.picker_ctx: Optional[PickerContext] = None
        self.activities: list[dict] = []
        # ผลล่าสุดจาก common.check_existing_work — ว่างไว้จนกว่าผู้ใช้จะกดปุ่มเช็คเอง
        # {ชื่องาน: [wp, ...]} และ {(wp_id, วันที่): ชั่วโมงที่ลงไปแล้ว}
        self.duplicates: dict[str, list[dict]] = {}
        self.logged_wp_dates: dict[tuple, float] = {}
        # QLabel คำเตือนของแต่ละแถว (index -> label) สร้างพร้อมแถวแล้วซ่อนไว้ ดู _wp_cell
        self._warn_labels: dict[int, QLabel] = {}
        # model ตัวเดียวที่ combo กิจกรรมของทุกแถวใช้ร่วมกัน — เดิมแต่ละแถวเติมรายการเอง ทำให้
        # set_activities (ยิงตอนโหลด activity เสร็จ ~5 วิหลัง login) ค้าง UI ไป 507 ms ที่ 100 แถว
        self._activities_model = None
        self.filter_mode = "all"  # "all" | "unresolved"
        # เก็บคำค้นไว้ที่นี่เหมือน filter_mode เพราะ set_rows()/MainWindow เรียก apply_filter()
        # ซ้ำทุกครั้งที่แก้แถว — ถ้าไม่เก็บไว้ คำค้นจะหายไปเองระหว่างแก้งาน
        self.filter_query = ""

        table.setColumnCount(len(COLUMN_LABELS))
        table.setHorizontalHeaderLabels(COLUMN_LABELS)
        table.verticalHeader().setVisible(False)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setShowGrid(False)

        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        # คอลัมน์ 5 ("งาน") เป็น Stretch จึงกินพื้นที่ที่เหลือทั้งหมด — บีบคอลัมน์คงที่ให้แคบลง
        # รวม 106px เพื่อยกให้ชื่องานที่ยาว 45-55 ตัวอักษร (รวมคงที่ 790 -> 684)
        table.setColumnWidth(0, 44)   # ส่ง
        table.setColumnWidth(1, 110)  # สถานะ
        # 140 ไม่ใช่ 120 — วัดจากภาพที่เรนเดอร์จริง ที่ 120px "2026-08-03" ถูกตัดเหลือ
        # "2026-08-0" (130 คือค่าต่ำสุดที่ครบพอดี เผื่อไว้อีก 10px เรื่องฟอนต์/การปัดเศษ)
        table.setColumnWidth(2, 140)  # วันที่
        table.setColumnWidth(3, 80)   # ชั่วโมง
        table.setColumnWidth(4, 130)  # กิจกรรม
        table.setColumnWidth(6, 200)  # หมายเหตุ

        self.show_empty_message("กำลังโหลด...")

    def show_empty_message(self, message: str) -> None:
        self.rows = []
        self._warn_labels = {}
        table = self.table
        # ต้องลงศูนย์ก่อนเสมอ ห้ามย่อจาก N มาเป็น 1 ตรงๆ — Qt ลบ cell widget ให้เฉพาะแถวที่หายไป
        # แถวที่ 0 ยังอยู่ widget ทั้ง 7 ช่องของมันจึงรอด แล้ว setCellWidget ข้างล่างเขียนทับแค่
        # ช่องเดียว อีก 6 ช่องยังเกาะตารางและถูก setSpan ดันมาวาดทับข้อความ (เห็นเป็นหมายเหตุ
        # ของแถวเก่าค้างอยู่กลางตารางว่างหลังกด "เคลียร์ไฟล์")
        table.setRowCount(0)
        table.setRowCount(1)
        table.setSpan(0, 0, 1, len(COLUMN_LABELS))
        label = QLabel(message)
        label.setObjectName("fieldLabel")
        label.setAlignment(Qt.AlignCenter)
        table.setCellWidget(0, 0, label)
        table.setRowHeight(0, 60)

    def set_rows(self, rows: list[RowState], picker_ctx: Optional[PickerContext] = None) -> None:
        self.rows = rows
        self.picker_ctx = picker_ctx
        self._warn_labels = {}
        table = self.table
        table.clearSpans()

        if not rows:
            self.show_empty_message("ไม่มีแถวที่ต้อง sync")
            return

        table.setRowCount(len(rows))
        table.setUpdatesEnabled(False)
        try:
            for i, row in enumerate(rows):
                # picker เติม new_type_id ให้แถวโหมด "new" ตั้งแต่ตอนสร้าง widget (ดู
                # WorkPackagePickerWidget._refresh_new_panel) จึงต้องสร้างมันก่อน checkbox กับ badge
                # ไม่งั้นสองช่องนั้นถูกคำนวณจาก state ที่ยังไม่ครบแล้วค้างที่ "ต้องเลือก" ทั้งที่แถว
                # พร้อมส่ง — ผู้ใช้ต้องไปขยับ combo อื่น (เช่นกิจกรรม) เองให้ recompute ถึงจะส่งได้
                # (row_changed ช่วยไม่ได้ ตอน __init__ ทำงานยังไม่มีใคร connect signal ตัวนี้)
                wp_cell = self._wp_cell(row, i)
                table.setCellWidget(i, 0, self._select_widget(row, i))
                table.setCellWidget(i, 1, self._status_widget(row, i))
                table.setCellWidget(i, 2, self._date_widget(row, i))
                table.setCellWidget(i, 3, self._hours_widget(row))
                table.setCellWidget(i, 4, self._activity_widget(row, i))
                table.setCellWidget(i, 5, wp_cell)
                table.setCellWidget(i, 6, self._comment_widget(row))
                table.setRowHeight(i, self._row_height_for(row))
        finally:
            table.setUpdatesEnabled(True)

        self.apply_filter(self.filter_mode)

    def counts(self) -> tuple[int, int]:
        total = len(self.rows)
        unresolved = sum(1 for r in self.rows if not r.is_resolved())
        return total, unresolved

    def apply_filter(self, mode: str, query: str | None = None) -> None:
        """query=None = ใช้คำค้นเดิม (ผู้เรียกส่วนใหญ่แค่อยากวาดตัวกรองเดิมซ้ำ)"""
        self.filter_mode = mode
        if query is not None:
            self.filter_query = query.strip().lower()

        for i, row in enumerate(self.rows):
            hidden = mode == "unresolved" and row.is_resolved()
            if not hidden and self.filter_query:
                hidden = self.filter_query not in self._row_haystack(row)
            self.table.setRowHidden(i, hidden)

    def _row_haystack(self, row: RowState) -> str:
        return " ".join(
            str(v) for v in (
                row.date, row.comment, row.wp_subject, row.new_subject,
                row.wp_id, row.wp_project, row.new_project_name,
            ) if v
        ).lower()

    def warning_count(self) -> int:
        """จำนวนแถวที่ผลเช็คล่าสุดบอกว่าต้องดู — 0 เสมอถ้ายังไม่เคยกดปุ่มเช็ค"""
        return sum(1 for r in self.rows if self._row_warning(r))

    def visible_count(self) -> int:
        return sum(1 for i in range(len(self.rows)) if not self.table.isRowHidden(i))

    def select_all(self) -> None:
        """ติ๊กทุกแถวที่พร้อมส่ง (แถวที่ยังไม่พร้อมติ๊กไม่ได้อยู่แล้ว ไม่มีผล)"""
        for i, row in enumerate(self.rows):
            if row.is_resolved():
                row.selected = True
            self._sync_select_widget(i, row)
        if self.on_selection_changed:
            self.on_selection_changed()

    def select_none(self) -> None:
        for i, row in enumerate(self.rows):
            row.selected = False
            self._sync_select_widget(i, row)
        if self.on_selection_changed:
            self.on_selection_changed()

    def _sync_select_widget(self, index: int, row: RowState) -> None:
        """อัปเดต checkbox เดิมแทนการสร้าง widget ใหม่

        เดิมทั้ง select_all/select_none/_on_row_resolution_changed เรียก setCellWidget ซึ่งทำลาย
        แล้วสร้าง QWidget+QCheckBox ใหม่ทุกแถวเพียงเพื่อสลับค่า boolean (93 ms ที่ 200 แถว)
        """
        wrapper = self.table.cellWidget(index, 0)
        checkbox = getattr(wrapper, "checkbox", None)
        if checkbox is None:
            self.table.setCellWidget(index, 0, self._select_widget(row, index))
            return
        resolved = row.is_resolved()
        checkbox.blockSignals(True)
        checkbox.setEnabled(resolved)
        checkbox.setChecked(row.selected if resolved else False)
        checkbox.blockSignals(False)

    def set_activities(self, activities: list[dict]) -> None:
        """รายการ activity มาจากเซิร์ฟเวอร์ทีหลัง (โหลดเบื้องหลัง) — สร้างเซลล์กิจกรรมใหม่ให้ทุกแถว

        ยังต้องสร้าง widget ใหม่เพราะเซลล์เดิมเป็น QLabel (ตอนยังไม่มีรายการ) ไม่ใช่ QComboBox
        แต่ตอนนี้ทุก combo ใช้ model ร่วมกันแล้ว ต้นทุนจึงเหลือแค่ตัว widget ไม่ใช่การเติมรายการ
        """
        self.activities = activities or []
        self._activities_model = None  # สร้างใหม่ตอนแถวแรกขอใช้ ดู _ensure_activities_model

        self.table.setUpdatesEnabled(False)
        try:
            for i, row in enumerate(self.rows):
                existing = self.table.cellWidget(i, 4)
                if self.activities and isinstance(existing, QComboBox):
                    # เซลล์เป็น combo อยู่แล้ว — สลับ model พอ ไม่ต้องทำลาย/สร้าง widget ใหม่
                    # (setCellWidget คือส่วนที่แพงจริง ไม่ใช่การเติมรายการ)
                    self._apply_activity_model(existing, row)
                    self._fit_activity_combo(existing)
                else:
                    self.table.setCellWidget(i, 4, self._activity_widget(row, i))
        finally:
            self.table.setUpdatesEnabled(True)

    def add_row(self, row: RowState) -> None:
        """เพิ่มแถวที่ผู้ใช้สร้างเองต่อท้ายตาราง แล้ววาดใหม่ทั้งตาราง (ตารางเล็ก ถูกกว่าการไล่แก้ index
        ของ callback ที่ผูกไว้กับแถวเดิม)"""
        self.set_rows(self.rows + [row], self.picker_ctx)
        if self.on_wp_changed:
            self.on_wp_changed(len(self.rows) - 1)

    def remove_row(self, index: int) -> None:
        rows = [r for i, r in enumerate(self.rows) if i != index]
        self.set_rows(rows, self.picker_ctx)
        if self.on_wp_changed:
            self.on_wp_changed(index)

    def refresh_status_badge(self, index: int) -> None:
        row = self.rows[index]
        self.table.setCellWidget(index, 1, self._status_widget(row, index))

    # -- ผลเช็คกับ OpenProject ----------------------------------------------

    def set_check_result(self, duplicates: dict, logged_wp_dates: dict) -> None:
        """รับผลจาก common.check_existing_work มาย้อมเตือนในตาราง — ไม่แตะ selected/is_resolved

        เตือนอย่างเดียวไม่บล็อก ตามที่ check_existing_work เขียนกำกับตัวเองไว้ ปุ่มส่งจึงยังกดได้
        เหมือนเดิมทุกกรณี คนตัดสินใจเอง"""
        self.duplicates = duplicates or {}
        self.logged_wp_dates = logged_wp_dates or {}
        for i in range(len(self.rows)):
            self.refresh_row_warning(i)

    def clear_check_result(self) -> None:
        """ไฟล์ใหม่/ตารางใหม่ = ผลเช็คของไฟล์เก่าใช้ไม่ได้แล้ว"""
        self.set_check_result({}, {})

    def refresh_row_warning(self, index: int) -> None:
        """วาดคำเตือนของแถวเดียวใหม่ทั้ง 3 ที่ (badge, บรรทัดใต้ช่องงาน, ความสูงแถว)

        ต้องเรียกทุกครั้งที่แถวเปลี่ยนจนคำเตือนอาจไม่ตรงแล้ว — แก้วันที่, เปลี่ยน WP, สลับโหมด"""
        row = self.rows[index]
        self._apply_row_warning(index, row)
        self.refresh_status_badge(index)
        self.table.setRowHeight(index, self._row_height_for(row))

    def _row_warning(self, row: RowState) -> Optional[tuple[str, str, str]]:
        """แถวนี้มีอะไรต้องดูไหม — คืน (ข้อความ badge, objectName, คำอธิบาย) หรือ None

        คิดสดจาก (wp_id, วันที่) ทุกครั้งที่วาด ไม่เก็บผลลง RowState — พอผู้ใช้แก้วันที่หรือเปลี่ยน
        WP คำเตือนจึงหายไปเองโดยไม่ต้องกดเช็คใหม่

        ลงชั่วโมงซ้ำร้ายแรงกว่าชื่องานพ้องกัน (time entry ที่ซ้ำตามลบทีหลังยาก ส่วนชื่อพ้องกัน
        บางทีก็ตั้งใจ) จึงเช็คก่อนและใช้สีแดง"""
        if row.mode == "existing" and row.wp_id and row.date:
            hours = self.logged_wp_dates.get((row.wp_id, row.date))
            if hours:
                return (
                    "ลงแล้ว",
                    "badgeRed",
                    f"งานนี้ลงชั่วโมงวันที่ {row.date} ไปแล้ว {hours:g} ชม. — ส่งอีกคือลงซ้ำ",
                )
        if row.mode == "new":
            matches = self.duplicates.get((row.new_subject or "").strip())
            if matches:
                wp = matches[0]
                subject = (wp.get("subject") or "").strip()
                return (
                    "ชื่อซ้ำ",
                    "badgeOrange",
                    f"ซ้ำกับ #{wp['id']} {subject} — ส่งต่อจะสร้างงานใหม่อีกอัน",
                )
        return None

    def _apply_row_warning(self, index: int, row: RowState) -> None:
        """เปิด/ปิดบรรทัดคำเตือนใต้ช่องงานของแถวเดียว — label ถูกสร้างไว้แล้วตั้งแต่ _wp_cell"""
        label = self._warn_labels.get(index)
        if label is None:
            return
        warning = self._row_warning(row)
        if warning is None:
            label.clear()
            label.hide()
            return
        _, object_name, message = warning
        color = COLORS["error"] if object_name == "badgeRed" else COLORS["warn"]
        label.setStyleSheet(f"color: {color}; font-size: 11px;")
        label.setText(message)
        label.show()

    # -- cell builders ------------------------------------------------------

    def _plain_label(self, text: str) -> QWidget:
        label = QLabel(text)
        label.setContentsMargins(10, 4, 10, 4)
        label.setWordWrap(True)
        return label

    def _select_widget(self, row: RowState, index: int) -> QWidget:
        checkbox = QCheckBox()
        checkbox.setEnabled(row.is_resolved())
        checkbox.setChecked(row.selected if row.is_resolved() else False)
        checkbox.setCursor(Qt.PointingHandCursor)

        def on_toggled(checked: bool, r=row):
            r.selected = checked
            if self.on_selection_changed:
                self.on_selection_changed()

        checkbox.toggled.connect(on_toggled)

        wrapper = QWidget()
        wrapper.checkbox = checkbox  # ให้ _sync_select_widget หยิบกลับมาแก้ค่าได้โดยไม่ต้องสร้างใหม่
        wrapper_layout = QHBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.addWidget(checkbox)
        wrapper_layout.setAlignment(checkbox, Qt.AlignCenter)
        return wrapper

    def _status_widget(self, row: RowState, index: int) -> QWidget:
        # คอลัมน์นี้กว้าง 110px ใส่ badge ได้ใบเดียว — คำเตือนจึง "แทนที่" สถานะปกติ ไม่ใช่ต่อท้าย
        # (แถวที่โดนเตือนยัง is_resolved() เหมือนเดิม การซ่อนป้าย "พร้อมส่ง" จึงไม่ได้ปิดข้อมูลอะไร)
        warning = self._row_warning(row)
        if warning:
            text, object_name, message = warning
        elif row.is_resolved():
            text = "พบงานเดิม" if (row.mode == "existing" and row.auto_matched) else "พร้อมส่ง"
            object_name, message = "badgeGreen", ""
        else:
            text, object_name, message = "ต้องเลือก", "badgeOrange", ""

        badge = QLabel(text)
        badge.setObjectName(object_name)
        badge.setAlignment(Qt.AlignCenter)
        if message:
            badge.setToolTip(message)

        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.addWidget(badge)

        # ลบได้เฉพาะแถวที่เพิ่มในแอปและยังไม่ถูกเขียนลงไฟล์ — แถวที่มาจาก Excel ต้องไปลบในไฟล์เอง
        if row.excel_row is None:
            remove_btn = QPushButton("✕")
            remove_btn.setObjectName("linkButton")
            remove_btn.setCursor(Qt.PointingHandCursor)
            remove_btn.setToolTip("ลบแถวนี้ (ยังไม่ถูกบันทึกลงไฟล์)")
            remove_btn.clicked.connect(lambda _=False, i=index: self.remove_row(i))
            layout.addWidget(remove_btn)

        layout.addStretch()
        return wrapper

    def _date_widget(self, row: RowState, index: int) -> QWidget:
        from PySide6.QtWidgets import QDateEdit

        editor = QDateEdit()
        editor.setDisplayFormat(DATE_FORMAT)
        editor.setCalendarPopup(True)
        tune_calendar_popup(editor)
        qdate = QDate.fromString(row.date, DATE_FORMAT) if row.date else QDate.currentDate()
        editor.setDate(qdate if qdate.isValid() else QDate.currentDate())

        def on_changed(new_date: QDate, r=row, i=index):
            r.date = new_date.toString(DATE_FORMAT)
            # คำเตือน "ลงแล้ว" ผูกกับ (wp_id, วันที่) — ย้ายวันแล้วต้องหายเอง ไม่ต้องกดเช็คใหม่
            self.refresh_row_warning(i)
            if self.on_row_edited:
                self.on_row_edited(r)

        editor.dateChanged.connect(on_changed)
        return editor

    def _hours_widget(self, row: RowState) -> QWidget:
        editor = QDoubleSpinBox()
        editor.setDecimals(2)
        editor.setSingleStep(0.5)
        editor.setMinimum(0.0)
        editor.setMaximum(24.0)
        editor.setValue(row.hours if row.hours is not None else 0.0)

        def on_changed(value: float, r=row):
            r.hours = value
            if self.on_row_edited:
                self.on_row_edited(r)

        editor.valueChanged.connect(on_changed)
        return editor

    def _ensure_activities_model(self):
        """สร้าง model ตอนแถวแรกขอใช้ ไม่ใช่ตอน set_activities

        ผูกกับ self.activities ตรงนี้ที่เดียว ทำให้ใครก็ตามที่ตั้ง self.activities เอง (เช่น
        โค้ดทดสอบ) ไม่หลุดไปเจอ model เป็น None
        """
        if self._activities_model is None:
            self._activities_model = build_combo_model(
                [(a["name"], a["id"]) for a in self.activities],
                placeholder=("— เลือกกิจกรรม —", None),
            )
        return self._activities_model

    def _fit_activity_combo(self, combo: QComboBox) -> None:
        """ชื่อกิจกรรมยาวกว่าคอลัมน์ (130px) ได้ — อย่างน้อยดรอปดาวน์ต้องอ่านออกครบ"""
        fit_combo_popup(combo)

    def _apply_activity_model(self, combo: QComboBox, row: RowState) -> None:
        target = -1
        if row.activity_id:
            target = next(
                (i for i, a in enumerate(self.activities, start=1) if a["id"] == row.activity_id), -1
            )
        if target < 0:
            # id ที่ค้างมาจาก activity_mapping ใช้ไม่ได้กับเซิร์ฟเวอร์นี้ — บังคับให้เลือกใหม่
            row.activity_id = None
        set_combo_model(combo, self._ensure_activities_model(), max(target, 0))
        if row.activity:
            combo.setToolTip(f'ค่าในไฟล์ Excel: "{row.activity}"')

    def _activity_widget(self, row: RowState, index: int) -> QWidget:
        if not self.activities:
            # ยังโหลดรายการจากเซิร์ฟเวอร์ไม่เสร็จ (หรือดึงไม่ได้) — โชว์ชื่อจาก Excel ไปก่อน
            return self._plain_label(row.activity or "-")

        combo = QComboBox()
        self._apply_activity_model(combo, row)

        def on_changed(i: int, r=row, c=combo, idx=index):
            r.activity_id = c.itemData(i)
            if r.excel_row is None:
                # แถวที่เพิ่มเองไม่มีชื่อกิจกรรมจาก Excel — เก็บชื่อที่เลือกไว้เขียนลงคอลัมน์ activity ตอน append
                r.activity = c.itemText(i) if r.activity_id else ""
            self._on_row_resolution_changed(idx)

        combo.currentIndexChanged.connect(on_changed)
        return combo

    def _comment_widget(self, row: RowState) -> QWidget:
        editor = QLineEdit(row.comment or "")
        editor.setPlaceholderText("หมายเหตุ...")

        def on_changed(text: str, r=row):
            r.comment = text

        editor.textChanged.connect(on_changed)
        return editor

    def _wp_widget(self, row: RowState, index: int) -> QWidget:
        if self.picker_ctx is None:
            if row.wp_id:
                text = f"#{row.wp_id} {row.wp_subject or ''} ({row.wp_project or ''})"
            else:
                text = "ยังไม่ได้เลือก Work Package"
            return self._plain_label(text)

        picker = WorkPackagePickerWidget(row, self.picker_ctx)
        picker.row_changed.connect(lambda idx=index: self._on_row_resolution_changed(idx))
        return picker

    def _wp_cell(self, row: RowState, index: int) -> QWidget:
        """คอลัมน์ "งาน" = picker ตัวเดิม + บรรทัดคำเตือนใต้มันที่ซ่อนไว้ตั้งแต่ต้น

        label ถูกสร้างครั้งเดียวพร้อมแถวแล้วเก็บ reference ไว้ที่ _warn_labels — set_check_result
        จึงแค่ setText/show/hide ได้ ไม่ต้องสร้าง cell ใหม่ ซึ่งจะทำ WorkPackagePickerWidget
        (พร้อม state คำค้น/หน้า/โปรเจกต์ที่ผู้ใช้ตั้งไว้) ตายทั้งตารางทุกครั้งที่กดเช็ค"""
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._wp_widget(row, index))

        label = QLabel()
        label.setContentsMargins(10, 0, 10, 2)
        label.hide()
        layout.addWidget(label)
        self._warn_labels[index] = label
        self._apply_row_warning(index, row)
        return wrapper

    def _row_height_for(self, row: RowState) -> int:
        height = EXPANDED_ROW_HEIGHT if row.mode == "new" else COMPACT_ROW_HEIGHT
        return height + WARN_LINE_HEIGHT if self._row_warning(row) else height

    def _on_row_resolution_changed(self, index: int) -> None:
        row = self.rows[index]
        self.refresh_row_warning(index)
        if not row.is_resolved():
            # แถวเพิ่งกลายเป็นไม่พร้อมส่ง (เช่นแก้ WP ออก) — เลิกติ๊กไว้ ไม่งั้นจะค้าง selected=True
            # ทั้งที่ checkbox กำลังจะถูก disable ไปพร้อมกัน
            row.selected = False
        self._sync_select_widget(index, row)
        if self.on_wp_changed:
            self.on_wp_changed(index)

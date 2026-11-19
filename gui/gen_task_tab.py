"""Bulk task-template generator — helps fill the upload Excel format faster; does NOT call the
OpenProject API at all. Every generated row is wp_mode="new"/synced=False — the actual work
package still gets picked or created for real later, when the file is opened via
"⬆ อัปโหลดไฟล์ใหม่" in the sync tab.

Two switchable input modes (same wpTab/QStackedWidget toggle pattern WorkPackagePickerWidget
uses for its "existing/new" switch): shared values applied to every subject line, or a fully
independent per-row table. Row removal in the table mode goes through QTableWidget.removeRow()
specifically (not a clear-and-rebuild loop) so Qt handles cell-widget cleanup correctly.
"""
from __future__ import annotations

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from common import PASTE_COLUMN_HELP, generate_task_template, parse_pasted_rows
from gui.busy import make_busy_bar, set_button_busy
from gui.state import AppState
from gui.theme import FONT_MONO, tune_calendar_popup
from gui.threads import run_async

DATE_FORMAT = "yyyy-MM-dd"
TABLE_COLUMN_LABELS = ["ลบ", "วันที่", "ชั่วโมง", "กิจกรรม", "โปรเจกต์ใหม่", "ชื่องาน", "หมายเหตุ"]
MAX_PREVIEW_LINES = 10  # จำนวนบรรทัด error สูงสุดที่แสดงในกล่องแจ้งเตือน
TABLE_ROW_HEIGHT = 34   # default 30px ของ Qt ตัดหางตัวอักษร (เช่นขีดล่างใน SDB_General)


class GenTaskTab(QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self._mode = "shared"
        self._table_rows: list[dict] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        # สร้างปุ่มนี้ไว้ก่อน (ยังไม่ addWidget) เพราะ _build_table_panel() ด้านล่างสร้างแถวแรก
        # ทันทีซึ่งเรียก _update_generate_enabled() ที่อ้างถึง self.generate_btn — ตำแหน่งจริงบน
        # หน้าจอถูกกำหนดตอน layout.addWidget(self.generate_btn) ท้ายเมธอดนี้ต่างหาก
        self.generate_btn = QPushButton("สร้างไฟล์ Template")
        self.generate_btn.setObjectName("primaryButton")
        self.generate_btn.setCursor(Qt.PointingHandCursor)
        self.generate_btn.clicked.connect(self._on_generate_clicked)

        mode_bar = QHBoxLayout()
        self.shared_mode_btn = QPushButton("กรอกค่าเดียวทุกแถว")
        self.shared_mode_btn.setObjectName("wpTab")
        self.shared_mode_btn.setCursor(Qt.PointingHandCursor)
        self.shared_mode_btn.clicked.connect(lambda: self._set_mode("shared"))
        mode_bar.addWidget(self.shared_mode_btn)

        self.table_mode_btn = QPushButton("ตารางรายแถว")
        self.table_mode_btn.setObjectName("wpTab")
        self.table_mode_btn.setCursor(Qt.PointingHandCursor)
        self.table_mode_btn.clicked.connect(lambda: self._set_mode("table"))
        mode_bar.addWidget(self.table_mode_btn)
        mode_bar.addStretch()
        layout.addLayout(mode_bar)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, stretch=1)
        self.stack.addWidget(self._build_shared_panel())
        self.stack.addWidget(self._build_table_panel())

        gen_bar = QHBoxLayout()
        gen_bar.addWidget(self.generate_btn)
        self.generate_progress = make_busy_bar(110)
        gen_bar.addWidget(self.generate_progress)
        gen_bar.addStretch()
        layout.addLayout(gen_bar)

        self._set_mode("shared")

    # -- mode toggle ------------------------------------------------------------

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        self.shared_mode_btn.setProperty("active", mode == "shared")
        self.table_mode_btn.setProperty("active", mode == "table")
        for btn in (self.shared_mode_btn, self.table_mode_btn):
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self.stack.setCurrentIndex(0 if mode == "shared" else 1)
        self._update_generate_enabled()

    def _field_label(self, text: str) -> QWidget:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    # -- โหมด A: ค่าเดียวใช้กับทุกแถว --------------------------------------------

    def _build_shared_panel(self) -> QWidget:
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(10)

        fields = QHBoxLayout()

        date_col = QVBoxLayout()
        date_col.addWidget(self._field_label("วันที่ (ใช้ทุกแถว)"))
        self.shared_date = QDateEdit()
        self.shared_date.setCalendarPopup(True)
        tune_calendar_popup(self.shared_date)
        self.shared_date.setDisplayFormat(DATE_FORMAT)
        self.shared_date.setDate(QDate.currentDate())
        date_col.addWidget(self.shared_date)
        fields.addLayout(date_col)

        hours_col = QVBoxLayout()
        hours_col.addWidget(self._field_label("ชั่วโมง (ใช้ทุกแถว)"))
        self.shared_hours = QDoubleSpinBox()
        self.shared_hours.setDecimals(2)
        self.shared_hours.setSingleStep(0.5)
        self.shared_hours.setMinimum(0.0)
        self.shared_hours.setMaximum(24.0)
        self.shared_hours.setValue(1.0)
        hours_col.addWidget(self.shared_hours)
        fields.addLayout(hours_col)

        activity_col = QVBoxLayout()
        activity_col.addWidget(self._field_label("กิจกรรม (ใช้ทุกแถว)"))
        self.shared_activity = QLineEdit()
        self.shared_activity.setPlaceholderText("เช่น Normal, Development...")
        activity_col.addWidget(self.shared_activity)
        fields.addLayout(activity_col)

        project_col = QVBoxLayout()
        project_col.addWidget(self._field_label("โปรเจกต์ใหม่ (ใช้ทุกแถว)"))
        self.shared_project = QLineEdit()
        self.shared_project.setPlaceholderText("ชื่อโปรเจกต์...")
        project_col.addWidget(self.shared_project)
        fields.addLayout(project_col)

        panel_layout.addLayout(fields)

        panel_layout.addWidget(self._field_label("ชื่องาน (บรรทัดละ 1 งาน)"))
        self.subjects_edit = QPlainTextEdit()
        self.subjects_edit.setPlaceholderText("พิมพ์หรือวางชื่องาน บรรทัดละ 1 งาน...")
        self.subjects_edit.textChanged.connect(self._update_generate_enabled)
        panel_layout.addWidget(self.subjects_edit, stretch=1)

        return panel

    # -- โหมด B: ตารางรายแถว ------------------------------------------------------

    def _build_table_panel(self) -> QWidget:
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(10)

        self.table = QTableWidget()
        self.table.setColumnCount(len(TABLE_COLUMN_LABELS))
        self.table.setHorizontalHeaderLabels(TABLE_COLUMN_LABELS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(1, 140)  # วันที่ — 120 ตัดท้ายวันที่ ดู table_controller
        self.table.setColumnWidth(2, 80)
        self.table.setColumnWidth(3, 120)
        self.table.setColumnWidth(4, 150)
        self.table.setColumnWidth(6, 200)
        panel_layout.addWidget(self.table, stretch=1)

        row_actions = QHBoxLayout()
        add_row_btn = QPushButton("+ เพิ่มแถว")
        add_row_btn.setObjectName("outlineButton")
        add_row_btn.setCursor(Qt.PointingHandCursor)
        add_row_btn.clicked.connect(lambda: self._add_table_row())
        row_actions.addWidget(add_row_btn)

        paste_btn = QPushButton("📋 วางจาก Excel")
        paste_btn.setObjectName("outlineButton")
        paste_btn.setCursor(Qt.PointingHandCursor)
        paste_btn.setToolTip(f"วางหลายแถวพร้อมกัน คั่นด้วย Tab: {PASTE_COLUMN_HELP}")
        paste_btn.clicked.connect(self._on_paste_clicked)
        row_actions.addWidget(paste_btn)
        row_actions.addStretch()
        panel_layout.addLayout(row_actions)

        self._add_table_row()
        return panel

    def _add_table_row(self, values: dict | None = None) -> None:
        """เพิ่มแถวเปล่า หรือแถวที่กรอกค่าไว้แล้ว (ใช้ตอนวางจาก Excel)"""
        values = values or {}
        row = {
            "date": QDateEdit(),
            "hours": QDoubleSpinBox(),
            "activity": QLineEdit(),
            "project": QLineEdit(),
            "subject": QLineEdit(),
            "comment": QLineEdit(),
        }
        row["date"].setCalendarPopup(True)
        tune_calendar_popup(row["date"])
        row["date"].setDisplayFormat(DATE_FORMAT)
        parsed = QDate.fromString(values.get("date", ""), DATE_FORMAT)
        row["date"].setDate(parsed if parsed.isValid() else QDate.currentDate())

        row["hours"].setDecimals(2)
        row["hours"].setSingleStep(0.5)
        row["hours"].setMinimum(0.0)
        row["hours"].setMaximum(24.0)
        row["hours"].setValue(float(values.get("hours") or 1.0))

        for key in ("activity", "project", "subject", "comment"):
            row[key].setText(values.get(key, "") or "")

        row["subject"].textChanged.connect(self._update_generate_enabled)

        index = len(self._table_rows)
        self._table_rows.append(row)
        self.table.setRowCount(len(self._table_rows))

        remove_btn = QPushButton("✕")
        remove_btn.setObjectName("linkButton")
        remove_btn.setCursor(Qt.PointingHandCursor)
        remove_btn.clicked.connect(lambda _=False, r=row: self._remove_table_row(r))

        self.table.setCellWidget(index, 0, remove_btn)
        self.table.setCellWidget(index, 1, row["date"])
        self.table.setCellWidget(index, 2, row["hours"])
        self.table.setCellWidget(index, 3, row["activity"])
        self.table.setCellWidget(index, 4, row["project"])
        self.table.setCellWidget(index, 5, row["subject"])
        self.table.setCellWidget(index, 6, row["comment"])
        self.table.setRowHeight(index, TABLE_ROW_HEIGHT)
        self._update_generate_enabled()

    def _remove_table_row(self, row: dict) -> None:
        if row not in self._table_rows:
            return
        index = self._table_rows.index(row)
        self._table_rows.pop(index)
        self.table.removeRow(index)  # ใช้ removeRow แถวเดียว ไม่ clear-and-rebuild ทั้งตาราง
        self._update_generate_enabled()

    # -- generate -----------------------------------------------------------------

    def _update_generate_enabled(self) -> None:
        self.generate_btn.setEnabled(bool(self._collect_rows()))

    def _collect_rows(self) -> list[dict]:
        if self._mode == "shared":
            date_str = self.shared_date.date().toString(DATE_FORMAT)
            hours = self.shared_hours.value()
            activity = self.shared_activity.text().strip()
            project = self.shared_project.text().strip()
            subjects = [s.strip() for s in self.subjects_edit.toPlainText().splitlines() if s.strip()]
            return [
                {"date": date_str, "hours": hours, "activity": activity, "project": project, "subject": s}
                for s in subjects
            ]

        rows = []
        for row in self._table_rows:
            subject = row["subject"].text().strip()
            if not subject:
                continue
            rows.append({
                "date": row["date"].date().toString(DATE_FORMAT),
                "hours": row["hours"].value(),
                "activity": row["activity"].text().strip(),
                "project": row["project"].text().strip(),
                "subject": subject,
                "comment": row["comment"].text().strip(),
            })
        return rows

    # -- วางจาก Excel -------------------------------------------------------------

    def _on_paste_clicked(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("วางข้อมูลจาก Excel")
        dialog.resize(680, 420)
        layout = QVBoxLayout(dialog)
        layout.setSpacing(10)

        help_label = QLabel(
            f"คัดลอกช่วงเซลล์จาก Excel/Google Sheets มาวางได้เลย — คอลัมน์คั่นด้วย Tab ตามลำดับ:\n"
            f"{PASTE_COLUMN_HELP}\n"
            "3 คอลัมน์แรกต้องมีเสมอ · วันที่ใช้ YYYY-MM-DD หรือ DD/MM/YYYY · แถวหัวตารางข้ามให้อัตโนมัติ"
        )
        help_label.setObjectName("fieldLabel")
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        editor = QPlainTextEdit()
        editor.setPlaceholderText("2026-07-01\t4\tเขียนเอกสาร flow\tRTAF_Email 2026\tหมายเหตุเพิ่มเติม")
        # 1 บรรทัด = 1 แถวเสมอ และคอลัมน์ที่คั่นด้วย Tab เรียงตรงกัน — ตรวจด้วยตาได้ทันทีว่าวาง
        # มากี่คอลัมน์ (ถ้าปล่อยให้ตัดบรรทัด คอลัมน์ท้ายจะตกไปบรรทัดถัดไปจนดูไม่ออกว่าถูกไหม)
        editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        editor.setFont(QFont(FONT_MONO, 10))
        layout.addWidget(editor, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("แปลงเป็นตาราง")
        buttons.button(QDialogButtonBox.Cancel).setText("ยกเลิก")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return

        rows, errors = parse_pasted_rows(editor.toPlainText())

        if errors:
            detail = "\n".join(errors[:MAX_PREVIEW_LINES])
            if len(errors) > MAX_PREVIEW_LINES:
                detail += f"\n... และอีก {len(errors) - MAX_PREVIEW_LINES} บรรทัด"
            if not rows:
                QMessageBox.warning(self, "Timesheet Sync", f"อ่านข้อมูลไม่ได้เลย\n\n{detail}")
                return
            answer = QMessageBox.question(
                self,
                "Timesheet Sync",
                f"อ่านได้ {len(rows)} แถว · อ่านไม่ได้ {len(errors)} บรรทัด\n\n{detail}\n\n"
                "เอาเฉพาะแถวที่อ่านได้ต่อไหม?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        elif not rows:
            QMessageBox.information(self, "Timesheet Sync", "ไม่พบข้อมูลในข้อความที่วางมา")
            return

        # แถวเปล่าที่ระบบสร้างไว้ตอนเปิดแท็บจะเกะกะ ถ้ายังไม่ได้กรอกอะไรเลยให้เอาออกก่อนต่อท้าย
        for existing in list(self._table_rows):
            if not existing["subject"].text().strip():
                self._remove_table_row(existing)

        for row in rows:
            self._add_table_row(row)

        QMessageBox.information(
            self, "Timesheet Sync", f"เพิ่ม {len(rows)} แถวลงตารางแล้ว ตรวจ/แก้ไขได้ก่อนกดสร้างไฟล์"
        )

    def _on_generate_clicked(self) -> None:
        rows = self._collect_rows()
        if not rows:
            return

        default_name = f"task_template_{QDate.currentDate().toString(DATE_FORMAT)}.xlsx"
        path, _ = QFileDialog.getSaveFileName(
            self, "บันทึกไฟล์ Template", default_name, "Excel files (*.xlsx)"
        )
        if not path:
            return

        self._set_generate_busy(True)
        run_async(
            generate_task_template,
            rows,
            self.state.config["excel"],
            path,
            on_success=lambda _r, p=path: self._on_generate_success(p),
            on_error=self._on_generate_error,
        )

    def _set_generate_busy(self, running: bool) -> None:
        set_button_busy(self.generate_btn, running, "กำลังสร้าง...", "สร้างไฟล์ Template")
        self.generate_progress.setVisible(running)
        if not running:
            # ปุ่มกดได้เฉพาะตอนมีแถวให้สร้าง — set_button_busy เปิดคืนให้เสมอ ต้องคิดใหม่
            self._update_generate_enabled()

    def _on_generate_success(self, path: str) -> None:
        self._set_generate_busy(False)
        QMessageBox.information(
            self,
            "Timesheet Sync",
            f'สร้างไฟล์สำเร็จ: {path}\n\n'
            'ไปที่ tab "อัปโหลด & ส่งเข้า OpenProject" แล้วกด "⬆ อัปโหลดไฟล์ใหม่" เพื่อเปิดไฟล์นี้ต่อ',
        )

    def _on_generate_error(self, message: str) -> None:
        self._set_generate_busy(False)
        QMessageBox.warning(self, "Timesheet Sync", f"สร้างไฟล์ไม่สำเร็จ: {message}")

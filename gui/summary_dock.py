"""Bottom dock — recomputed summary (new-WP count / total hours / >8h-per-day warnings) plus
submit-button gating. Ported from renderSummary/updateSubmitButton in static/app.js."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from gui.busy import make_progress_bar, set_button_busy, set_progress
from gui.row_model import RowState
from gui.theme import COLORS


class SummaryDock(QWidget):
    submit_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(8)

        self.banner_label = QLabel()
        self.banner_label.setWordWrap(True)
        self.banner_label.hide()
        layout.addWidget(self.banner_label)

        # label สองบรรทัดแรกเปลี่ยนแค่ตัวเลข จึงถือไว้ถาวรแล้ว setText แทนการทำลาย/สร้างใหม่ —
        # update_summary ถูกเรียกทุกครั้งที่ติ๊ก checkbox และทุกตัวอักษรที่พิมพ์ในช่องชื่องานใหม่
        self.summary_container = QVBoxLayout()
        self.summary_container.setSpacing(2)
        self.new_count_label = QLabel()
        self.total_label = QLabel()
        self.summary_container.addWidget(self.new_count_label)
        self.summary_container.addWidget(self.total_label)
        # label เตือน ">8 ชม./วัน" จำนวนไม่คงที่ — reuse ตามที่ต้องใช้ ซ่อนส่วนเกินไว้
        self._warn_labels: list[QLabel] = []
        # {วันที่: {"hours", "count"}} ที่ลงไปแล้วบน OpenProject — ว่างจนกว่าจะกดปุ่มเช็ค
        self._logged_by_date: dict[str, dict] = {}
        # สรุปผลเช็คล่าสุด ต่อท้าย missing_label — เก็บไว้เพราะ update_gating ถูกเรียกซ้ำทุกครั้ง
        # ที่ติ๊ก checkbox ถ้าเขียน missing_label ตรงๆ โน้ตจะหายไปในการติ๊กครั้งถัดไป
        self._check_note = ""
        layout.addLayout(self.summary_container)

        actions = QHBoxLayout()
        # ข้อความตั้งต้นเก็บไว้ที่นี่ที่เดียว — set_submitting ต้องคืนค่าเดิมให้ปุ่มทุกครั้งที่จบ
        self.submit_default_text = "ยืนยันและส่งเข้า OpenProject"
        self.submit_btn = QPushButton(self.submit_default_text)
        self.submit_btn.setObjectName("primaryButton")
        self.submit_btn.setEnabled(False)
        self.submit_btn.clicked.connect(self.submit_clicked.emit)
        actions.addWidget(self.submit_btn)

        # จำนวนแถวรู้ล่วงหน้า ส่งทีละแถว — หลอดนี้จึงเป็น % จริง ไม่ใช่การเดาเวลา
        self.submit_progress = make_progress_bar(180)
        actions.addWidget(self.submit_progress)

        self.missing_label = QLabel()
        self.missing_label.setObjectName("fieldLabel")
        actions.addWidget(self.missing_label)
        actions.addStretch()

        layout.addLayout(actions)

    def set_submit_busy(self, running: bool, text: str = "กำลังส่ง...") -> None:
        """ปุ่มส่งกำลังทำงานแต่ยังไม่รู้จำนวนงาน (เช่นช่วงตรวจชื่องานซ้ำ) — ยังไม่โชว์หลอด
        เป็นทางเดียวที่คืนปุ่มกลับสภาพเดิม ทุก path ที่จบต้องเรียก set_submit_busy(False)"""
        set_button_busy(self.submit_btn, running, text, self.submit_default_text)
        if not running:
            self.submit_progress.setVisible(False)

    def set_submit_progress(self, done: int, total: int) -> None:
        """ระหว่างส่งจริง: ปุ่มบอกว่าถึงแถวไหนแล้ว หลอดเดินตามจำนวนแถวที่ส่งไปจริง"""
        self.submit_btn.setText(f"กำลังส่ง... {done}/{total}" if total else "กำลังส่ง...")
        self.submit_progress.setVisible(True)
        set_progress(self.submit_progress, done, total)

    def show_banner(self, success: int, failed: int) -> None:
        self.banner_label.show()
        text = f"สำเร็จ {success} แถว · ล้มเหลว {failed} แถว"
        if failed > 0:
            text += " — ดูรายละเอียด error ที่คอลัมน์ sync_note ในไฟล์ Excel"
            color, bg = COLORS["warn"], COLORS["warn_bg"]
        else:
            color, bg = COLORS["success"], COLORS["success_bg"]
        self.banner_label.setStyleSheet(
            f"background: {bg}; color: {color}; border-radius: 8px; padding: 8px 12px; font-size: 13px; font-weight: 500;"
        )
        self.banner_label.setText(text)

    def hide_banner(self) -> None:
        self.banner_label.hide()

    def set_check_note(self, text: str) -> None:
        self._check_note = text or ""

    def clear(self) -> None:
        # สถานะ "ยังไม่ได้เลือกไฟล์" — ไม่มีอะไรให้สรุป จึงไม่ควรขึ้นตัวเลข 0 หรือ "ไม่มีแถวที่ต้อง sync"
        self._clear_summary_rows()
        self._check_note = ""
        self.missing_label.setText("")
        self.submit_btn.setEnabled(False)
        self.hide_banner()

    def _clear_summary_rows(self) -> None:
        self.new_count_label.setText("")
        self.total_label.setText("")
        for label in self._warn_labels:
            label.hide()

    def set_logged_by_date(self, logged_by_date: dict) -> None:
        """ชั่วโมงที่ลงไปแล้วบน OpenProject รายวัน จาก common.check_existing_work

        ว่างไว้จนกว่าผู้ใช้จะกดปุ่มเช็ค — ตอนว่างคำเตือน 8 ชม./วัน ทำงานเหมือนเดิมทุกอย่าง
        ผู้เรียกต้อง update_summary() ต่อเองเพื่อวาดใหม่
        """
        self._logged_by_date = logged_by_date or {}

    def update_summary(self, rows: list[RowState]) -> None:
        new_count = sum(1 for r in rows if r.mode == "new")
        total_hours = 0.0
        per_date: dict[str, float] = {}
        for r in rows:
            hours = r.hours or 0
            total_hours += hours
            if r.date:
                per_date[r.date] = per_date.get(r.date, 0) + hours

        self.new_count_label.setText(f"จะสร้างงานใหม่ {new_count} งาน")
        self.total_label.setText(f"บันทึกชั่วโมงรวม {total_hours:.1f} ชม. ใน {len(rows)} รายการ")

        # นับชั่วโมงที่ลงไปแล้วบนเซิร์ฟเวอร์รวมด้วย ไม่ใช่แค่ที่อยู่ในไฟล์ — ไฟล์ที่มี 8 ชม.
        # ของวันที่เคยลงไปแล้วครบ 8 ชม. คือการลงซ้ำ ซึ่งเดิมผ่านฉลุยโดยไม่มีคำเตือนอะไรเลย
        logged = {d: (v or {}).get("hours") or 0.0 for d, v in self._logged_by_date.items()}
        over = sorted(d for d in per_date if per_date[d] + logged.get(d, 0.0) > 8)
        for i, date in enumerate(over):
            if i == len(self._warn_labels):
                label = QLabel()
                label.setStyleSheet(f"color: {COLORS['warn']}; font-weight: 500;")
                self.summary_container.addWidget(label)
                self._warn_labels.append(label)
            label = self._warn_labels[i]
            already = logged.get(date, 0.0)
            # ลงซ้ำของจริง (มีชั่วโมงบนเซิร์ฟเวอร์อยู่แล้ว) ร้ายกว่าไฟล์ที่เผลอใส่เกิน 8 ชม. — ใช้สีแดง
            color = COLORS["error"] if already else COLORS["warn"]
            label.setStyleSheet(f"color: {color}; font-weight: 500;")
            if already:
                text = (
                    f"⚠ วันที่ {date}: ในไฟล์ {per_date[date]:.1f} + ลงไปแล้ว {already:.1f}"
                    f" = {per_date[date] + already:.1f} ชม."
                )
            else:
                text = f"⚠ วันที่ {date} รวม {per_date[date]:.1f} ชม. เกิน 8 ชม./วัน ตรวจสอบอีกครั้ง"
            label.setText(text)
            label.show()
        for label in self._warn_labels[len(over):]:
            label.hide()

    def update_gating(self, rows: list[RowState]) -> None:
        total = len(rows)
        unresolved = sum(1 for r in rows if not r.is_resolved())
        ready = [r for r in rows if r.selected and r.is_resolved()]
        self.submit_btn.setEnabled(bool(ready))

        if total == 0:
            text = "ไม่มีแถวที่ต้อง sync"
        elif ready:
            text = f"จะส่ง {len(ready)} จาก {total} รายการ"
        elif unresolved < total:
            # มีแถวพร้อมส่งอยู่ แต่ไม่มีแถวไหนถูกติ๊กเลย
            text = "ยังไม่ได้เลือกแถวที่จะส่ง"
        else:
            text = f"ยังขาดอีก {unresolved} แถว"
        self.missing_label.setText(f"{text} · {self._check_note}" if self._check_note else text)

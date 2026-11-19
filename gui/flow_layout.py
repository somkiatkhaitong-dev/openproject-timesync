"""Layout ที่ไหลไปบรรทัดใหม่เองเมื่อของในบรรทัดเดิมไม่พอ

QHBoxLayout บีบ widget ทุกตัวให้ลงในบรรทัดเดียวเสมอ — พอชิปสรุปงานมีชื่อภาษาไทยยาวๆ หลายตัว
ผลคือทุกตัวโดนย่อจนอ่านไม่ออกและตัวท้ายทะลุขอบหน้าต่าง ตัวนี้ให้แต่ละตัวได้ความกว้างตาม
sizeHint ของมันจริงๆ แล้วขึ้นบรรทัดใหม่เมื่อเต็ม

PySide6 ไม่มี FlowLayout มาให้ (ใน Qt เป็นแค่โค้ดตัวอย่าง) จึงต้องเขียนเอง — เมธอดที่ override
ทั้งหมดเป็นสัญญาขั้นต่ำที่ QLayout บังคับ ไม่ใช่ของเกิน
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QSizePolicy, QWidget


class FlowLayout(QLayout):
    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items: list = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    # -- สัญญาของ QLayout ---------------------------------------------------

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientations:
        return Qt.Orientations(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(
            margins.left() + margins.right(), margins.top() + margins.bottom()
        )

    # -- การจัดวางจริง ------------------------------------------------------

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        """วางของทีละตัวจากซ้ายไปขวา ขึ้นบรรทัดใหม่เมื่อชนขอบขวา คืนความสูงที่ใช้จริง

        test_only=True คือโหมดวัดความสูงให้ heightForWidth โดยไม่ขยับ widget จริง — Qt เรียก
        ตอนคำนวณ layout ก่อนจะรู้ตำแหน่งสุดท้าย
        """
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        space = self.spacing()
        x, y, line_height = area.x(), area.y(), 0

        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + space
            # line_height > 0 = มีของอยู่ในบรรทัดนี้แล้ว ถึงจะขึ้นบรรทัดใหม่ได้ — ไม่งั้นของที่
            # กว้างกว่าพื้นที่ทั้งหมดจะดันตัวเองขึ้นบรรทัดใหม่ไปเรื่อยๆ โดยไม่มีอะไรอยู่ข้างบน
            if next_x - space > area.right() and line_height > 0:
                x = area.x()
                y = y + line_height + space
                next_x = x + hint.width() + space
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())

        return y + line_height - rect.y() + margins.bottom()


class FlowWidget(QWidget):
    """ตัวห่อ FlowLayout สำหรับใส่ใน QVBoxLayout

    ใส่ FlowLayout เป็น layout ซ้อน layout ตรงๆ ไม่ได้ — QBoxLayout อ่าน heightForWidth จาก
    "widget" ที่อยู่ในนั้น ไม่ได้อ่านจาก sub-layout ผลคือแถวสูงเท่าบรรทัดเดียวเสมอ บรรทัดที่
    ไหลลงมาจึงโดนตารางข้างล่างทับ
    """

    def __init__(self, spacing: int = 6, parent=None):
        super().__init__(parent)
        self.flow = FlowLayout(self, spacing)
        policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self.flow.heightForWidth(width)

    def sizeHint(self) -> QSize:
        return QSize(self.width(), self.flow.heightForWidth(self.width()))

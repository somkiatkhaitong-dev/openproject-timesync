"""ตัวช่วยสร้าง QStandardItemModel สำหรับ QComboBox ที่ "ใช้ร่วมกันหลายตัว"

เดิมทุก combo ในตารางเติมรายการของตัวเองทีละ addItem ซึ่งแพงมากเมื่อรายการยาวและมีหลายแถว:
วัดได้ 100 combo × 300 โปรเจกต์ = 448 ms เทียบกับ 24 ms ถ้าสร้าง model ตัวเดียวแล้ว setModel
ให้ทุกตัว (18 เท่า) — currentIndex ยังแยกกันต่อ combo และ itemData()/currentData()/findData()
ให้ผลเหมือน addItem ทุกประการ เพราะ QComboBox อ่าน data จาก Qt.UserRole อยู่แล้ว

ข้อควรระวังของ model ที่ใช้ร่วมกัน: **ห้ามเรียก combo.clear()** — มันล้าง model ที่ combo อื่น
ใช้อยู่ด้วย ให้ setModel() ไปที่ model อื่นแทน (ดู _type_placeholder_model ใน wp_picker_widget)
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel


def make_item(text: str, data=None) -> QStandardItem:
    item = QStandardItem(text)
    item.setData(data, Qt.UserRole)
    return item


def build_combo_model(entries, placeholder=None, parent=None) -> QStandardItemModel:
    """entries = [(text, data), ...]  placeholder = (text, data) ที่จะถูกใส่เป็นแถวแรก"""
    model = QStandardItemModel(parent)
    rows = list(entries)
    if placeholder is not None:
        rows.insert(0, placeholder)
    for text, data in rows:
        model.appendRow(make_item(text, data))
    return model


def set_combo_model(combo, model, index: int = 0) -> None:
    """สลับ model แล้วตั้ง currentIndex โดยไม่ปล่อย currentIndexChanged ออกไป

    setModel() ปล่อย currentIndexChanged เองเสมอ ถ้าไม่บล็อกไว้ handler ของแถวจะถูกเรียก
    ระหว่างวาดตาราง แล้วไปเขียนทับ RowState ด้วยค่าของ item แรกใน model
    """
    combo.blockSignals(True)
    combo.setModel(model)
    combo.setCurrentIndex(index)
    combo.blockSignals(False)

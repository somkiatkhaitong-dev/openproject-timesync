"""Central color palette + QSS — Claude design language (warm ivory canvas, clay accent).

แทนที่ชุดสีเดิม (เทาฟ้า + เขียว teal ที่ยกมาจาก static/style.css) ด้วยโทนของ Claude:
พื้นหลังครีมอุ่น, การ์ดขาว, เส้นขอบโทนอุ่น, สีหลักเป็น clay/terracotta

หมายเหตุ 2 ข้อที่เป็นข้อจำกัดของ Qt (ไม่ใช่การเลือกดีไซน์):
- QSS ไม่มี box-shadow — ความ "ลอย" ของการ์ดจึงมาจากพื้นขาวบนครีม + เส้นขอบ 1px เท่านั้น
- QSS ไม่มี letter-spacing — หัวเรื่องใช้ขนาด/น้ำหนักตัวอักษรคุมจังหวะแทน
"""
from __future__ import annotations

import os

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QComboBox, QDateEdit, QSpinBox

from gui.paths import resource_path

COLORS = {
    # -- พื้นผิว ------------------------------------------------------------
    "bg": "#F0EEE6",          # ผืนพื้นครีม (oat) ของ Claude
    "bg_subtle": "#F7F5EF",   # แถบหัวตาราง/พื้นรอง — อยู่กึ่งกลางระหว่าง bg กับ card
    "card": "#FFFFFF",
    "border": "#E0DDD3",      # เส้นขอบโทนอุ่น
    "border_strong": "#D0CCC0",  # ขอบตอน hover / ขอบ checkbox ที่ต้องเห็นชัดกว่า
    "grid": "#EDEBE3",        # เส้นตารางจางกว่า border เพื่อลดความเป็น spreadsheet

    # -- ตัวอักษร -----------------------------------------------------------
    "text": "#1F1E1D",        # ดำอมน้ำตาล ไม่ใช่ดำสนิท
    "text_muted": "#73716B",

    # -- สีหลัก (clay) -------------------------------------------------------
    "accent": "#C96442",         # ใช้เป็น "พื้น" ของปุ่ม/ขอบ/จุดโฟกัส
    "accent_text": "#A34D2E",    # ใช้เป็น "ตัวอักษร" บนพื้นสว่าง — เข้มกว่าเพื่อให้อ่านออก (~5.8:1)
    "accent_hover": "#B2563A",
    "accent_active": "#9B4A31",
    "accent_bg": "#F7EDE7",
    "accent_border": "#E6CBBD",

    # -- สีสถานะ ------------------------------------------------------------
    # เขียวยังต้องเป็นเขียว: badge "พร้อมส่ง" อยู่ติดกับ badge "ต้องเลือก" (ส้ม) ถ้าใช้ clay
    # เป็นสีสำเร็จ สองสถานะจะกลายเป็นสีเดียวกันในสายตา — เลยเลือกเขียวอมเทาที่เข้าโทนอุ่น
    "success": "#4A6B4F",
    "success_bg": "#EAF0E6",
    "warn": "#9A6206",
    "warn_bg": "#F7EEDA",
    "error": "#B3392C",
    "error_bg": "#FAE9E5",
    "error_border": "#F0D5CF",
}

# Segoe UI ไม่ใช่ฟอนต์ของ Claude (Styrene/Copernicus ไม่มีสิทธิ์แจกจ่าย) แต่เปลี่ยนไม่ได้จริง —
# UI เป็นภาษาไทยทั้งหมด และ Segoe UI คือตัวที่ Windows fallback ไป Leelawadee UI ให้อัตโนมัติ
FONT_FAMILY = "Segoe UI"
FONT_MONO = "Consolas"


def _check_image_rule() -> str:
    """บรรทัด image: สำหรับเครื่องหมายถูกใน checkbox — QSS วาดเครื่องหมายถูกเองไม่ได้

    คืนสตริงว่างถ้าหาไฟล์ไม่เจอ (กลับไปเป็นสี่เหลี่ยมทึบแบบเดิม ไม่ทำให้แอปพัง)
    """
    path = resource_path(os.path.join("assets", "check.svg"))
    if not os.path.exists(path):
        return ""
    # backslash ของ Windows ทำให้ url() ของ Qt พังเงียบๆ — ไม่ error แค่ไม่ขึ้นรูป
    return f"image: url({path.replace(os.sep, '/')});"


def _arrow_rules() -> str:
    """กฎของลูกศร dropdown/spinbox — พอ QSS แตะ ::drop-down หรือ ::up-button เมื่อไหร่ Qt จะเลิก
    วาดหัวลูกศรของ style เดิมให้ทันที ต้องแนบรูปเองทั้งชุด กฎจัดตำแหน่งกับกฎรูปจึงต้องมาคู่กัน

    คืนสตริงว่างถ้าหาไฟล์ไม่เจอ — ปล่อยให้ Qt วาดลูกศรเดิม (หน้าตาไม่เนียนเท่า แต่ไม่หายไปทั้งอัน)
    """
    down = resource_path(os.path.join("assets", "chevron-down.svg"))
    up = resource_path(os.path.join("assets", "chevron-up.svg"))
    if not (os.path.exists(down) and os.path.exists(up)):
        return ""
    down_url = down.replace(os.sep, "/")
    up_url = up.replace(os.sep, "/")
    return f"""
    QComboBox::drop-down, QDateEdit::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: center right;
        border: none;
        width: 20px;
        background: transparent;
    }}
    QComboBox::down-arrow, QDateEdit::down-arrow {{
        image: url({down_url});
        width: 12px;
        height: 12px;
    }}

    /* ปุ่มขึ้น/ลงของช่องชั่วโมง ถ้าไม่จัดตำแหน่งเองจะไปวาดทับขอบมนจนเห็นเป็นขีดค้างอยู่ริมขวา */
    QDoubleSpinBox::up-button, QSpinBox::up-button,
    QDoubleSpinBox::down-button, QSpinBox::down-button {{
        subcontrol-origin: padding;
        width: 16px;
        height: 11px;
        border: none;
        background: transparent;
    }}
    QDoubleSpinBox::up-button, QSpinBox::up-button {{
        subcontrol-position: top right;
    }}
    QDoubleSpinBox::down-button, QSpinBox::down-button {{
        subcontrol-position: bottom right;
    }}
    QDoubleSpinBox::up-arrow, QSpinBox::up-arrow {{
        image: url({up_url});
        width: 11px;
        height: 11px;
    }}
    QDoubleSpinBox::down-arrow, QSpinBox::down-arrow {{
        image: url({down_url});
        width: 11px;
        height: 11px;
    }}
    """


def build_qss() -> str:
    c = COLORS
    return f"""
    QWidget {{
        background: {c['bg']};
        color: {c['text']};
        font-family: "{FONT_FAMILY}";
        font-size: 13px;
    }}
    QMainWindow, QDialog {{
        background: {c['bg']};
    }}
    /* กฎ QWidget ด้านบนทำให้ label/checkbox ระบายพื้นครีมของตัวเองทับพื้นขาวของการ์ดเป็นแถบๆ
       (specificity เท่ากับ QWidget คือ type 1 ตัว จึงต้องอยู่ "หลัง" ถึงจะชนะ) — ตัวที่ต้องมีพื้น
       จริงอย่าง badge/authError/fileBadge ระบุด้วย #objectName ซึ่ง specificity สูงกว่าอยู่แล้ว */
    QLabel, QCheckBox {{
        background: transparent;
    }}

    QWidget#card, QFrame#card {{
        background: {c['card']};
        border: 1px solid {c['border']};
        border-radius: 14px;
    }}

    QToolTip {{
        background: #262624;
        color: #F5F4EE;
        border: 1px solid #3B3A37;
        border-radius: 8px;
        padding: 5px 9px;
        font-size: 12px;
    }}

    QLabel#authLogo {{ font-size: 40px; }}
    QLabel#authTitle {{ font-size: 22px; font-weight: 600; }}
    QLabel#authSubtitle, QLabel#fieldLabel, QLabel#hintLabel {{
        color: {c['text_muted']};
        font-size: 12px;
    }}
    QLabel#hintLabel {{ margin-top: 12px; }}

    QPushButton#filterButton {{
        border: 1px solid {c['border']};
        background: {c['card']};
        color: {c['text_muted']};
        padding: 6px 15px;
        border-radius: 999px;
        font-size: 13px;
        font-weight: 400;
    }}
    QPushButton#filterButton:hover {{
        border-color: {c['border_strong']};
        color: {c['text']};
    }}
    QPushButton#filterButton[active="true"] {{
        background: {c['accent_bg']};
        border-color: {c['accent_border']};
        color: {c['accent_text']};
        font-weight: 600;
    }}

    /* เดิม pane เป็นกล่องขาวมีขอบ แล้วในนั้นมีตารางขาวมีขอบอีกชั้น — กล่องซ้อนกล่อง
       ตัด pane ทิ้งให้เหลือลำดับชั้นเดียวแบบ Claude: การ์ดขาว (ตาราง/ช่องกรอก) ลอยบนพื้นครีม
       ส่วนแท็บกลายเป็น segmented control เม็ดขาวแทนแท็บแบบแฟ้มเอกสาร */
    QTabWidget::pane {{
        border: none;
        background: transparent;
    }}
    QTabBar::tab {{
        background: transparent;
        color: {c['text_muted']};
        border: 1px solid transparent;
        border-radius: 10px;
        padding: 8px 16px;
        margin-right: 6px;
        margin-bottom: 8px;
        font-size: 13px;
    }}
    QTabBar::tab:selected {{
        background: {c['card']};
        color: {c['accent_text']};
        font-weight: 600;
        border-color: {c['border']};
    }}
    QTabBar::tab:hover:!selected {{
        background: {c['accent_bg']};
        color: {c['accent_text']};
    }}

    QPushButton#wpTab {{
        border: 1px solid {c['border']};
        background: {c['card']};
        color: {c['text_muted']};
        font-size: 12px;
        padding: 5px 12px;
        border-radius: 8px;
    }}
    QPushButton#wpTab:hover {{
        border-color: {c['border_strong']};
        color: {c['text']};
    }}
    QPushButton#wpTab[active="true"] {{
        background: {c['accent']};
        border-color: {c['accent']};
        color: #ffffff;
        font-weight: 600;
    }}

    QLabel#fileBadge {{
        font-family: "{FONT_MONO}";
        font-size: 11px;
        color: {c['text_muted']};
        background: {c['card']};
        border: 1px solid {c['border']};
        border-radius: 8px;
        padding: 3px 9px;
    }}

    QLineEdit, QDateEdit, QDoubleSpinBox, QSpinBox, QComboBox {{
        background: {c['card']};
        border: 1px solid {c['border']};
        border-radius: 8px;
        padding: 7px 10px;
        color: {c['text']};
        font-size: 13px;
        selection-background-color: {c['accent_bg']};
        selection-color: {c['text']};
    }}
    QLineEdit:hover, QDateEdit:hover, QDoubleSpinBox:hover, QSpinBox:hover, QComboBox:hover {{
        border-color: {c['border_strong']};
    }}
    QLineEdit:focus, QDateEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus {{
        border: 1px solid {c['accent']};
    }}
    QLineEdit:disabled, QDateEdit:disabled, QDoubleSpinBox:disabled,
    QSpinBox:disabled, QComboBox:disabled {{
        background: {c['bg_subtle']};
        color: {c['text_muted']};
    }}
    QLineEdit {{
        placeholder-text-color: {c['text_muted']};
    }}
    /* หมายเหตุ: ห้ามเติม padding-right เผื่อลูกศรที่นี่ — Qt หักพื้นที่ปุ่มลูกศรออกจากกรอบข้อความ
       ให้อยู่แล้ว เติมซ้ำจะกลายเป็นหักสองรอบ ข้อความยาวๆ อย่าง "2026-08-04" จะถูกตัดท้ายทิ้ง */

    /* ช่องกรอกปีในปฏิทินแคบมาก (แถบหัวปฏิทินแบ่งที่ให้เท่าที่เหลือ) — padding 7px 10px ของกฎ
       QSpinBox รวมข้างบนกินที่จนเลขปีไม่พอแสดง ลดเฉพาะในปฏิทิน ส่วนความกว้างต้องไปกันด้วยโค้ด
       ที่ tune_calendar_popup() เพราะ QSS ขยาย popup ที่ถูกล็อกขนาดไปแล้วไม่ได้ */
    QCalendarWidget QSpinBox {{
        padding: 2px 4px;
    }}

    {_arrow_rules()}

    QComboBox QAbstractItemView {{
        background: {c['card']};
        border: 1px solid {c['border']};
        border-radius: 10px;
        padding: 4px;
        outline: none;
        selection-background-color: {c['accent_bg']};
        selection-color: {c['text']};
    }}

    QPlainTextEdit, QTextEdit {{
        background: {c['card']};
        border: 1px solid {c['border']};
        border-radius: 10px;
        padding: 8px;
        color: {c['text']};
        selection-background-color: {c['accent_bg']};
        selection-color: {c['text']};
    }}
    QPlainTextEdit:focus, QTextEdit:focus {{
        border: 1px solid {c['accent']};
    }}

    QCheckBox {{
        color: {c['text_muted']};
        font-size: 12px;
        spacing: 7px;
    }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border: 1.5px solid {c['border_strong']};
        border-radius: 5px;
        background: {c['card']};
    }}
    QCheckBox::indicator:hover {{
        border-color: {c['accent']};
    }}
    QCheckBox::indicator:checked {{
        background: {c['accent']};
        border-color: {c['accent']};
        {_check_image_rule()}
    }}
    QCheckBox::indicator:disabled {{
        background: {c['bg']};
        border-color: {c['border']};
    }}
    QCheckBox::indicator:checked:disabled {{
        background: {c['border_strong']};
        border-color: {c['border_strong']};
    }}

    QMenu {{
        background: {c['card']};
        border: 1px solid {c['border']};
        border-radius: 10px;
        padding: 4px;
    }}
    QMenu::item {{
        padding: 6px 14px;
        border-radius: 6px;
        color: {c['text']};
    }}
    QMenu::item:selected {{
        background: {c['accent_bg']};
        color: {c['accent_text']};
    }}

    QScrollBar:vertical {{
        background: transparent;
        width: 12px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {c['border_strong']};
        border-radius: 4px;
        min-height: 28px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {c['text_muted']};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
        background: none;
    }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: none;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 12px;
        margin: 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: {c['border_strong']};
        border-radius: 4px;
        min-width: 28px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {c['text_muted']};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0;
        background: none;
    }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
        background: none;
    }}

    QListWidget#wpResults {{
        background: {c['card']};
        border: 1px solid {c['border']};
        border-radius: 10px;
        padding: 4px;
        outline: none;
    }}
    QListWidget#wpResults::item {{
        padding: 8px 10px;
        border-radius: 8px;
        color: {c['text']};
    }}
    QListWidget#wpResults::item:hover {{
        background: {c['bg_subtle']};
    }}
    QListWidget#wpResults::item:selected {{
        background: {c['accent_bg']};
        color: {c['text']};
    }}
    QListWidget#wpResults::item:disabled {{
        color: {c['text_muted']};
    }}

    QMessageBox QPushButton {{
        background: {c['card']};
        color: {c['text']};
        border: 1px solid {c['border']};
        border-radius: 8px;
        padding: 7px 16px;
        font-size: 13px;
        font-weight: 600;
        min-width: 70px;
    }}
    QMessageBox QPushButton:hover {{
        background: {c['accent_bg']};
        border-color: {c['accent_border']};
        color: {c['accent_text']};
    }}
    QMessageBox QPushButton:default {{
        background: {c['accent']};
        color: #ffffff;
        border: 1px solid {c['accent']};
    }}
    QMessageBox QPushButton:default:hover {{
        background: {c['accent_hover']};
        border-color: {c['accent_hover']};
        color: #ffffff;
    }}

    QPushButton {{
        border: none;
        border-radius: 10px;
        padding: 10px 20px;
        font-size: 14px;
        font-weight: 600;
        background: {c['border']};
        color: {c['text']};
    }}
    QPushButton#primaryButton {{
        background: {c['accent']};
        color: #ffffff;
    }}
    QPushButton#primaryButton:hover:!disabled {{
        background: {c['accent_hover']};
    }}
    QPushButton#primaryButton:pressed {{
        background: {c['accent_active']};
    }}
    QPushButton#primaryButton:disabled {{
        background: {c['border']};
        color: {c['text_muted']};
    }}
    QPushButton#outlineButton {{
        background: {c['card']};
        color: {c['accent_text']};
        border: 1px solid {c['accent_border']};
        border-radius: 8px;
        padding: 6px 13px;
        font-size: 12px;
    }}
    QPushButton#outlineButton:hover {{
        background: {c['accent_bg']};
        border-color: {c['accent']};
    }}
    QPushButton#outlineButton:pressed {{
        background: #F2DFD5;
    }}
    QPushButton#outlineButton:disabled {{
        background: {c['card']};
        color: {c['text_muted']};
        border-color: {c['border']};
    }}
    /* ปุ่มที่ปิดอยู่เพราะ "กำลังทำงาน" ไม่ใช่เพราะ "กดไม่ได้" — ปุ่มพวกนี้มีนาฬิกา/ข้อความ
       สถานะอยู่บนตัวมันเอง ถ้าปล่อยให้เป็นสีเทาตามกฎข้างบนจะอ่านไม่ออกและดูเหมือนปุ่มตาย
       (selector มี attribute เพิ่มมา specificity จึงสูงกว่า :disabled เปล่าๆ) */
    QPushButton#outlineButton[busy="true"]:disabled {{
        background: {c['accent_bg']};
        color: {c['accent_text']};
        border-color: {c['accent_border']};
    }}
    QPushButton#primaryButton[busy="true"]:disabled {{
        background: {c['accent_hover']};
        color: #ffffff;
    }}
    QPushButton#mutedButton {{
        background: {c['card']};
        color: {c['text_muted']};
        border: 1px solid {c['border']};
        border-radius: 8px;
        padding: 6px 13px;
        font-size: 12px;
    }}
    QPushButton#mutedButton:hover {{
        background: {c['bg_subtle']};
        border-color: {c['border_strong']};
        color: {c['text']};
    }}
    QPushButton#mutedButton:disabled {{
        color: {c['border_strong']};
    }}
    QPushButton#linkButton {{
        background: transparent;
        color: {c['accent_text']};
        border: none;
        font-size: 12px;
        font-weight: 400;
        text-decoration: underline;
        padding: 0;
    }}
    QPushButton#linkButton:hover {{
        color: {c['accent_active']};
    }}

    QLabel#authError {{
        background: {c['error_bg']};
        color: {c['error']};
        border: 1px solid {c['error_border']};
        border-radius: 10px;
        padding: 9px 11px;
        font-size: 12px;
    }}

    /* หลอดโหลดข้างปุ่มที่รอนาน — ไม่มีกฎนี้จะได้หลอดสีน้ำเงิน default ของ Windows ที่ตีกับ
       โทนครีม/clay ทั้งแอป (ดู gui/busy.py สำหรับตัวสร้าง) */
    QProgressBar#busyBar {{
        border: none;
        background: {c['accent_bg']};
        border-radius: 3px;
    }}
    QProgressBar#busyBar::chunk {{
        background: {c['accent']};
        border-radius: 3px;
    }}

    QLabel#badgeGreen {{
        background: {c['success_bg']};
        color: {c['success']};
        border-radius: 999px;
        padding: 3px 10px;
        font-size: 11px;
        font-weight: 600;
    }}
    QLabel#badgeOrange {{
        background: {c['warn_bg']};
        color: {c['warn']};
        border-radius: 999px;
        padding: 3px 10px;
        font-size: 11px;
        font-weight: 600;
    }}
    QLabel#badgeRed {{
        background: {c['error_bg']};
        color: {c['error']};
        border-radius: 999px;
        padding: 3px 10px;
        font-size: 11px;
        font-weight: 600;
    }}

    QTableWidget {{
        background: {c['card']};
        border: 1px solid {c['border']};
        border-radius: 12px;
        gridline-color: {c['grid']};
        selection-background-color: {c['accent_bg']};
        selection-color: {c['text']};
        outline: none;
    }}
    QTableWidget::item:selected {{
        background: {c['accent_bg']};
        color: {c['text']};
    }}
    QHeaderView {{
        background: {c['bg_subtle']};
    }}
    QHeaderView::section {{
        background: {c['bg_subtle']};
        color: {c['text_muted']};
        border: none;
        border-bottom: 1px solid {c['border']};
        padding: 10px 14px;
        font-size: 11px;
        font-weight: 600;
    }}
    /* section แรก/สุดท้ายต้องมนตามการ์ด ไม่งั้นแถบหัวตารางจะเป็นเหลี่ยมโผล่ทับมุมมนของตาราง
       (ทุกตารางในแอป stretch คอลัมน์ใดคอลัมน์หนึ่งอยู่แล้ว หัวตารางจึงเต็มความกว้างเสมอ) */
    QHeaderView::section:first {{
        border-top-left-radius: 11px;
    }}
    QHeaderView::section:last {{
        border-top-right-radius: 11px;
    }}
    QTableCornerButton::section {{
        background: {c['bg_subtle']};
        border: none;
        border-bottom: 1px solid {c['border']};
    }}

    /* ตารางเกือบทุกคอลัมน์เป็น setCellWidget ไม่ใช่ QTableWidgetItem — widget พวกนั้นกิน
       background ของกฎ QWidget ด้านบน (สีครีม) มาแปะทับพื้นขาวของตารางเป็นหย่อมๆ ตอนพื้นหลัง
       เป็น #F5F6F8 แทบมองไม่ออก แต่ครีมของ Claude เข้มพอที่จะเห็นชัด จึงล้างให้โปร่งทั้งตาราง
       แล้วค่อยคืนพื้นทึบให้เฉพาะตัวที่ต้องมีจริง — สองบล็อกนี้ specificity เท่ากัน (type 2 ตัว)
       บล็อกล่างจึงต้องอยู่หลังเสมอถึงจะชนะ */
    QTableWidget QWidget {{
        background: transparent;
    }}
    QTableWidget QLineEdit, QTableWidget QDateEdit, QTableWidget QDoubleSpinBox,
    QTableWidget QSpinBox, QTableWidget QComboBox, QTableWidget QPlainTextEdit,
    QTableWidget QTextEdit, QTableWidget QAbstractItemView {{
        background: {c['card']};
    }}
    QTableWidget QHeaderView {{
        background: {c['bg_subtle']};
    }}
    /* WpSearchDialog เปิดจาก picker ที่อยู่ในเซลล์ตาราง มันจึงเป็น "ลูกของ QTableWidget" ในสายตา
       QSS (Qt ไล่ descendant ตามสาย parent ไม่สนว่าเป็นคนละหน้าต่าง) แล้วโดนกฎโปร่งด้านบนจับไป
       ด้วย ผลคือ dialog ไม่มีพื้นเลย = ดำสนิททั้งกรอบ
       กฎ "QMainWindow, QDialog" ตอนต้นไฟล์ช่วยไม่ได้เพราะ type เดียวสู้ descendant สองตัวไม่ได้ —
       ต้องเป็น descendant สองตัวเท่ากันแล้ววางทีหลังถึงจะชนะ (QMessageBox เป็นลูกของ QDialog
       จึงถูกครอบไปด้วย) */
    QTableWidget QDialog {{
        background: {c['bg']};
    }}
    """


def apply_theme(app: QApplication) -> None:
    if FONT_FAMILY not in QFontDatabase.families():
        pass  # Segoe UI ships with Windows; no bundled fallback needed for now
    app.setStyleSheet(build_qss())


# ปฏิทินป๊อปอัปของ QDateEdit ถูกย่อให้พอดี sizeHint ของปฏิทิน "ตั้งแต่ตอนเปิด" ซึ่งตอนนั้นแถบหัว
# ยังแสดงปีเป็น QToolButton อยู่ พอผู้ใช้กดที่ปี Qt สลับปุ่มนั้นเป็นช่องกรอก (qt_calendar_yearedit)
# ลงในแถบที่ถูกล็อกความกว้างไปแล้ว ช่องจึงเหลือราว 49px ซึ่งไม่พอกับเลข 4 หลัก + ปุ่มลูกศร
# ผู้ใช้เลยเห็นปีถูกตัดเหลือ 2 ตัวและอ่านไม่ออกว่าตอนนี้เลือกปีอะไรอยู่
#
# แก้ด้วย QSS ไม่ได้ — ลอง min-width ถึง 160px แล้วขนาดบนจอไม่ขยับเลย เพราะ popup ถูกกำหนด
# ขนาดไปก่อนที่ช่องกรอกจะโผล่ ต้องกันความกว้างขั้นต่ำที่ตัวปฏิทินเองตั้งแต่สร้าง QDateEdit
CALENDAR_MIN_WIDTH = 300
CALENDAR_YEAR_MIN_WIDTH = 88


def tune_calendar_popup(date_edit: QDateEdit) -> None:
    """ให้ปฏิทินป๊อปอัปกว้างพอที่ช่องแก้ไขปีจะแสดงเลข 4 หลักได้ครบ

    เรียกต่อจาก setCalendarPopup(True) ทุกที่ที่สร้าง QDateEdit
    """
    calendar = date_edit.calendarWidget()
    if calendar is None:
        return
    calendar.setMinimumWidth(CALENDAR_MIN_WIDTH)
    # ชื่อนี้เป็นของภายใน Qt — วันหน้าถ้าเปลี่ยนชื่อก็แค่กลับไปแคบเหมือนเดิม ไม่ทำให้แอปพัง
    year_edit = calendar.findChild(QSpinBox, "qt_calendar_yearedit")
    if year_edit is not None:
        year_edit.setMinimumWidth(CALENDAR_YEAR_MIN_WIDTH)


# QComboBox ตั้งความกว้างของดรอปดาวน์ตามความกว้างของตัวมันเอง ข้อความที่ยาวกว่านั้นจึงถูกย่อ
# "กลางคำ" เช่นสถานะจาก OpenProject "PENDING FEEDBACK" -> "PE...CK", "IN PROGRESS" -> "IN ...ESS"
# ซึ่งอ่านไม่ออกว่าเป็นสถานะอะไร และเลือกผิดได้ง่ายเพราะหลายตัวขึ้นต้นเหมือนกัน (RE.../IN...)
# ขยายเฉพาะดรอปดาวน์ ไม่ขยายตัวคอมโบ เพื่อไม่ให้แถบเครื่องมือด้านบนถูกดันจนเบียด
COMBO_POPUP_EXTRA = 46  # padding ของ item + เผื่อแถบเลื่อนแนวตั้งตอนรายการยาว


def fit_combo_popup(combo: QComboBox) -> None:
    """ให้คอมโบและดรอปดาวน์กว้างพอแสดงตัวเลือกที่ยาวที่สุด — เรียก "หลัง" เติมรายการแล้วเท่านั้น

    ต้องแก้สองส่วน เพราะอาการมาจากคนละสาเหตุ:

    1. ตัวคอมโบเอง — ค่า default ของ Qt คือ AdjustToContentsOnFirstShow คือวัดขนาด "ครั้งแรก
       ที่แสดง" ครั้งเดียว รายการสถานะ/โปรเจกต์ในแอปนี้โหลดแบบ async มาถึงทีหลังตอนคอมโบ
       ถูกแสดงไปแล้ว ความกว้างจึงค้างอยู่ที่ขนาดตอนยังว่าง (วัดได้ 86px) แล้วไม่ขยายอีกเลย
       "IN PROGRESS" (ต้องใช้ 80px + ลูกศร) จึงถูกตัดเป็น "IN PRO..."
    2. ดรอปดาวน์ — Qt ตั้งความกว้าง popup ตามความกว้างของคอมโบ ข้อความที่ยาวกว่านั้นถูกย่อ
       "กลางคำ" เช่น "PENDING FEEDBACK" -> "PE...CK" ซึ่งอ่านไม่ออกและเลือกผิดง่าย เพราะสถานะ
       หลายตัวขึ้นต้นเหมือนกัน (RE.../IN...)

    ในเซลล์ตารางที่ความกว้างถูกล็อกไว้แล้ว ข้อ 1 จะไม่มีผล เหลือแต่ข้อ 2 ซึ่งเป็นตัวที่จำเป็นจริง
    """
    count = combo.count()
    if not count:
        return

    combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
    combo.updateGeometry()

    view = combo.view()
    fm = view.fontMetrics()
    widest = max(fm.horizontalAdvance(combo.itemText(i)) for i in range(count))
    if widest:
        view.setMinimumWidth(widest + COMBO_POPUP_EXTRA)

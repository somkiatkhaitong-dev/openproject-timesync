"""RowState mirrors static/app.js's client-side row object (mapServerRowToClientRow / isResolved)
so the editable-cell and WP-picker stages (3-4) can port that state machine 1:1."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_cls
from typing import Optional


@dataclass
class RowState:
    # None = แถวที่ผู้ใช้กด "+ เพิ่มแถว" ในแอป ยังไม่มีที่อยู่ในไฟล์ Excel
    # (จะถูก append ลงท้าย sheet ตอน submit — ดู gui/submit.py)
    excel_row: Optional[int]
    date: Optional[str]
    hours: Optional[float]
    activity: str  # ชื่อกิจกรรมดิบจากคอลัมน์ activity ใน Excel (ไม่ถูกแก้)
    comment: str
    user_email: str

    # id ของ activity ใน OpenProject ที่จะใช้ส่งจริง — เลือกจาก dropdown ในตาราง
    # ตั้งค่าเริ่มต้นจาก activity_mapping/ชื่อที่ตรงกัน (ดู MainWindow._apply_default_activities)
    activity_id: Optional[int] = None

    mode: str = "existing"  # "existing" | "new"

    wp_id: Optional[int] = None
    wp_subject: Optional[str] = None
    wp_project: Optional[str] = None
    wp_type: Optional[str] = None
    wp_status: Optional[str] = None
    auto_matched: bool = False

    # ติ๊กว่าจะส่งแถวนี้ในรอบ submit ถัดไปหรือไม่ — ค่าเริ่มต้น True เพราะพฤติกรรมเดิมคือส่งทุกแถว
    # ที่พร้อมส่งพร้อมกัน (ดู TableController._select_widget / MainWindow._on_submit_clicked)
    selected: bool = True

    new_project_id: Optional[int] = None
    new_project_name: Optional[str] = None
    new_project_autofilled: bool = False
    new_subject: str = ""
    new_type_id: Optional[int] = None

    def is_resolved(self) -> bool:
        # กิจกรรมต้องถูกเลือกก่อนเสมอ — เดิมความผิดพลาดตรงนี้ไปโผล่ตอน submit แล้วล้มทั้งแถว
        if not self.activity_id:
            return False
        if self.mode == "new":
            # ต้องมีประเภทงานด้วย ไม่งั้น submit_rows จะปัดตกทั้งแถวทั้งที่ badge ขึ้นว่า "พร้อมส่ง"
            return bool(
                self.new_project_id
                and self.new_subject
                and self.new_subject.strip()
                and self.new_type_id
            )
        return bool(self.wp_id)


def new_manual_row() -> RowState:
    """แถวเปล่าที่ผู้ใช้เพิ่มเองในแอป — ยังไม่มีอยู่ในไฟล์ Excel จนกว่าจะกด submit สำเร็จ/ล้มเหลว"""
    return RowState(
        excel_row=None,
        date=date_cls.today().isoformat(),
        hours=0.0,
        activity="",
        comment="",
        user_email="",
        mode="existing",
    )


def build_row_state(server_row: dict) -> RowState:
    """server_row: the dict shape row_to_dict() + {"match": ...} produces (old /api/state route)."""
    match = server_row.get("match")
    mode = server_row.get("wp_mode") or "existing"
    return RowState(
        excel_row=server_row["excel_row"],
        date=server_row.get("date"),
        hours=server_row.get("hours"),
        activity=server_row.get("activity") or "",
        comment=server_row.get("comment") or "",
        user_email=server_row.get("user_email") or "",
        mode=mode,
        new_subject=server_row.get("new_subject") or "",
        new_project_id=server_row.get("new_project_id"),
        new_project_name=server_row.get("new_project_name"),
        wp_id=match["id"] if match else None,
        wp_subject=match["subject"] if match else None,
        wp_project=match["project"] if match else None,
        wp_type=match["type"] if match else None,
        wp_status=match["status"] if match else None,
        auto_matched=bool(match),
    )

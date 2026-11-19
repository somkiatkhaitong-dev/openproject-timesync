from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from common import OpenProjectClient


@dataclass
class AppState:
    base_dir: str
    config_path: str
    config: dict
    # file_path = ไฟล์ที่โหลดเข้าตารางสำเร็จแล้วเท่านั้น (ใช้เป็น guard ตอน submit)
    file_path: Optional[str] = None
    # cli_file = ค่า --file ตอนรัน ใช้เป็นตำแหน่งตั้งต้นของ file dialog เฉยๆ ไม่ได้โหลดอัตโนมัติ
    cli_file: Optional[str] = None
    client: Optional[OpenProjectClient] = None

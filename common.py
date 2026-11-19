"""Core helpers shared by app.py and sync_timesheet.py.

Wraps the OpenProject REST API v3 and the Excel timesheet file format
described in config.yaml.
"""
from __future__ import annotations

import math
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date as date_cls, datetime, timedelta
from pathlib import Path
from typing import Any

import certifi
import pandas as pd
import requests
import yaml
from openpyxl import load_workbook
from requests.adapters import HTTPAdapter


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_COLUMNS = {
    "date": "date",
    "work_package_id": "wp_id",
    "hours": "hours",
    "activity": "activity",
    "comment": "comment",
    "user_email": "user_email",
    "synced": "synced",
    "sync_note": "sync_note",
    # ระบุว่าจะใช้ Work Package เดิมหรือสร้างใหม่ + ข้อมูลของงานที่จะสร้าง
    # ไฟล์เก่าที่ไม่มี 3 คอลัมน์นี้ยังใช้ได้ปกติ (read_timesheet เติมให้เป็นค่าว่าง แล้วเดาโหมดจาก wp_id)
    "wp_mode": "wp_mode",
    "new_subject": "new_subject",
    "new_project": "new_project",
}

# ค่าที่ยอมรับในคอลัมน์ wp_mode — เทียบแบบตัดช่องว่างและไม่สนตัวพิมพ์
_MODE_NEW = {"new", "ใหม่", "สร้างใหม่", "สร้างงานใหม่", "create", "n"}
_MODE_EXISTING = {"existing", "เดิม", "งานเดิม", "old", "e"}


# ค่าตั้งต้นของแท็บ "สรุปงาน" — ดู gui/worklog_tab.py
DEFAULT_WORKLOG = {
    "output_dir": "",       # ว่าง = โฟลเดอร์ของ last_file แล้วค่อย fallback เป็น ~/Documents
    "hours_per_day": 8,
    "default_activity": "Normal",
    "authors": [],          # ว่าง = git config user.email ของแต่ละ repo
    "claude_path": "",      # ว่าง = หา claude จาก PATH
    "claude_model": "claude-sonnet-5",  # ว่าง = ใช้ค่านี้ · "cli" = ปล่อยให้ CLI เลือกเอง
    "claude_effort": "low",  # ความลึกในการคิด — low พอสำหรับงานแตก task และเร็วกว่ามาก
    "claude_fast": True,    # False = ไม่ใส่ชุดธงประหยัดโทเคน (สำหรับ claude เวอร์ชันเก่า)
    "repos": [],            # [{"path": ..., "project_id": ..., "project_name": ..., "enabled": bool}]
}


class ConfigError(Exception):
    pass


def load_config_raw(path: str) -> dict:
    """อ่าน config.yaml พร้อม default ให้ครบ แต่ไม่บังคับว่าต้องมี openproject.url/api_key
    (ใช้ตอน app.py ต้องรองรับกรณียังไม่ได้ login — ใส่ url/api_key ทีหลังผ่านหน้า login ได้)"""
    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    op = config.setdefault("openproject", {}) or {}
    op.setdefault("url", "")
    op.setdefault("api_key", "")
    config["openproject"] = op

    config.setdefault("activity_mapping", {})
    config.setdefault("user_mapping", {})
    config.setdefault("last_file", "")

    worklog = config.setdefault("worklog", {}) or {}
    for key, default in DEFAULT_WORKLOG.items():
        worklog.setdefault(key, _copy_default(default))
    config["worklog"] = worklog

    excel_cfg = config.setdefault("excel", {})
    excel_cfg.setdefault("sheet_name", "Sheet1")
    columns = excel_cfg.setdefault("columns", {})
    for key, default in DEFAULT_COLUMNS.items():
        columns.setdefault(key, default)

    return config


def default_config() -> dict:
    """config เปล่าตอนไม่มีไฟล์ config.yaml เลย — ใช้ default ทั้งหมด รอกรอก url/api_key ผ่านหน้า login"""
    return {
        "openproject": {"url": "", "api_key": ""},
        "activity_mapping": {},
        "user_mapping": {},
        "last_file": "",
        "worklog": {k: _copy_default(v) for k, v in DEFAULT_WORKLOG.items()},
        "excel": {"sheet_name": "Sheet1", "columns": dict(DEFAULT_COLUMNS)},
    }


def _copy_default(value):
    """list/dict ใน DEFAULT_* ต้องก๊อปทุกครั้ง ไม่งั้นทุก config จะแชร์ object เดียวกัน"""
    if isinstance(value, list):
        return list(value)
    if isinstance(value, dict):
        return dict(value)
    return value


def save_worklog_config(path: str, worklog: dict) -> None:
    """บันทึกหมวด worklog กลับ config.yaml (คง key อื่นไว้) — ใช้ตอนแท็บ Git เพิ่ม/แก้ repo

    เขียนทับเฉพาะหมวดนี้ตามแบบเดียวกับ save_credentials/save_last_file เพราะแท็บอื่นอาจแก้
    ค่าอื่นในไฟล์เดียวกันระหว่างที่แอปเปิดอยู่
    """
    data = {}
    if Path(path).exists():
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    data["worklog"] = worklog
    data.setdefault("openproject", {})
    data.setdefault("activity_mapping", {})
    data.setdefault("user_mapping", {})
    data.setdefault("excel", {})
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def save_credentials(path: str, url: str, api_key: str) -> None:
    """บันทึก url/api_key กลับเข้า config.yaml (ทับเฉพาะส่วน openproject, คงค่าอื่นไว้) — ใช้ตอนติ๊ก 'จำไว้' ที่หน้า login"""
    data = {}
    if Path(path).exists():
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    data.setdefault("openproject", {})
    data["openproject"]["url"] = url
    data["openproject"]["api_key"] = api_key
    data.setdefault("activity_mapping", {})
    data.setdefault("user_mapping", {})
    data.setdefault("excel", {})
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def save_last_file(path: str, file_path: str) -> None:
    """บันทึก path ไฟล์ Excel ล่าสุดที่ใช้กลับเข้า config.yaml — เปิดแอปครั้งถัดไปหลัง login จะโหลดไฟล์นี้
    ให้เลยถ้ายังเปิดได้อยู่ ไม่ต้องผ่านหน้าอัปโหลดซ้ำ"""
    data = {}
    if Path(path).exists():
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    data["last_file"] = file_path
    data.setdefault("openproject", {})
    data.setdefault("activity_mapping", {})
    data.setdefault("user_mapping", {})
    data.setdefault("excel", {})
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def load_config(path: str) -> dict:
    """เหมือน load_config_raw แต่บังคับว่าต้องมี url/api_key ครบ — ใช้กับ sync_timesheet.py (CLI)
    ที่รันแบบไม่มีคนตรวจ/ไม่มีหน้า login ให้กรอกทีหลัง"""
    config = load_config_raw(path)

    op = config["openproject"]
    if not op.get("url"):
        raise ConfigError(f"config '{path}' ขาด openproject.url")
    if not op.get("api_key"):
        raise ConfigError(f"config '{path}' ขาด openproject.api_key")

    return config


# ---------------------------------------------------------------------------
# OpenProject API client
# ---------------------------------------------------------------------------

# ช่วงที่ยอมไล่ยิงหา activity ทีละ id ตอน instance ไม่มี endpoint แบบ list
ACTIVITY_PROBE_MAX_ID = 40
ACTIVITY_PROBE_MISS_LIMIT = 15
# จำนวน request ที่ find_duplicate_subjects ยิงพร้อมกัน — ต่ำกว่า pool_maxsize ของ session
# (16) เพื่อไม่ให้เบียด connection ของ picker ที่อาจยิงอยู่พร้อมกัน
DUP_CHECK_WORKERS = 6


def _id_from_href(href: str | None) -> int | None:
    if not href:
        return None
    try:
        return int(href.rstrip("/").rsplit("/", 1)[-1])
    except ValueError:
        return None


class OpenProjectAPIError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code
        # ข้อความดิบไว้ลง log — ที่ผู้ใช้เห็นคือ message ที่แปลแล้ว ซึ่งตัดรายละเอียดทิ้งไปมาก
        self.detail: str = message


def describe_request_error(exc: Exception) -> str:
    """แปล exception ของ requests เป็นภาษาคน พร้อมบอกว่าควรไปดูตรงไหนต่อ

    เขียนขึ้นหลังเคส 2026-08-07 ที่ผู้ใช้เห็นแค่ 'SSLError(SSLCertVerificationError(1, [SSL:'
    ในกล่องแดงแล้วไปต่อไม่ถูก ต้อง remote เข้าไปไล่ทีละคำสั่งกว่าจะรู้ว่าเป็นเรื่อง CA bundle
    """
    if isinstance(exc, requests.exceptions.SSLError):
        return (
            "ตรวจสอบใบรับรอง (SSL) ของเซิร์ฟเวอร์ไม่ผ่าน\n"
            "มักเกิดจากเครื่องนี้ตั้งตัวแปร REQUESTS_CA_BUNDLE หรือ SSL_CERT_FILE ไว้ "
            "(เช่นตอนใช้ mitmproxy/Fiddler) หรือมี proxy/แอนตี้ไวรัสดักการเชื่อมต่ออยู่\n"
            "กดปุ่ม 'ตรวจสอบระบบ' เพื่อดูว่าเป็นกรณีไหน"
        )
    if isinstance(exc, requests.exceptions.ProxyError):
        return (
            "เชื่อมต่อผ่าน proxy ไม่สำเร็จ\n"
            "เครื่องนี้ตั้ง HTTP_PROXY / HTTPS_PROXY ไว้ แต่ proxy ตัวนั้นตอบไม่ได้\n"
            "กดปุ่ม 'ตรวจสอบระบบ' เพื่อดูค่าที่ตั้งไว้"
        )
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return "เชื่อมต่อไม่ทันเวลา — เซิร์ฟเวอร์ไม่ตอบ หรือถูก firewall บล็อก"
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return "เซิร์ฟเวอร์รับการเชื่อมต่อแล้วแต่ตอบกลับช้าเกินไป ลองใหม่อีกครั้ง"
    if isinstance(exc, requests.exceptions.MissingSchema):
        return "URL ไม่ครบ — ต้องขึ้นต้นด้วย https:// เช่น https://openproject.example.com"
    if isinstance(exc, requests.exceptions.InvalidURL):
        return "รูปแบบ URL ไม่ถูกต้อง ตรวจว่าพิมพ์ถูกไหม"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return (
            "ติดต่อเซิร์ฟเวอร์ไม่ได้\n"
            "ตรวจว่า URL ถูกต้อง และเครื่องนี้เข้าถึงเซิร์ฟเวอร์ได้ (VPN/เน็ตออฟฟิศ)\n"
            "กดปุ่ม 'ตรวจสอบระบบ' เพื่อดูว่าติดที่ DNS หรือ TCP"
        )
    return str(exc)


def describe_status_error(status_code: int | None, fallback: str) -> str:
    """ข้อความสำหรับกรณีต่อเซิร์ฟเวอร์ติดแล้วแต่โดนปฏิเสธ — fallback คือข้อความจากตัว API เอง"""
    if status_code == 401:
        return "API key ไม่ถูกต้องหรือหมดอายุ — สร้างใหม่ได้ที่ OpenProject: My Account > Access tokens"
    if status_code == 403:
        return f"API key ใช้ได้ แต่ไม่มีสิทธิ์เข้าถึงส่วนนี้\n{fallback}"
    if status_code == 429:
        return "เซิร์ฟเวอร์จำกัดจำนวนคำขอชั่วคราว (429) รอสักครู่แล้วลองใหม่"
    if status_code is not None and status_code >= 500:
        return f"เซิร์ฟเวอร์ OpenProject มีปัญหา (HTTP {status_code})\n{fallback}"
    return fallback


class OpenProjectClient:
    def __init__(self, url: str, api_key: str, timeout: int = 20):
        self.base_url = url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.auth = ("apikey", api_key)
        self.session.headers.update({"Content-Type": "application/json"})
        # ค่า default ของ requests คือ pool_maxsize=10 ซึ่งน้อยกว่าจำนวน worker ที่ยิงพร้อมกันจริง
        # (picker ทุกแถว + ThreadPoolExecutor ของ find_duplicate_subjects) connection ส่วนเกิน
        # จะถูกทิ้งแล้วเปิด TCP+TLS ใหม่ทุกครั้ง — ขยาย pool ให้พอกับที่ใช้จริง
        adapter = HTTPAdapter(pool_connections=4, pool_maxsize=16)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        # รายการ activity ไม่เปลี่ยนระหว่าง session และการหามันแพง (instance ที่ไม่มี endpoint
        # แบบ list ต้องไล่ยิงทีละ id เรียงกันเกือบ 20 ครั้ง) — จำไว้ทั้งก้อน ดู get_activities
        self._activities_cache: list[dict] | None = None

    def _request(self, method: str, path: str, quiet: bool = False, **kwargs) -> dict | None:
        """quiet=True สำหรับคำขอที่ "ไม่ผ่าน" เป็นคำตอบที่ถูกต้องอย่างหนึ่ง ไม่ใช่ความผิดพลาด

        ตัวเดียวที่ใช้ตอนนี้คือการหา activity ซึ่งต้องคลำว่า instance นี้มี endpoint แบบไหน —
        ยังโยน OpenProjectAPIError เหมือนเดิมทุกอย่าง ต่างแค่ไม่พ่น [ERROR] ลง log เพราะผู้เรียก
        จัดการต่อเองได้อยู่แล้ว ปัญหาระดับ transport (timeout/DNS/TLS) ยังขึ้น log เสมอ —
        พวกนั้นควรรู้ ต่อให้ผู้เรียกจะกลืนมันไปก็ตาม"""
        url = f"{self.base_url}{path}"
        # requests อ่าน REQUESTS_CA_BUNDLE/CURL_CA_BUNDLE จาก environment เฉพาะตอน verify เป็น
        # None/True แล้ว "แทนที่" certifi ทั้งก้อนด้วยไฟล์นั้น ไม่ใช่เพิ่มเข้าไป — เครื่องที่ตั้ง
        # ตัวแปรพวกนี้ไว้ใช้กับ mitmproxy จึง verify cert ของ CA สาธารณะไม่ผ่าน ระบุ bundle
        # ที่บันเดิลมากับ .exe ไปตรง ๆ เพื่อไม่ให้ environment ของเครื่องปลายทางมามีผล
        kwargs.setdefault("verify", certifi.where())
        try:
            resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
        except requests.RequestException as e:
            # ตัวเต็มลง log ผ่าน stderr ที่ gui/logging_setup ดักไว้, ตัวที่แปลแล้วขึ้นหน้าจอ
            print(f"[ERROR] {method} {url} ล้มเหลว: {type(e).__name__}: {e}", file=sys.stderr)
            error = OpenProjectAPIError(describe_request_error(e))
            error.detail = f"{type(e).__name__}: {e}"
            raise error from e

        if resp.status_code == 404:
            return None
        if not resp.ok:
            raw = _extract_error_message(resp)
            if not quiet:
                print(f"[ERROR] {method} {url} -> HTTP {resp.status_code}: {raw}", file=sys.stderr)
            error = OpenProjectAPIError(
                describe_status_error(resp.status_code, raw), resp.status_code
            )
            error.detail = f"HTTP {resp.status_code}: {raw}"
            raise error
        if not resp.content:
            return {}
        return resp.json()

    # -- work packages ----------------------------------------------------

    def get_work_package(self, wp_id: int) -> dict | None:
        data = self._request("GET", f"/api/v3/work_packages/{wp_id}")
        return _transform_wp(data) if data else None

    @staticmethod
    def _subject_search_params(query: str, project_id: int | None, page_size: int) -> dict:
        filters: list[dict] = [{"subject": {"operator": "~", "values": [query.strip()]}}]
        if project_id:
            filters.append({"project": {"operator": "=", "values": [str(project_id)]}})
        return {
            "filters": _json_dumps(filters),
            "pageSize": page_size,
            "sortBy": _json_dumps([["updatedAt", "desc"]]),
        }

    def search_work_packages(self, query: str, project_id: int | None = None, page_size: int = 15) -> list[dict]:
        if not query or not query.strip():
            return []
        params = self._subject_search_params(query, project_id, page_size)
        data = self._request("GET", "/api/v3/work_packages", params=params)
        elements = (data or {}).get("_embedded", {}).get("elements", [])
        return [_transform_wp(wp) for wp in elements]

    def search_work_packages_page(
        self, query: str, project_id: int | None = None, page_size: int = 30, offset: int = 1
    ) -> dict:
        """เหมือน search_work_packages แต่คืน {"results": [...], "total": int} เพื่อทำปุ่ม "โหลดเพิ่ม"

        offset ของ API v3 เป็น "เลขหน้า" เริ่มที่ 1 ไม่ใช่จำนวนรายการที่ข้าม — หน้า 2 คือ offset=2
        (แยกเมธอดแทนการเปลี่ยน return type ของ search_work_packages เพราะ find_duplicate_subjects
        ยังเรียกตัวเดิมอยู่)
        """
        if not query or not query.strip():
            return {"results": [], "total": 0}
        params = self._subject_search_params(query, project_id, page_size)
        params["offset"] = max(1, offset)
        data = self._request("GET", "/api/v3/work_packages", params=params) or {}
        elements = data.get("_embedded", {}).get("elements", [])
        return {"results": [_transform_wp(wp) for wp in elements], "total": data.get("total", 0)}

    def get_work_packages_by_ids(self, ids: list[int]) -> dict[int, dict]:
        """ดึงหลาย work package ด้วย id ในคำขอเดียว (แบ่งเป็นชุดถ้าเกิน 500 id) คืน dict คีย์เป็น id

        แทนการเรียก get_work_package ทีละตัว — id ที่หาไม่เจอ/ไม่มีสิทธิ์เข้าถึงจะไม่ปรากฏใน
        ผลลัพธ์ (เหมือนเดิมที่ get_work_package คืน None ให้แถวนั้น) ไม่ทำให้ทั้งชุด error
        """
        if not ids:
            return {}
        result: dict[int, dict] = {}
        unique_ids = sorted(set(ids))
        for i in range(0, len(unique_ids), 500):
            chunk = unique_ids[i : i + 500]
            filters = [{"id": {"operator": "=", "values": [str(x) for x in chunk]}}]
            params = {"filters": _json_dumps(filters), "pageSize": len(chunk)}
            data = self._request("GET", "/api/v3/work_packages", params=params) or {}
            for wp in data.get("_embedded", {}).get("elements", []):
                transformed = _transform_wp(wp)
                result[transformed["id"]] = transformed
        return result

    def create_work_package(self, project_id: int, subject: str, type_id: int) -> int:
        payload = {
            "subject": subject,
            "_links": {"type": {"href": f"/api/v3/types/{type_id}"}},
        }
        data = self._request("POST", f"/api/v3/projects/{project_id}/work_packages", json=payload)
        if not data or "id" not in data:
            raise OpenProjectAPIError("สร้าง Work Package ไม่สำเร็จ: ไม่ได้รับ id กลับมา")
        return data["id"]

    def update_work_package_status(self, wp_id: int, status_id: int) -> dict:
        """เปลี่ยนสถานะของ Work Package หนึ่งตัว คืนข้อมูล WP หลังแก้ (รูปแบบเดียวกับ get_work_package)

        ต้อง GET ก่อนเสมอเพื่อเอา lockVersion — OpenProject บังคับส่งมาด้วยทุกครั้งที่ PATCH
        (กันสองคนแก้ทับกัน) ถ้าไม่ส่งหรือส่งค่าเก่าจะตอบ 409 กลับมา
        """
        current = self._request("GET", f"/api/v3/work_packages/{wp_id}")
        if not current:
            raise OpenProjectAPIError(f"ไม่พบ Work Package #{wp_id}", 404)
        payload = {
            "lockVersion": current.get("lockVersion"),
            "_links": {"status": {"href": f"/api/v3/statuses/{status_id}"}},
        }
        data = self._request("PATCH", f"/api/v3/work_packages/{wp_id}", json=payload)
        if not data:
            raise OpenProjectAPIError(f"ปรับสถานะ Work Package #{wp_id} ไม่สำเร็จ")
        return _transform_wp(data)

    # -- projects / types / activities / statuses ---------------------------

    def get_statuses(self) -> list[dict]:
        """คืน [{"id", "name"}] ของสถานะทั้งหมดใน instance (ชื่อสถานะต่างกันได้ในแต่ละ instance)"""
        data = self._request("GET", "/api/v3/statuses")
        elements = (data or {}).get("_embedded", {}).get("elements", [])
        return [{"id": s["id"], "name": s["name"]} for s in elements]

    def get_projects(self) -> list[dict]:
        data = self._request("GET", "/api/v3/projects", params={"pageSize": 500})
        elements = (data or {}).get("_embedded", {}).get("elements", [])
        return [{"id": p["id"], "name": p["name"]} for p in elements]

    def get_types(self, project_id: int | None = None) -> list[dict]:
        path = f"/api/v3/projects/{project_id}/types" if project_id else "/api/v3/types"
        data = self._request("GET", path)
        elements = (data or {}).get("_embedded", {}).get("elements", [])
        return [{"id": t["id"], "name": t["name"]} for t in elements]

    def get_activities(self) -> list[dict]:
        """คืน [{"id", "name"}] ของ time entry activity ทั้งหมด เรียงตาม id

        จำผลไว้ต่อ client หนึ่งตัว — รายการนี้ไม่เปลี่ยนระหว่าง session และทางที่ต้อง discover
        เอาต้องยิงเรียงกันเกือบ 20 ครั้ง ไม่ควรจ่ายซ้ำ
        """
        if self._activities_cache is None:
            self._activities_cache = self._load_activities()
        return self._activities_cache

    def _load_activities(self) -> list[dict]:
        """API v3 ไม่มี endpoint แบบ list สำหรับ activity บนทุก instance — ที่ไม่มี route นี้จะ
        ตอบ 400 "id is invalid" (route จับเป็นแบบราย id แล้วได้ id ว่าง) ไม่ใช่ 404

        400 ตรงนี้จึงเป็น "คำตอบ" ว่า instance นี้ต้อง discover เอา ไม่ใช่ความผิดพลาด — ส่ง
        quiet=True เพื่อไม่ให้ขึ้น [ERROR] ใน log ทุกรอบ login ทั้งที่ทางสำรองทำงานได้ปกติ
        """
        try:
            data = self._request("GET", "/api/v3/time_entries/activities", quiet=True)
            elements = (data or {}).get("_embedded", {}).get("elements", [])
            if elements:
                return [{"id": a["id"], "name": a["name"]} for a in elements]
        except OpenProjectAPIError:
            pass
        return self._discover_activities()

    def _discover_activities(self) -> list[dict]:
        found: dict[int, str] = {}

        # 1) activity ที่ถูกใช้จริงใน time entry ล่าสุด — ได้ชื่อ+id มาในคำขอเดียว
        try:
            data = self._request(
                "GET", "/api/v3/time_entries", params={"pageSize": 100}, quiet=True
            )
            for entry in (data or {}).get("_embedded", {}).get("elements", []):
                link = (entry.get("_links") or {}).get("activity") or {}
                activity_id = _id_from_href(link.get("href"))
                if activity_id is not None and link.get("title"):
                    found[activity_id] = link["title"]
        except OpenProjectAPIError:
            pass

        # 2) ไล่ยิงทีละ id เพื่อให้เจอ activity ที่ตั้งไว้แต่ยังไม่เคยมีใครใช้ด้วย
        #    (id ที่ไม่มีจริงตอบ 404 -> _request คืน None) หยุดเมื่อพลาดติดกันหลายครั้ง
        misses = 0
        for activity_id in range(1, ACTIVITY_PROBE_MAX_ID + 1):
            if activity_id in found:
                misses = 0
                continue
            try:
                data = self._request(
                    "GET", f"/api/v3/time_entries/activities/{activity_id}", quiet=True
                )
            except OpenProjectAPIError:
                data = None
            if data and data.get("name"):
                found[activity_id] = data["name"]
                misses = 0
            else:
                misses += 1
                if misses >= ACTIVITY_PROBE_MISS_LIMIT and found:
                    break

        return [{"id": i, "name": found[i]} for i in sorted(found)]

    # -- time entries -------------------------------------------------------

    def create_time_entry(
        self,
        work_package_id: int,
        spent_on: str,
        hours: float,
        activity_id: int,
        comment: str | None = None,
        user_id: int | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "spentOn": spent_on,
            "hours": hours_to_iso8601_duration(hours),
            "_links": {
                "workPackage": {"href": f"/api/v3/work_packages/{work_package_id}"},
                "activity": {"href": f"/api/v3/time_entries/activities/{activity_id}"},
            },
        }
        if comment:
            payload["comment"] = {"raw": comment}
        if user_id:
            payload["_links"]["user"] = {"href": f"/api/v3/users/{user_id}"}

        data = self._request("POST", "/api/v3/time_entries", json=payload)
        return data or {}

    def get_current_user(self) -> dict:
        data = self._request("GET", "/api/v3/users/me")
        data = data or {}
        return {"id": data.get("id"), "name": data.get("name"), "email": data.get("email")}

    def get_my_time_entries(self, user_id: int, from_date: str, to_date: str) -> list[dict]:
        filters: list[dict] = [
            {"user_id": {"operator": "=", "values": [str(user_id)]}},
            {"spent_on": {"operator": "<>d", "values": [from_date, to_date]}},
        ]
        elements: list[dict] = []
        offset = None
        for _ in range(20):  # กันลูปไม่รู้จบถ้า total ผิดปกติ (20 หน้า x 500 = 10,000 รายการ เกินพอ)
            params = {
                "filters": _json_dumps(filters),
                "pageSize": 500,
                "sortBy": _json_dumps([["spentOn", "asc"]]),
            }
            if offset is not None:
                params["offset"] = offset
            data = self._request("GET", "/api/v3/time_entries", params=params) or {}
            page_elements = data.get("_embedded", {}).get("elements", [])
            elements.extend(page_elements)

            total = data.get("total", len(elements))
            if len(elements) >= total or not page_elements:
                break
            # เดินหน้าต่อจากเลขหน้าที่ server ตอบมาจริง ไม่เดาว่า offset เริ่มที่ 0 หรือ 1 เอง
            offset = data.get("offset", 1) + 1

        return [_transform_time_entry(e) for e in elements]


def _transform_wp(wp: dict) -> dict:
    links = wp.get("_links", {})
    return {
        "id": wp.get("id"),
        "subject": wp.get("subject"),
        "project": (links.get("project") or {}).get("title"),
        "type": (links.get("type") or {}).get("title"),
        "status": (links.get("status") or {}).get("title"),
        "lock_version": wp.get("lockVersion"),
        "author_id": _id_from_href((links.get("author") or {}).get("href")),
        "author": (links.get("author") or {}).get("title"),
    }


def _transform_time_entry(entry: dict) -> dict:
    links = entry.get("_links", {})
    wp_link = links.get("workPackage") or {}
    return {
        "id": entry.get("id"),
        "date": entry.get("spentOn"),
        "hours": iso8601_duration_to_hours(entry.get("hours")),
        "work_package_id": _id_from_href(wp_link.get("href")),
        "work_package_subject": wp_link.get("title"),
        "project": (links.get("project") or {}).get("title"),
        "activity": (links.get("activity") or {}).get("title"),
        "comment": (entry.get("comment") or {}).get("raw") or "",
    }


def bulk_update_status(client: OpenProjectClient, wp_ids: list[int], status_id: int) -> dict:
    """ปรับสถานะ Work Package หลายตัวทีละตัว คืนสรุปผลรูปแบบเดียวกับ gui.submit.submit_rows()

    จับ error รายตัวเหมือน submit_rows เพราะ workflow ของ OpenProject กำหนดเป็นราย type/role —
    การข้ามสถานะที่ไม่อนุญาตจะตอบ 422 เฉพาะ WP นั้น ไม่ควรทำให้ทั้งชุดล้มตาม
    """
    results = []
    success = 0
    failed = 0

    for wp_id in wp_ids:
        try:
            client.update_work_package_status(wp_id, status_id)
        except (OpenProjectAPIError, ValueError) as e:
            results.append({"wp_id": wp_id, "ok": False, "message": str(e)})
            failed += 1
        else:
            results.append({"wp_id": wp_id, "ok": True, "message": "ปรับสถานะสำเร็จ"})
            success += 1

    return {"success": success, "failed": failed, "results": results}


def _extract_error_message(resp: requests.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return f"OpenProject API error ({resp.status_code}): {resp.text[:300]}"

    message = data.get("message")
    if message:
        return message

    errors = data.get("_embedded", {}).get("errors", [])
    if errors:
        return "; ".join(e.get("message", "") for e in errors if e.get("message"))

    return f"OpenProject API error ({resp.status_code})"


def _json_dumps(obj) -> str:
    import json

    return json.dumps(obj, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Hours <-> ISO 8601 duration
# ---------------------------------------------------------------------------

def hours_to_iso8601_duration(hours: float) -> str:
    total_minutes = round(float(hours) * 60)
    if total_minutes <= 0:
        return "PT0M"
    h, m = divmod(total_minutes, 60)
    out = "PT"
    if h:
        out += f"{h}H"
    if m or not h:
        out += f"{m}M"
    return out


def iso8601_duration_to_hours(duration: str | None) -> float:
    if not duration:
        return 0.0
    match = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?$", duration)
    if not match:
        return 0.0
    hours, minutes = match.groups()
    return int(hours or 0) + int(minutes or 0) / 60


# ---------------------------------------------------------------------------
# Activity / user mapping
# ---------------------------------------------------------------------------

def resolve_activity_id(activity_name: str, activity_mapping: dict) -> int:
    if activity_name in activity_mapping:
        return activity_mapping[activity_name]
    raise ValueError(f"ไม่พบกิจกรรม '{activity_name}' ใน config activity_mapping")


def resolve_user_id(user_email: str | None, user_mapping: dict) -> int | None:
    if not user_email:
        return None
    return user_mapping.get(user_email)


# ---------------------------------------------------------------------------
# Excel read/write
# ---------------------------------------------------------------------------

# config.yaml keys (config-facing) -> internal DataFrame column names used everywhere else
_INTERNAL_FIELD_NAMES = {"work_package_id": "wp_id"}


def read_timesheet(file_path: str, excel_cfg: dict) -> pd.DataFrame:
    sheet_name = excel_cfg.get("sheet_name", "Sheet1")
    columns = excel_cfg["columns"]

    df = pd.read_excel(file_path, sheet_name=sheet_name)

    rename_map = {
        excel_header: _INTERNAL_FIELD_NAMES.get(config_key, config_key)
        for config_key, excel_header in columns.items()
    }
    df = df.rename(columns=rename_map)

    for config_key in DEFAULT_COLUMNS:
        internal = _INTERNAL_FIELD_NAMES.get(config_key, config_key)
        if internal not in df.columns:
            df[internal] = None

    df["_excel_row"] = df.index + 2  # header is row 1, data starts row 2

    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df["hours"] = pd.to_numeric(df["hours"], errors="coerce")
    df["wp_id"] = pd.to_numeric(df["wp_id"], errors="coerce").astype("Int64")
    df["synced"] = df["synced"].apply(_coerce_bool)
    df["activity"] = df["activity"].fillna("").astype(str)
    df["comment"] = df["comment"].fillna("").astype(str)
    df["user_email"] = df["user_email"].fillna("").astype(str)
    df["sync_note"] = df["sync_note"].fillna("").astype(str)
    df["wp_mode"] = df["wp_mode"].fillna("").astype(str)
    df["new_subject"] = df["new_subject"].fillna("").astype(str)
    # new_project ใส่เป็นชื่อโปรเจกต์หรือ id ก็ได้ — ถ้าใส่เป็นตัวเลข pandas จะอ่านมาเป็น 666.0
    # ปล่อยไปตรงๆ จะได้ข้อความ "666.0" ที่จับคู่ไม่ติด จึงต้องตัด .0 ทิ้งก่อน
    df["new_project"] = df["new_project"].apply(_as_lookup_text)

    return df


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, float) and math.isnan(value):
        return False
    text = str(value).strip().lower()
    return text in ("true", "1", "yes", "y")


def _as_lookup_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        if value.is_integer():
            return str(int(value))
    return str(value).strip()


def resolve_wp_mode(raw, wp_id, new_subject: str) -> str:
    """คืน "new" | "existing"

    เว้นว่างหรือพิมพ์มาไม่ตรงคำไหนเลย = เดาจากข้อมูลที่มีในแถวนั้น: มีชื่องานใหม่และยังไม่มี wp_id
    ถือว่าตั้งใจสร้างงานใหม่ นอกนั้นถือว่าใช้งานเดิม (ไฟล์เก่าที่ไม่มีคอลัมน์นี้จึงได้ "existing" ทุกแถว)
    """
    text = _as_lookup_text(raw).lower()
    if text in _MODE_NEW:
        return "new"
    if text in _MODE_EXISTING:
        return "existing"
    return "new" if (new_subject or "").strip() and wp_id is None else "existing"


def get_unsynced_rows(df: pd.DataFrame) -> pd.DataFrame:
    return df[~df["synced"].astype(bool)].copy()


def row_to_dict(row: pd.Series) -> dict:
    wp_id = row["wp_id"]
    wp_id = int(wp_id) if pd.notna(wp_id) else None
    hours = row["hours"]
    hours = float(hours) if pd.notna(hours) else None
    new_subject = (row["new_subject"] or "").strip()
    return {
        "excel_row": int(row["_excel_row"]),
        "date": row["date"] if pd.notna(row["date"]) else None,
        "wp_id": wp_id,
        "hours": hours,
        "activity": row["activity"],
        "comment": row["comment"],
        "user_email": row["user_email"],
        "wp_mode": resolve_wp_mode(row["wp_mode"], wp_id, new_subject),
        "new_subject": new_subject,
        "new_project": _as_lookup_text(row["new_project"]),
    }


def update_timesheet_rows(
    file_path: str,
    excel_cfg: dict,
    updates: dict[int, dict],
    appends: list[dict] | None = None,
) -> None:
    """updates: excel_row_number -> {"synced": bool, "sync_note": str, "wp_id": int|None}

    appends: แถวใหม่ที่ผู้ใช้เพิ่มในแอป (ยังไม่มีในไฟล์) — ต่อท้าย sheet ทั้งแถวพร้อมทุกคอลัมน์
    ทำในรอบ load/save เดียวกับ updates เพื่อไม่ต้องเปิด-เซฟไฟล์สองรอบ
    """
    sheet_name = excel_cfg.get("sheet_name", "Sheet1")
    columns = excel_cfg["columns"]

    wb = load_workbook(file_path)
    sheet = wb[sheet_name]

    header_row = 1
    headers = {}
    for cell in sheet[header_row]:
        if cell.value is not None:
            headers[str(cell.value)] = cell.column

    synced_col = _get_or_create_column(sheet, headers, columns["synced"])
    sync_note_col = _get_or_create_column(sheet, headers, columns["sync_note"])
    wp_id_col = _get_or_create_column(sheet, headers, columns["work_package_id"])

    for row_num, update in updates.items():
        sheet.cell(row=row_num, column=synced_col, value=bool(update.get("synced")))
        sheet.cell(row=row_num, column=sync_note_col, value=update.get("sync_note", ""))
        if update.get("wp_id") is not None:
            sheet.cell(row=row_num, column=wp_id_col, value=update["wp_id"])

    if appends:
        cols = {
            key: _get_or_create_column(sheet, headers, columns[key]) for key in DEFAULT_COLUMNS
        }
        next_row = sheet.max_row + 1
        for i, new_row in enumerate(appends):
            row_num = next_row + i
            sheet.cell(row=row_num, column=cols["date"], value=_as_date_cell(new_row.get("date")))
            sheet.cell(row=row_num, column=cols["hours"], value=new_row.get("hours"))
            sheet.cell(row=row_num, column=cols["activity"], value=new_row.get("activity") or "")
            sheet.cell(row=row_num, column=cols["comment"], value=new_row.get("comment") or "")
            sheet.cell(row=row_num, column=cols["user_email"], value=new_row.get("user_email") or "")
            sheet.cell(row=row_num, column=cols["synced"], value=bool(new_row.get("synced")))
            sheet.cell(row=row_num, column=cols["sync_note"], value=new_row.get("sync_note", ""))
            sheet.cell(row=row_num, column=cols["wp_mode"], value=new_row.get("wp_mode") or "")
            sheet.cell(row=row_num, column=cols["new_subject"], value=new_row.get("new_subject") or "")
            sheet.cell(row=row_num, column=cols["new_project"], value=new_row.get("new_project") or "")
            if new_row.get("wp_id") is not None:
                sheet.cell(row=row_num, column=cols["work_package_id"], value=new_row["wp_id"])

    wb.save(file_path)


def _as_date_cell(value):
    """"YYYY-MM-DD" -> date object เพื่อให้เซลล์เป็นวันที่จริงเหมือนแถวเดิมในไฟล์"""
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return value


def _get_or_create_column(sheet, headers: dict, header_name: str) -> int:
    if header_name in headers:
        return headers[header_name]
    next_col = sheet.max_column + 1
    sheet.cell(row=1, column=next_col, value=header_name)
    headers[header_name] = next_col
    return next_col


def export_time_entries_to_excel(
    entries: list[dict], excel_cfg: dict, file_path: str, user_email: str | None = None
) -> None:
    """ส่งออก time entries (จาก _transform_time_entry) เป็นไฟล์ .xlsx รูปแบบเดียวกับไฟล์อัปโหลด

    ใช้ชื่อคอลัมน์จาก excel_cfg["columns"] ตัวเดียวกับที่ read_timesheet/update_timesheet_rows ใช้
    (ไม่ใช่ header ใหม่) เพื่อให้ไฟล์นี้เปิดกลับเข้า "อัปโหลดไฟล์ใหม่" ได้จริงถ้าต้องการ — แถวทุกแถวเป็น
    synced=True/wp_mode=existing อยู่แล้วเพราะเป็นข้อมูลที่ sync สำเร็จบน OpenProject ก่อนหน้านี้
    """
    sheet_name = excel_cfg.get("sheet_name", "Sheet1")
    columns = excel_cfg["columns"]

    rows = [
        {
            columns["date"]: entry["date"],
            columns["work_package_id"]: entry["work_package_id"],
            columns["hours"]: entry["hours"],
            columns["activity"]: entry["activity"],
            columns["comment"]: entry["comment"],
            columns["user_email"]: user_email or "",
            columns["synced"]: True,
            columns["sync_note"]: "",
            columns["wp_mode"]: "existing",
            columns["new_subject"]: "",
            columns["new_project"]: "",
        }
        for entry in entries
    ]
    df = pd.DataFrame(rows, columns=[columns[key] for key in DEFAULT_COLUMNS])
    df.to_excel(file_path, index=False, sheet_name=sheet_name)


PASTE_COLUMN_HELP = "วันที่ · ชั่วโมง · ชื่องาน · [โปรเจกต์] · [หมายเหตุ]"
_HEADER_WORDS = ("วันที่", "date", "ชั่วโมง", "hours")


def parse_pasted_rows(text: str) -> tuple[list[dict], list[str]]:
    """แปลงข้อความที่คัดลอกมาจาก Excel/Sheets เป็น rows สำหรับ generate_task_template

    คืน (rows, errors) — errors เป็นข้อความรายบรรทัดที่อ่านไม่ได้ พร้อมเลขบรรทัด
    บรรทัดที่อ่านไม่ได้ต้องไม่หายเงียบ ผู้เรียกต้องเอา errors ไปแสดงเสมอ

    รูปแบบ: คั่นด้วย Tab เป็นหลัก (Excel/Sheets คัดลอกมาเป็น TSV) ถ้าบรรทัดนั้นไม่มี Tab
    เลยจึงลองคอมมา — ไม่สลับกลับ เพราะชื่องานภาษาไทยมีคอมมาปนได้บ่อยกว่ามี Tab
    """
    rows: list[dict] = []
    errors: list[str] = []

    for lineno, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue

        parts = raw.split("\t") if "\t" in raw else raw.split(",")
        parts = [p.strip() for p in parts]

        if len(parts) < 3:
            errors.append(f"บรรทัด {lineno}: ต้องมีอย่างน้อย 3 คอลัมน์ ({PASTE_COLUMN_HELP})")
            continue

        date_text, hours_text, subject = parts[0], parts[1], parts[2]
        project = parts[3] if len(parts) > 3 else ""
        comment = parts[4] if len(parts) > 4 else ""

        date = _parse_paste_date(date_text)
        hours = _parse_paste_hours(hours_text)

        # แถวหัวตาราง: อ่านทั้งวันที่และชั่วโมงไม่ได้ และมีคำที่ใช้เป็นหัวคอลัมน์ -> ข้ามเงียบได้
        if date is None and hours is None:
            lowered = raw.lower()
            if any(word in lowered for word in _HEADER_WORDS):
                continue

        if date is None:
            errors.append(f"บรรทัด {lineno}: วันที่ '{date_text}' อ่านไม่ได้ (ใช้ YYYY-MM-DD หรือ DD/MM/YYYY)")
            continue
        if hours is None:
            errors.append(f"บรรทัด {lineno}: ชั่วโมง '{hours_text}' ต้องเป็นตัวเลขมากกว่า 0")
            continue
        if not subject:
            errors.append(f"บรรทัด {lineno}: ไม่มีชื่องาน")
            continue

        rows.append({
            "date": date,
            "hours": hours,
            "activity": "",
            "project": project,
            "subject": subject,
            "comment": comment,
        })

    return rows, errors


def _parse_paste_date(text: str) -> str | None:
    """คืน "YYYY-MM-DD" หรือ None ถ้าอ่านไม่ได้"""
    text = (text or "").strip()
    if not text:
        return None
    # DD/MM/YYYY กับ DD-MM-YYYY ใช้กันทั่วไปในไทย ส่วน ISO เป็นรูปแบบที่ไฟล์ template ใช้จริง
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _parse_paste_hours(text: str) -> float | None:
    """คืนจำนวนชั่วโมง (> 0) หรือ None ถ้าอ่านไม่ได้"""
    text = (text or "").strip().replace(",", "")  # ตัดตัวคั่นหลักพันที่ Excel ใส่มา
    if not text:
        return None
    try:
        hours = float(text)
    except ValueError:
        return None
    return hours if hours > 0 else None


def find_duplicate_subjects(client, pairs: list[tuple[object, str]]) -> dict[str, list[dict]]:
    """หา Work Package ที่ชื่อ "ตรงกันเป๊ะ" กับชื่องานที่กำลังจะสร้างใหม่

    pairs = [(project_id, subject)] ที่ไม่ซ้ำกันแล้ว — ยิง 1 คำขอต่อคู่ จึงควรกรองให้เหลือเฉพาะ
    ที่จำเป็นก่อนเรียก search_work_packages ค้นแบบ contains (operator "~") จึงต้องกรองชื่อซ้ำเอง

    ตัวไหนค้นไม่สำเร็จให้ข้ามไป ไม่ให้ล้มทั้งชุด — นี่เป็นแค่คำเตือนก่อน submit ไม่ใช่ตัวบล็อก

    ยิงพร้อมกันหลายตัวเพราะเดิมเป็น sequential — 100 ชื่อ = 100 round-trip เรียงกันก่อนที่ submit
    จะได้เริ่ม เป็น GET ล้วนไม่เขียนอะไร ขนานได้ปลอดภัย (ต่างจาก submit_rows ที่ยังเรียงตามเดิม)
    """
    names = []
    seen = set()
    for project_id, subject in pairs:
        name = (subject or "").strip()
        if name and (project_id, name) not in seen:
            seen.add((project_id, name))
            names.append((project_id, name))
    if not names:
        return {}

    def lookup(project_id, name: str):
        try:
            matches = client.search_work_packages(name, project_id=project_id)
        except OpenProjectAPIError:
            return name, []
        return name, [
            wp for wp in matches if (wp.get("subject") or "").strip().lower() == name.lower()
        ]

    found: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=min(DUP_CHECK_WORKERS, len(names))) as pool:
        for name, exact in pool.map(lambda pair: lookup(*pair), names):
            if exact:
                found[name] = exact
    return found


# ตัวย่อวันในสัปดาห์ เรียงตาม date.weekday() (0 = จันทร์)
THAI_WEEKDAYS = ("จ", "อ", "พ", "พฤ", "ศ", "ส", "อา")


def summarize_coverage(
    entries: list[dict],
    date_from: str,
    date_to: str,
    hours_per_day: float = 8.0,
    today: "date_cls | None" = None,
) -> dict:
    """วันทำงานไหนในช่วงที่ค้นหายังลงชั่วโมงไม่ถึงเป้า และวันไหนลงเกิน

    ไม่ยิง API เลย — คิดจาก entries ชุดเดียวกับที่ตารางแสดงอยู่แล้ว ตัวเลข hours_per_day มาจาก
    config worklog ตัวเดียวกับที่แท็บสรุปงานใช้ตอนให้ Claude แตก task

    กติกาที่เลือกไว้ และเหตุผล:

    * **นับเฉพาะจันทร์-ศุกร์** — โปรเจกต์นี้ไม่มีปฏิทินวันหยุด และข้อมูลจริงไม่เคยมีการลงชั่วโมง
      วันเสาร์-อาทิตย์เลย
    * **"ขาด" นับเฉพาะวันที่ผ่านไปแล้ว (< today)** — วันนี้ยังลงไม่เสร็จเป็นเรื่องปกติ ถ้านับด้วย
      จะขึ้นคำเตือนทุกเช้าจนคนเลิกอ่านมันไปเลย รับ today เข้ามาแทนการเรียก date.today() เองข้างใน
      เพื่อให้เขียนเทสต์ที่ไม่ผูกกับนาฬิกาเครื่องได้
    * **"เกิน" นับทุกวันรวมวันนี้และเสาร์-อาทิตย์** — ต่างจาก "ขาด" ตรงที่ชั่วโมงที่ลงเกินไปแล้ว
      ไม่ได้ถูกต้องขึ้นเองทีหลัง รู้เร็วยิ่งดี และวันหยุดที่มีชั่วโมงเกินเป้าก็ผิดปกติพอที่จะบอก
    * วันลาที่ผู้ใช้ลงเป็น work package จะมีชั่วโมงอยู่แล้วจึงไม่ถูกนับว่าขาด ส่วนวันหยุดราชการที่
      ไม่ได้ลงอะไรเลยจะขึ้นเป็น "ขาด" — ข้อจำกัดที่ยอมรับ (ดู README)

    **ห้าม raise** — เป็นข้อมูลประกอบข้างตาราง ไม่ใช่ตัวบล็อก ช่วงวันที่ที่อ่านไม่ออกคืนผลว่างไป

    คืน:
      missing  [{"date", "weekday", "hours"}]  วันทำงานที่ลงไม่ถึงเป้า เรียงตามวันที่
      over     [{"date", "hours"}]             วันที่ลงเกินเป้า
      target   float                           เป้าหมายชั่วโมง/วันที่ใช้คิด
    """
    try:
        target = float(hours_per_day or 0)
    except (TypeError, ValueError):
        target = 0.0
    result: dict = {"missing": [], "over": [], "target": target}
    if target <= 0:
        return result

    try:
        start = date_cls.fromisoformat(date_from)
        end = date_cls.fromisoformat(date_to)
    except (TypeError, ValueError):
        return result
    if start > end:
        return result

    cutoff = today or date_cls.today()

    per_date: dict[str, float] = {}
    for entry in entries or []:
        day_key = entry.get("date")
        if not day_key:
            continue
        try:
            hours = float(entry.get("hours") or 0)
        except (TypeError, ValueError):
            hours = 0.0
        per_date[day_key] = per_date.get(day_key, 0.0) + hours

    day = start
    while day <= end:
        key = day.isoformat()
        hours = per_date.get(key, 0.0)
        if hours > target:
            result["over"].append({"date": key, "hours": hours})
        elif day < cutoff and day.weekday() < 5 and hours < target:
            result["missing"].append(
                {"date": key, "weekday": THAI_WEEKDAYS[day.weekday()], "hours": hours}
            )
        day += timedelta(days=1)

    return result


def check_existing_work(client, pairs: list[tuple[object, str]], date_from: str, date_to: str) -> dict:
    """งานที่กำลังจะสร้าง มีอยู่บน OpenProject แล้วหรือยัง และวันนั้นลงชั่วโมงไปแล้วหรือยัง

    รวมสองคำถามไว้ในเรียกครั้งเดียวเพราะฝั่ง UI ยิงผ่าน run_async ทีเดียว ไม่ต้องต่อ callback
    สามทอด (get_current_user -> get_my_time_entries -> find_duplicate_subjects) แบบที่
    history_tab ต้องทำ ทั้งหมดเป็น GET ล้วน ไม่สร้างและไม่แก้อะไรบนเซิร์ฟเวอร์

    **ห้าม raise** — นี่เป็นคำเตือนก่อนส่ง ไม่ใช่ตัวบล็อก ส่วนไหนล้มก็ใส่ข้อความลง errors แล้วคืน
    ส่วนที่เหลือ (หลักเดียวกับ find_duplicate_subjects ที่ข้ามชื่อที่ค้นไม่สำเร็จ) จึงจับ Exception
    กว้างๆ ไม่ใช่เฉพาะ OpenProjectAPIError — requests โยน error คนละตระกูลออกมาได้อีกหลายแบบ

    คืน:
      duplicates      {subject: [wp, ...]}       WP ชื่อตรงเป๊ะที่มีอยู่แล้วในโปรเจกต์นั้น
      logged_by_date  {date: {"hours", "count"}} ชั่วโมงที่ลงไปแล้วทั้งวัน ใช้เตือนว่าลงซ้ำ
      logged_wp_dates {(wp_id, date): hours}     งานนั้นวันนั้นลงไปแล้วกี่ชั่วโมง (ซ้ำแน่ๆ)
      errors          [str]
    """
    result: dict = {"duplicates": {}, "logged_by_date": {}, "logged_wp_dates": {}, "errors": []}

    if pairs:
        try:
            result["duplicates"] = find_duplicate_subjects(client, pairs)
        except Exception as e:
            result["errors"].append(f"ตรวจชื่องานซ้ำไม่สำเร็จ: {e}")

    if not (date_from and date_to):
        return result

    try:
        user_id = (client.get_current_user() or {}).get("id")
        if not user_id:
            raise OpenProjectAPIError("ไม่พบข้อมูลผู้ใช้ปัจจุบันจาก OpenProject")
        entries = client.get_my_time_entries(user_id, date_from, date_to)
    except Exception as e:
        result["errors"].append(f"ตรวจชั่วโมงที่ลงไปแล้วไม่สำเร็จ: {e}")
        return result

    by_date: dict[str, dict] = {}
    by_wp_date: dict[tuple, float] = {}
    for entry in entries:
        date = entry.get("date")
        if not date:
            continue
        try:
            hours = float(entry.get("hours") or 0)
        except (TypeError, ValueError):
            hours = 0.0
        slot = by_date.setdefault(date, {"hours": 0.0, "count": 0})
        slot["hours"] += hours
        slot["count"] += 1
        wp_id = entry.get("work_package_id")
        if wp_id is not None:
            key = (wp_id, date)
            by_wp_date[key] = by_wp_date.get(key, 0.0) + hours

    result["logged_by_date"] = by_date
    result["logged_wp_dates"] = by_wp_date
    return result


def generate_task_template(rows: list[dict], excel_cfg: dict, file_path: str) -> None:
    """สร้างไฟล์ .xlsx รูปแบบเดียวกับไฟล์อัปโหลด จาก rows ที่ระบุ date/hours/activity/project/subject
    มาแล้ว — synced=False ทุกแถว

    แถวที่ส่ง wp_id มาด้วย (แท็บสรุปงานเช็คแล้วว่างานนี้มีอยู่บน OpenProject จริง) เขียนเป็น
    wp_mode="existing" ผูกกับงานนั้นเลย เปิดในตารางหลักแล้วขึ้น "พร้อมส่ง" ทันทีไม่ต้องเลือก WP เอง
    แถวที่ไม่มี wp_id เป็น wp_mode="new" เหมือนเดิม ต้องไปเลือก/สร้าง WP จริงในตารางหลักก่อนถึง
    จะ submit ได้ (แท็บ "สร้างไฟล์ Template" ไม่เคยส่ง wp_id มา จึงได้ไฟล์แบบเดิมทุกช่อง)
    """
    sheet_name = excel_cfg.get("sheet_name", "Sheet1")
    columns = excel_cfg["columns"]

    excel_rows = [
        {
            columns["date"]: r["date"],
            columns["work_package_id"]: r.get("wp_id"),
            columns["hours"]: r["hours"],
            columns["activity"]: r["activity"],
            # ผู้เรียกเดิม (โหมดกรอกในแอป) ไม่ส่ง comment มา — โหมด "วางจาก Excel" ส่งมาได้
            columns["comment"]: r.get("comment", ""),
            columns["user_email"]: "",
            columns["synced"]: False,
            columns["sync_note"]: "",
            # แถว existing เว้น new_subject/new_project ว่างตามรูปแบบเดียวกับที่
            # export_time_entries_to_excel เขียนแถวที่ sync แล้ว
            columns["wp_mode"]: "existing" if r.get("wp_id") is not None else "new",
            columns["new_subject"]: "" if r.get("wp_id") is not None else r["subject"],
            columns["new_project"]: "" if r.get("wp_id") is not None else r["project"],
        }
        for r in rows
    ]
    df = pd.DataFrame(excel_rows, columns=[columns[key] for key in DEFAULT_COLUMNS])
    df.to_excel(file_path, index=False, sheet_name=sheet_name)

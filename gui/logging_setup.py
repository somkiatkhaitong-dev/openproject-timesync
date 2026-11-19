"""ตั้งค่า log ลงไฟล์ให้ทั้ง GUI และ CLI

เหตุผลที่ต้องมี: build แบบ --windowed ไม่มี console ทำให้ sys.stdout/sys.stderr เป็น None
ของเดิมจึง redirect ทิ้งลง os.devnull เพื่อกัน print() crash ผลข้างเคียงคือ traceback.print_exc()
ใน gui/threads.py และ print("[WARN] ...") ใน gui/controller.py หายไปหมด เวลาเครื่องปลายทางมีปัญหา
จึงไม่เหลือหลักฐานอะไรเลยนอกจากข้อความสั้นๆ ในกล่องแดง

โมดูลนี้เปลี่ยนปลายทางจาก devnull เป็นไฟล์ log แทน — print()/traceback ที่กระจายอยู่ทั่วโค้ดเดิม
จึงถูกเก็บทันทีโดยไม่ต้องแก้ call site ใดๆ
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import tempfile

LOG_FILENAME = "timesheetsync.log"
_MAX_BYTES = 1024 * 1024
_BACKUP_COUNT = 3

logger = logging.getLogger("timesheetsync")

_log_path: str | None = None


def log_file_path() -> str | None:
    """path ของไฟล์ log ที่ใช้อยู่จริง (None ถ้าเขียนไม่ได้เลย) — diagnostics เอาไปแสดง"""
    return _log_path


class _StreamToLogger:
    """ไฟล์ปลอมที่รับ write() แล้วส่งต่อเข้า logger ทีละบรรทัด

    ต้อง buffer เองเพราะ print() เรียก write() แยกระหว่างตัวข้อความกับ "\\n" และ
    traceback.print_exc() ยิง write() มาหลายครั้งต่อหนึ่ง traceback ถ้า log ทุก write()
    จะได้ log ที่ขาดเป็นชิ้นๆ อ่านไม่รู้เรื่อง
    """

    def __init__(self, level: int, mirror=None):
        self._level = level
        self._mirror = mirror
        self._buffer = ""

    def write(self, text) -> int:
        if not isinstance(text, str):
            text = str(text)
        if self._mirror is not None:
            try:
                self._mirror.write(text)
            except Exception:
                self._mirror = None
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                logger.log(self._level, line.rstrip())
        return len(text)

    def flush(self) -> None:
        if self._buffer.strip():
            logger.log(self._level, self._buffer.rstrip())
        self._buffer = ""
        if self._mirror is not None:
            try:
                self._mirror.flush()
            except Exception:
                self._mirror = None

    def isatty(self) -> bool:
        return False

    def writable(self) -> bool:
        return True


def _candidate_log_dirs(base_dir: str) -> list[str]:
    dirs = [os.path.join(base_dir, "logs")]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(os.path.join(local, "TimesheetSync", "logs"))
    dirs.append(os.path.join(tempfile.gettempdir(), "TimesheetSync-logs"))
    return dirs


def setup_logging(base_dir: str, *, capture_streams: bool = True) -> str | None:
    """เริ่ม log ลงไฟล์ คืน path ที่ใช้จริง (None ถ้าเขียนไม่ได้ทุกที่ — แอปยังต้องเปิดได้ตามปกติ)

    base_dir คือโฟลเดอร์ข้าง .exe (ดู gui/paths.app_base_dir) ซึ่ง installer ลงไว้ที่
    {localappdata}\\TimesheetSync จึงเขียนได้โดยไม่ต้องใช้สิทธิ์ Admin ส่วน fallback มีไว้เผื่อ
    กรณีมีคนย้าย .exe ไปไว้ใน Program Files เอง
    """
    global _log_path

    if logger.handlers:
        return _log_path

    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    for directory in _candidate_log_dirs(base_dir):
        path = os.path.join(directory, LOG_FILENAME)
        try:
            os.makedirs(directory, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
            )
        except OSError:
            continue
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        _log_path = path
        break

    if capture_streams:
        # frozen+windowed → stream เป็น None อยู่แล้ว, ตอน dev → ยังพิมพ์ออก console ด้วย (mirror)
        sys.stdout = _StreamToLogger(logging.INFO, mirror=_as_utf8(sys.stdout))
        sys.stderr = _StreamToLogger(logging.ERROR, mirror=_as_utf8(sys.stderr))

    return _log_path


def _as_utf8(stream):
    """บังคับ console ให้รับ UTF-8 และไม่โยน UnicodeEncodeError

    ค่าเริ่มต้นของ stdout บน Windows คือ codepage ของ console ซึ่งบางเครื่องเข้ารหัสไทยไม่ได้
    ข้อความจะกลายเป็นอักขระเพี้ยนหรือทำให้ print พังไปเลย errors="replace" กันเคสหลังไว้
    (เคสอักขระเพี้ยนแก้ที่ฝั่ง console ด้วย chcp 65001 — จึงเขียนรายงานลงไฟล์ให้ด้วยเสมอ)
    """
    if stream is None:
        return None
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass
    return stream


def log_startup_banner(config_path: str) -> None:
    """บรรทัดแรกของทุก session — ข้อมูลชุดนี้คือสิ่งที่ต้องถามผู้ใช้เป็นอย่างแรกเวลามีปัญหา"""
    from diagnostics import collect_network_env
    from version import APP_NAME, APP_VERSION

    logger.info("=" * 70)
    logger.info("%s %s เริ่มทำงาน", APP_NAME, APP_VERSION)
    logger.info("frozen=%s exe=%s", getattr(sys, "frozen", False), sys.executable)
    logger.info("python=%s", sys.version.replace("\n", " "))
    logger.info("config=%s (มีไฟล์: %s)", config_path, os.path.exists(config_path))
    logger.info("log=%s", _log_path)
    for name, value, warn in collect_network_env():
        if value:
            logger.info("env %s=%s%s", name, value, "  <-- override certifi" if warn else "")


def install_excepthook(on_error=None) -> None:
    """exception ที่หลุดออกมาถึง UI thread ของเดิมทำให้แอปตายเงียบๆ — ให้ log ไว้ก่อนเสมอ

    on_error เป็น callback ที่ไว้เด้ง QMessageBox (ฝั่ง Qt ส่งเข้ามา) เพื่อไม่ให้โมดูลนี้
    ต้อง import PySide6 — sync_timesheet.py ที่เป็น CLI ล้วนจะได้ใช้ร่วมกันได้
    """
    previous = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        logger.critical("exception ที่ไม่ได้ถูกจับ", exc_info=(exc_type, exc_value, exc_tb))
        if on_error is not None:
            try:
                on_error(exc_type, exc_value, exc_tb)
            except Exception:
                logger.exception("แสดงกล่อง error ไม่สำเร็จ")
        previous(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook

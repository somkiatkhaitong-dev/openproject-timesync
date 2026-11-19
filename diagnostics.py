"""ตรวจสอบสภาพแวดล้อมของเครื่องที่รันแอป แล้วสรุปเป็นข้อความให้ผู้ใช้ก็อปส่งกลับมาได้

ไม่ import Qt เลย เพื่อให้ทั้ง GUI (gui/diagnostics.py) และ CLI (sync_timesheet.py --diagnose)
เรียกใช้ตัวเดียวกัน

ลำดับการตรวจล้อกับลำดับที่ใช้ไล่หาสาเหตุจริงในเคสวันที่ 2026-08-07 (เครื่องปลายทางล็อกอินไม่ผ่าน
เพราะ REQUESTS_CA_BUNDLE ชี้ไป mitmproxy-ca-cert.pem) — ข้อ 2 คือข้อที่จะจับเคสแบบนั้นได้ทันที
"""
from __future__ import annotations

import os
import platform
import socket
import ssl
import sys
from datetime import datetime
from urllib.parse import urlparse

import certifi
import requests

OK = "[ ok ]"
WARN = "[warn]"
FAIL = "[fail]"
INFO = "[ -- ]"

# (ชื่อตัวแปร, คำอธิบาย, ทำให้ certifi ถูกแทนที่ทั้งก้อนหรือไม่)
NETWORK_ENV_VARS = [
    ("HTTP_PROXY", "proxy สำหรับ http", False),
    ("HTTPS_PROXY", "proxy สำหรับ https", False),
    ("NO_PROXY", "รายชื่อ host ที่ไม่ต้องผ่าน proxy", False),
    ("REQUESTS_CA_BUNDLE", "CA bundle ที่ requests ใช้แทน certifi", True),
    ("CURL_CA_BUNDLE", "CA bundle สำรองที่ requests อ่านต่อจากตัวบน", True),
    ("SSL_CERT_FILE", "CA bundle ระดับ OpenSSL", True),
    ("SSL_CERT_DIR", "โฟลเดอร์ CA ระดับ OpenSSL", True),
]


def collect_network_env() -> list[tuple[str, str, bool]]:
    """คืน (ชื่อ, ค่า, เป็นตัวที่ override certifi ไหม) — ค่าว่างหมายถึงไม่ได้ตั้งไว้

    ใช้ร่วมกันระหว่าง startup banner ใน log กับหน้าตรวจสอบระบบ
    """
    out = []
    for name, _desc, overrides in NETWORK_ENV_VARS:
        out.append((name, os.environ.get(name, "") or os.environ.get(name.lower(), ""), overrides))
    return out


def redact_key(api_key: str) -> str:
    """API key ต้องไม่โผล่ใน log หรือในรายงานที่ผู้ใช้ก็อปส่งต่อ — บอกแค่ว่ามีและยาวเท่าไร

    ความยาวมีประโยชน์จริงตอนไล่ปัญหา เพราะจับเคสวางไม่ครบ/วางติดช่องว่างได้
    """
    if not api_key:
        return "(ยังไม่ได้กรอก)"
    return f"(ซ่อนไว้ ยาว {len(api_key)} ตัวอักษร)"


def _section(lines: list[str], title: str) -> None:
    lines.append("")
    lines.append(f"--- {title} " + "-" * max(0, 56 - len(title)))


def _check_app(lines: list[str], config_path: str | None) -> None:
    from version import APP_NAME, APP_VERSION

    _section(lines, "แอปและไฟล์")
    lines.append(f"{INFO} {APP_NAME} {APP_VERSION}")
    lines.append(f"{INFO} frozen={getattr(sys, 'frozen', False)}  exe={sys.executable}")
    lines.append(f"{INFO} python={sys.version.split()[0]}  {platform.platform()}")

    try:
        from gui.logging_setup import log_file_path

        lines.append(f"{INFO} log={log_file_path() or '(เขียนไม่ได้)'}")
    except Exception:
        pass

    if config_path:
        exists = os.path.exists(config_path)
        lines.append(f"{OK if exists else INFO} config={config_path} (มีไฟล์: {exists})")
        directory = os.path.dirname(config_path) or "."
        writable = os.access(directory, os.W_OK)
        lines.append(
            f"{OK if writable else FAIL} โฟลเดอร์ config เขียนได้: {writable}"
            + ("" if writable else "  <-- ติ๊ก 'จำไว้' แล้วจะบันทึกไม่สำเร็จ")
        )


def _check_env(lines: list[str]) -> None:
    _section(lines, "ตัวแปรสภาพแวดล้อมที่มีผลต่อการเชื่อมต่อ")
    found_override = False
    for name, value, overrides in collect_network_env():
        if not value:
            lines.append(f"{INFO} {name} = (ไม่ได้ตั้ง)")
            continue
        if overrides:
            found_override = True
            lines.append(f"{WARN} {name} = {value}")
        elif name.endswith("_PROXY") and name != "NO_PROXY":
            # proxy ที่ตั้งไว้เปลี่ยนเส้นทางการเชื่อมต่อทั้งหมด จึงต้องสะดุดตาเวลาไล่ปัญหา
            # ไม่ใช่ค่าปกติที่ผ่านเฉยๆ
            lines.append(f"{WARN} {name} = {value}  (การเชื่อมต่อทั้งหมดจะวิ่งผ่านตัวนี้)")
        else:
            lines.append(f"{OK} {name} = {value}")

    if found_override:
        lines.append("")
        lines.append("       ^ ตัวแปรที่ติด [warn] จะสั่งให้ไลบรารี network ใช้ไฟล์ CA นั้น")
        lines.append("         *แทนที่* ชุด CA มาตรฐานทั้งก้อน ไม่ใช่เพิ่มเข้าไป เครื่องที่ตั้งไว้")
        lines.append("         ใช้กับ mitmproxy/Fiddler มักเข้าเว็บ CA สาธารณะไม่ได้")
        lines.append("         แอปนี้กันไว้แล้วโดยระบุ CA bundle ของตัวเองตรงๆ ตอนเรียก API")


def _check_ca_bundle(lines: list[str]) -> None:
    _section(lines, "ชุด CA ที่แอปใช้จริง")
    path = certifi.where()
    lines.append(f"{INFO} certifi = {path}")
    try:
        data = open(path, "rb").read()
        count = data.count(b"BEGIN CERTIFICATE")
        lines.append(f"{OK} อ่านได้ {len(data):,} bytes, {count} ใบ")
    except OSError as e:
        lines.append(f"{FAIL} อ่านไฟล์ไม่ได้: {e}")

    proxies = requests.utils.getproxies()
    if proxies:
        lines.append(f"{WARN} proxy ที่ requests จะใช้จริง: {proxies}")
    else:
        lines.append(f"{OK} ไม่มี proxy (ต่อตรง)")


def _check_network(lines: list[str], url: str, api_key: str) -> None:
    _section(lines, "การเชื่อมต่อไปยัง OpenProject")

    if not url:
        lines.append(f"{INFO} ยังไม่ได้กรอก URL — ข้ามการตรวจส่วนนี้")
        return

    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    lines.append(f"{INFO} url={url}")
    lines.append(f"{INFO} host={host}  port={port}  api_key={redact_key(api_key)}")

    if not host:
        lines.append(f"{FAIL} แยกชื่อโฮสต์จาก URL ไม่ได้ — ตรวจว่าพิมพ์ URL ถูกไหม")
        return

    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        ips = sorted({i[4][0] for i in infos})
        lines.append(f"{OK} DNS: {', '.join(ips)}")
    except OSError as e:
        lines.append(f"{FAIL} DNS: หาไม่เจอ ({e}) — URL ผิด หรือเน็ตเข้าไม่ถึง DNS")
        return

    try:
        with socket.create_connection((host, port), timeout=15):
            lines.append(f"{OK} TCP: ต่อพอร์ต {port} ได้")
    except OSError as e:
        lines.append(f"{FAIL} TCP: ต่อไม่ได้ ({e}) — firewall บล็อก หรือเซิร์ฟเวอร์ไม่ตอบ")
        return

    if parsed.scheme != "https":
        lines.append(f"{INFO} ไม่ใช่ https — ข้ามการตรวจใบรับรอง")
    else:
        _check_tls(lines, host, port)

    _check_api(lines, url, api_key)


def _check_tls(lines: list[str], host: str, port: int) -> None:
    ctx = ssl.create_default_context(cafile=certifi.where())
    try:
        with socket.create_connection((host, port), timeout=15) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as tls:
                cert = tls.getpeercert() or {}
        lines.append(f"{OK} TLS: ตรวจใบรับรองผ่านด้วยชุด CA มาตรฐาน")
        lines.append(f"       subject : {_name_of(cert.get('subject'))}")
        lines.append(f"       issuer  : {_name_of(cert.get('issuer'))}")
        lines.append(f"       หมดอายุ  : {cert.get('notAfter', '?')}")
        return
    except ssl.SSLCertVerificationError as e:
        lines.append(f"{FAIL} TLS: ตรวจใบรับรองไม่ผ่าน — {e.verify_message or e}")
    except OSError as e:
        lines.append(f"{FAIL} TLS: handshake ไม่สำเร็จ ({e})")
        return

    # ตรวจไม่ผ่านด้วย CA มาตรฐาน ลองใหม่ด้วยชุด CA ของ Windows — ถ้าผ่านแปลว่ามีตัวกลาง
    # (proxy องค์กร / โปรแกรมสแกน HTTPS) เซ็นใบใหม่ให้ ซึ่ง Windows เชื่อถือแต่ certifi ไม่รู้จัก
    win_ctx = ssl.create_default_context()
    try:
        win_ctx.load_default_certs(ssl.Purpose.SERVER_AUTH)
        with socket.create_connection((host, port), timeout=15) as raw:
            with win_ctx.wrap_socket(raw, server_hostname=host) as tls:
                cert = tls.getpeercert() or {}
        lines.append(f"{WARN} แต่ผ่านด้วยชุด CA ของ Windows — มีตัวกลางดักและเซ็นใบรับรองใหม่")
        lines.append(f"       issuer  : {_name_of(cert.get('issuer'))}  <-- ตัวกลางที่ว่า")
        lines.append("       ถ้าเป็น proxy/แอนตี้ไวรัสขององค์กร ให้แจ้ง IT หรือยกเว้นโฮสต์นี้")
    except OSError:
        lines.append(f"{FAIL} ชุด CA ของ Windows ก็ตรวจไม่ผ่าน — ใบรับรองฝั่งเซิร์ฟเวอร์อาจมีปัญหาจริง")


def _name_of(rdn) -> str:
    """แปลง subject/issuer ที่ ssl คืนมาเป็นสตริงอ่านง่าย"""
    if not rdn:
        return "?"
    parts = []
    for group in rdn:
        for key, value in group:
            parts.append(f"{key}={value}")
    return ", ".join(parts)


def _check_api(lines: list[str], url: str, api_key: str) -> None:
    if not api_key:
        lines.append(f"{INFO} ยังไม่ได้กรอก API key — ข้ามการเรียก API")
        return

    from common import OpenProjectAPIError, OpenProjectClient

    client = OpenProjectClient(url, api_key)
    try:
        projects = client.get_projects()
    except OpenProjectAPIError as e:
        status = f" (HTTP {e.status_code})" if e.status_code else ""
        lines.append(f"{FAIL} เรียก /api/v3/projects ไม่สำเร็จ{status}")
        # ข้อความที่แปลแล้วมักยาวหลายบรรทัด — ย่อหน้าให้อยู่ในบล็อกเดียวกัน อ่านง่ายกว่าปล่อยชิดซ้าย
        for line in str(e).splitlines():
            lines.append(f"       {line}")
        if getattr(e, "detail", None) and e.detail != str(e):
            lines.append(f"       (ต้นฉบับ) {e.detail}")
    else:
        lines.append(f"{OK} เรียก /api/v3/projects สำเร็จ — เห็น {len(projects)} โปรเจกต์")


def _check_clock(lines: list[str]) -> None:
    _section(lines, "เวลาเครื่อง")
    now = datetime.now().astimezone()
    lines.append(f"{INFO} {now:%Y-%m-%d %H:%M:%S %Z%z}")
    lines.append("       ถ้าวันที่เพี้ยนไปมาก ใบรับรองจะดูเหมือนหมดอายุและ TLS จะไม่ผ่าน")


def run_diagnostics(url: str = "", api_key: str = "", config_path: str | None = None) -> str:
    """รันทุกการตรวจแล้วคืนรายงานเป็นข้อความล้วน — ปลอดภัยที่จะให้ผู้ใช้ก็อปส่งต่อ (ไม่มี api_key)

    ห้าม raise ออกไป: หน้าที่ของฟังก์ชันนี้คือรายงานปัญหา ไม่ใช่สร้างปัญหาใหม่
    """
    from version import APP_NAME, APP_VERSION

    lines = [
        f"รายงานตรวจสอบระบบ {APP_NAME} {APP_VERSION}",
        f"สร้างเมื่อ {datetime.now():%Y-%m-%d %H:%M:%S}",
    ]

    for check in (
        lambda: _check_app(lines, config_path),
        lambda: _check_env(lines),
        lambda: _check_ca_bundle(lines),
        lambda: _check_network(lines, url.strip(), api_key.strip()),
        lambda: _check_clock(lines),
    ):
        try:
            check()
        except Exception as e:  # noqa: BLE001 - รายงานต้องออกมาเสมอแม้บางหัวข้อจะพัง
            lines.append(f"{FAIL} ตรวจหัวข้อนี้ไม่สำเร็จ: {type(e).__name__}: {e}")

    lines.append("")
    lines.append("-" * 62)
    lines.append("ก็อปข้อความทั้งหมดนี้ส่งให้ผู้ดูแลได้เลย (ไม่มี API key อยู่ในนี้)")
    return "\n".join(lines)

"""
Control an Android phone from the PC via **ADB** (Android Debug Bridge).

**What happens when you plug in the USB cable?**
- The PC and phone negotiate USB; Windows loads a driver (often "Android ADB
  interface" or OEM composite device).
- If **USB debugging** is enabled, the `adbd` daemon on the phone can talk to
  the `adb` client on the PC. That is a *separate* channel from file transfer:
  you still allow debugging on the phone when prompted the first time.
- **USB tethering** or **MTP** is unrelated: your phone *also* using Wi-Fi is
  normal; the PC command `adb tcpip` tells `adbd` to listen on a TCP port so you
  can run `adb connect <phone-lan-ip>:5555` and then unplug USB; commands then
  go over Wi-Fi (phone and PC must be on the same network).

**Typical "cable in to Wi-Fi control" flow**
1. USB connected, one device: ``adb devices`` shows ``device``.
2. ``adb tcpip 5555`` (or this module's :func:`enable_wireless_adb`).
3. Read the phone's Wi-Fi IP (e.g. ``192.168.1.42``), then
   ``adb connect 192.168.1.42:5555``.
4. ``adb devices`` should list ``192.168.1.42:5555`` as *device*.
5. Unplug USB. If the wireless line stays *device*, you can keep using ADB.

Environment:
- **JARVIS_ADB_WIFI_IP** (or **JARVIS_PHONE_WIFI_IP**) — if reading the phone IP from
  the device fails, set this to the phone's static LAN address (e.g. ``192.168.1.50``) so
  ``adb connect`` can still run after ``tcpip`` on port 5555.
- **ADB_PATH** - full path to ``adb.exe`` (overrides everything).
- If unset, a copy under ``<this-project>/platform-tools/adb.exe`` is used if present
  (install with: ``uv run python scripts/fetch_platform_tools.py``).
- Otherwise the ``adb`` on the system **PATH** is used.
- **ADB_SERIAL** - optional device id from ``adb devices`` if more than one.

Voice/Jarvis integration: import these functions from another script or add thin
callables to your assistant **after** you are happy with the flow on the command
line (pairing, same LAN, and app packages may differ on your phone).
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

# WhatsApp / YouTube — override package with JARVIS_WHATSAPP_PACKAGE if needed
WHATSAPP_PKG = os.environ.get("JARVIS_WHATSAPP_PACKAGE", "com.whatsapp")
WHATSAPP_ALT_PKGS = tuple(
    p.strip()
    for p in os.environ.get("JARVIS_WHATSAPP_ALT", "com.whatsapp,com.whatsapp.w4b").split(",")
    if p.strip()
)
YOUTUBE_PKG = "com.google.android.youtube"


def _whatsapp_packages() -> list[str]:
    out: list[str] = []
    for p in (WHATSAPP_PKG, *WHATSAPP_ALT_PKGS):
        if p and p not in out:
            out.append(p)
    return out

logger = logging.getLogger("android_adb")
_bundled_adb_logged = False


@dataclass(frozen=True)
class WirelessResult:
    ok: bool
    message: str
    connect_host: str | None = None  # e.g. "192.168.1.5:5555"


def adb_executable() -> str:
    global _bundled_adb_logged
    p = (os.environ.get("ADB_PATH") or "").strip()
    if p:
        return p
    bundled = Path(__file__).resolve().parent / "platform-tools" / "adb.exe"
    if bundled.is_file():
        if not _bundled_adb_logged:
            logger.info("Using ADB at %s (project platform-tools).", bundled)
            _bundled_adb_logged = True
        return str(bundled)
    return "adb"


def _adb_base_args(serial: str | None) -> list[str]:
    exe = adb_executable()
    if serial or os.environ.get("ADB_SERIAL"):
        sid = (serial or os.environ.get("ADB_SERIAL") or "").strip()
        if sid:
            return [exe, "-s", sid]
    return [exe]


def _run(
    args: list[str],
    timeout: float = 60.0,
) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except FileNotFoundError as e:
        exe = args[0] if args else "adb"
        return (
            127,
            "",
            f"Cannot run {exe!r} (is Android platform-tools on PATH, or set ADB_PATH): {e}",
        )
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    return r.returncode, out, err


def _adb(args: list[str], serial: str | None = None, timeout: float = 60.0) -> tuple[int, str, str]:
    return _run(_adb_base_args(serial) + args, timeout=timeout)


def _adb_shell(
    command: str,
    serial: str | None = None,
    timeout: float = 30.0,
) -> tuple[int, str, str]:
    return _adb(["shell", command], serial=serial, timeout=timeout)


def explain_connection() -> str:
    """Return a short human-readable description of USB + Wi-Fi ADB (for CLI / logging)."""
    return __doc__ or ""


def adb_version_line() -> str:
    code, out, err = _adb(["version"], timeout=10.0)
    if code != 0:
        return f"adb not working (exit {code}): {err or out or 'no output'}"
    return out.splitlines()[0] if out else "adb: no version output"


def list_devices() -> str:
    code, out, err = _adb(["devices", "-l"], timeout=15.0)
    text = out if out else err
    if code != 0:
        return f"exit {code}\n{text}"
    return text


def ensure_adb_server() -> None:
    """Start the local adb server if it is not running (fixes empty device list glitches)."""
    _adb(["start-server"], timeout=25.0)


def device_status_summary() -> str:
    """
    One-line human hint from `adb devices` (unauthorized / offline / none / one ready).
    """
    ensure_adb_server()
    code, out, err = _adb(["devices"], timeout=15.0)
    text = (out or err or "").strip()
    if code != 0:
        return f"adb devices failed (exit {code}): {text or 'no output'}"
    lines = [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("List of")]
    if not lines:
        return "No devices listed. Replug USB, unlock the phone, enable USB debugging, and try: adb start-server (or replug cable)."
    ready = [l for l in lines if l.endswith("\tdevice")]
    unauth = [l for l in lines if l.endswith("\tunauthorized")]
    offline = [l for l in lines if l.endswith("\toffline")]
    if unauth and not ready:
        return "Phone listed but UNAUTHORIZED — on the phone tap Allow for USB debugging (RSA), then run check again."
    if offline and not ready:
        return "Device OFFLINE: unplug/replug USB, change cable or USB port, accept debugging."
    if ready:
        return f"One or more ready device(s): {len(ready)} in state 'device'."
    return f"Device states unclear. Raw: {text}"


def _first_usb_device_serial(devices_text: str) -> str | None:
    for line in devices_text.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return None


def get_usb_device_serial() -> str | None:
    ensure_adb_server()
    code, out, _ = _adb(["devices"], timeout=15.0)
    if code != 0:
        return None
    return _first_usb_device_serial(out)


def _parse_first_ipv4(text: str) -> str | None:
    for m in re.finditer(
        r"\b(?!127\.|169\.254\.)(?:\d{1,3}\.){3}\d{1,3}\b",
        text,
    ):
        ip = m.group(0)
        return ip
    return None


def _static_wifi_lan_ip_from_env() -> str | None:
    """Phone LAN address from JARVIS_ADB_WIFI_IP / JARVIS_PHONE_WIFI_IP when auto-read fails."""
    raw = (
        os.environ.get("JARVIS_ADB_WIFI_IP")
        or os.environ.get("JARVIS_PHONE_WIFI_IP")
        or ""
    ).strip()
    if not raw:
        return None
    m = re.fullmatch(
        r"(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})",
        raw,
    )
    if not m:
        return None
    try:
        if any(int(m.group(i)) > 255 for i in (1, 2, 3, 4)):
            return None
    except ValueError:
        return None
    if raw.startswith("127."):
        return None
    return raw


def get_phone_wifi_ip(serial: str | None = None) -> str | None:
    """
    Read the phone's Wi-Fi IP (LAN). Tries a few `adb shell` strategies for
    different Android versions; returns None if nothing plausible is found.
    """
    script_attempts = [
        "ip -f inet addr show wlan0 2>/dev/null",
        "ip -f inet addr show wlan0",
        "ifconfig wlan0 2>/dev/null",
        "getprop dhcp.wlan0.ipaddress 2>/dev/null",
    ]
    for s in script_attempts:
        code, out, _ = _adb_shell(s, serial=serial)
        if code == 0 and out:
            ip = _parse_first_ipv4(out)
            if ip:
                return ip
    # Some devices use wlan1 or tiwlan0
    for extra in (
        "ip -f inet addr show wlan1 2>/dev/null",
        "ip -f inet addr 2>/dev/null | head -n 30",
    ):
        code, out, _ = _adb_shell(extra, serial=serial)
        if code == 0 and out:
            ip = _parse_first_ipv4(out)
            if ip:
                return ip
    return None


def enable_wireless_adb(port: int = 5555, serial: str | None = None) -> WirelessResult:
    """
    On the *USB-attached* device: switch ``adbd`` to listen on *port*, discover
    Wi-Fi IP, and ``adb connect`` so you can unplug the cable. Phone and PC must
    share the same LAN/Wi-Fi for the next steps.
    """
    ensure_adb_server()
    env_sn = (os.environ.get("ADB_SERIAL") or "").strip() or None
    sid = (serial or env_sn) or get_usb_device_serial()
    if not sid:
        return WirelessResult(
            False,
            "No device in 'adb devices'. Connect USB, enable USB debugging, "
            "and authorize this computer (or set ADB_SERIAL to the id from 'adb devices').",
        )

    code, out, err = _adb(["tcpip", str(port)], serial=sid)
    combined = f"{out}\n{err}".strip()
    ok_tcpip = code == 0 or "restarting" in combined.lower() or f": {port}" in combined
    if not ok_tcpip:
        return WirelessResult(
            False,
            f"adb tcpip {port} failed: {combined or 'no output'}. Is the device online over USB?",
        )

    ip = get_phone_wifi_ip(serial=sid)
    ip_from_env = False
    if not ip:
        ip = _static_wifi_lan_ip_from_env()
        ip_from_env = bool(ip)
    if not ip:
        return WirelessResult(
            False,
            f"adbd is set to TCP port {port}, but could not read the phone Wi-Fi IP. "
            f"Set **JARVIS_ADB_WIFI_IP** to the phone's LAN address (e.g. 192.168.1.50), save, then ask again; "
            f"or with USB in, find IP in Settings, Wi-Fi, then run: adb connect <IP>:{port}\n{combined or ''}",
            connect_host=None,
        )

    host = f"{ip}:{port}"
    c2, o2, e2 = _adb(["connect", host], serial=None, timeout=20.0)
    msg = (o2 or e2 or "").strip()
    env_note = " (IP from JARVIS_ADB_WIFI_IP.)" if ip_from_env else ""
    if c2 == 0 and "connected" in msg.lower():
        return WirelessResult(
            True,
            f"Success. Wireless ADB: {msg}{env_note} You can unplug USB; keep Wi-Fi on the same network as this PC. "
            f"Target: {host}",
            connect_host=host,
        )
    if "connected" in msg.lower():
        return WirelessResult(
            True,
            f"{msg}{env_note} Target: {host}",
            connect_host=host,
        )
    return WirelessResult(
        False,
        f"Could not connect to {host}. Output: {msg or 'no output'}. "
        "Check firewall, same Wi-Fi, and that you still had USB for tcpip step.",
        connect_host=host,
    )


def adb_shell(
    command: str,
    serial: str | None = None,
) -> str:
    """Run ``adb shell <command>``; returns stdout+stderr for display."""
    code, out, err = _adb_shell(command, serial=serial)
    return (out or err or f"(exit {code})").strip()


def _am_start_launcher(package: str, serial: str | None) -> tuple[int, str, str, str]:
    code, o, e = _adb(
        [
            "shell",
            "am",
            "start",
            "-a",
            "android.intent.action.MAIN",
            "-c",
            "android.intent.category.LAUNCHER",
            "-p",
            package,
        ],
        serial=serial,
    )
    return code, o, e, f"am start -p {package}: {((o or e) or f'exit {code}')!s}".strip()


def _monkey_launch(package: str, serial: str | None) -> tuple[int, str, str, str]:
    # Launch main activity without knowing component name; works on many ROMs
    code, o, e = _adb(
        [
            "shell",
            "monkey",
            "-p",
            package,
            "-c",
            "android.intent.category.LAUNCHER",
            "1",
        ],
        serial=serial,
    )
    return code, o, e, f"monkey {package}: {((o or e) or f'exit {code}')!s}".strip()


def open_whatsapp(serial: str | None = None) -> str:
    """
    Open WhatsApp: try main package(s), `am start` then `monkey` fallback.
    Set JARVIS_WHATSAPP_PACKAGE or JARVIS_WHATSAPP_ALT for OEM variants.
    """
    ensure_adb_server()
    env_s = (os.environ.get("ADB_SERIAL") or "").strip() or None
    sid = (serial or env_s) or get_usb_device_serial()
    if not sid:
        return (
            f"{device_status_summary()} | Cannot open WhatsApp: no device in 'device' state. "
            "Authorize USB debugging on the phone if you see a prompt."
        )
    log: list[str] = []
    for pkg in _whatsapp_packages():
        c1, o1, e1, s1 = _am_start_launcher(pkg, sid)
        log.append(s1)
        m1 = f"{o1} {e1}"
        if c1 == 0 and "Starting: Intent" in m1 and "Error" not in m1:
            return "SUCCESS " + s1
        c2, o2, e2, s2 = _monkey_launch(pkg, sid)
        log.append(s2)
        m2 = f"{o2} {e2}"
        if c2 == 0 and "Events injected" in m2 and "No activities" not in m2:
            return "SUCCESS " + s2
        if c2 == 0 and "Error" not in m2 and "inaccessible" not in m2.lower() and m2.strip():
            return "SUCCESS " + s2
    return "FAILED. Tried: " + " | ".join(log)


def open_youtube(serial: str | None = None) -> str:
    ensure_adb_server()
    env_s = (os.environ.get("ADB_SERIAL") or "").strip() or None
    sid = (serial or env_s) or get_usb_device_serial()
    if not sid:
        return f"{device_status_summary()} | No device to open YouTube."
    code, o, e = _adb(
        [
            "shell",
            "am",
            "start",
            "-a",
            "android.intent.action.MAIN",
            "-c",
            "android.intent.category.LAUNCHER",
            "-p",
            YOUTUBE_PKG,
        ],
        serial=sid,
    )
    return (o or e or f"exit {code}").strip()


def youtube_open_search(
    query: str,
    serial: str | None = None,
) -> str:
    """
    Open the YouTube app to search results for *query* (user may need to tap play).
    Uses a VIEW intent; behavior depends on YouTube version.
    """
    ensure_adb_server()
    env_s = (os.environ.get("ADB_SERIAL") or "").strip() or None
    sid = (serial or env_s) or get_usb_device_serial()
    if not sid:
        return f"{device_status_summary()} | No device for YouTube search."
    q = urllib.parse.quote_plus(query.strip())
    url = f"https://www.youtube.com/results?search_query={q}"
    code, o, e = _adb(
        [
            "shell",
            "am",
            "start",
            "-a",
            "android.intent.action.VIEW",
            "-d",
            url,
            "-p",
            YOUTUBE_PKG,
        ],
        serial=sid,
    )
    return (o or e or f"exit {code}").strip()
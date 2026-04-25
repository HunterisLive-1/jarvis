"""
Gemini automatic function calling (AFC) wrappers for :mod:`android_adb`.
Requires Android ``platform-tools`` ``adb`` on PATH or **ADB_PATH**. Control
flows to the **connected phone** over USB or (after pairing) Wi-Fi ADB.

Disable all phone tools: set **JARVIS_ENABLE_PHONE_TOOLS=0**.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("jarvis_adb_tools")


def phone_tools_enabled() -> bool:
    return os.environ.get("JARVIS_ENABLE_PHONE_TOOLS", "1").lower() in (
        "1",
        "true",
        "yes",
    )


def phone_check_adb_and_devices() -> str:
    """Check that adb works and list connected devices (USB or Wi-Fi). Use when the user asks if the phone is connected, for debugging, or before other phone actions."""
    if not phone_tools_enabled():
        return "Phone tools are disabled (JARVIS_ENABLE_PHONE_TOOLS=0)."
    try:
        import android_adb as adb
    except ImportError as e:
        return f"Phone control module not available: {e!s}"
    exe = adb.adb_executable()
    summary = adb.device_status_summary()
    ver = adb.adb_version_line()
    dev = adb.list_devices()
    text = f"Summary: {summary}\n\nADB executable: {exe}\n\n{ver}\n\n{dev}"
    logger.info("phone_check_adb_and_devices: %s", text[:2000])
    try:
        from session_memory import note_tool_result

        note_tool_result("phone_check", text[:500])
    except ImportError:
        pass
    return text


def phone_enable_wireless_adb(port: int = 5555) -> str:
    """Switch the phone's adb to TCP (port 5555 by default), then ``adb connect``. If the phone IP cannot be read from the device, set **JARVIS_ADB_WIFI_IP** to the phone's static LAN address (e.g. 192.168.1.50) in the environment. Use when they want wireless ADB. USB must be connected for the ``tcpip`` step. After success, the return text may include a one-line hint to offer YouTube (see JARVIS_WIRELESS_FOLLOWUP_*)."""
    if not phone_tools_enabled():
        return "Phone tools are disabled (JARVIS_ENABLE_PHONE_TOOLS=0)."
    try:
        import android_adb as adb
    except ImportError as e:
        return f"Phone control not available: {e!s}"
    r = adb.enable_wireless_adb(port=port)
    out = r.message
    if r.connect_host:
        out += f" Target: {r.connect_host}."
    if r.ok:
        out += " User may unplug USB if the connection is stable."
        if os.environ.get("JARVIS_WIRELESS_FOLLOWUP_ASK_YT", "1").lower() in (
            "1",
            "true",
            "yes",
        ):
            q = (
                os.environ.get("JARVIS_WIRELESS_FOLLOWUP_YT_QUERY")
                or "Knife Bros Danda Noliwala"
            ).strip()
            if q:
                out += (
                    f" NEXT: Ask in one short line if the user wants you to open YouTube on the phone for "
                    f'"{q}". If they say yes, call phone_youtube_search_and_open with query exactly: {q!r}.'
                )
    logger.info("phone_enable_wireless_adb: %s", out[:2000])
    try:
        from session_memory import note_tool_result

        note_tool_result("phone_wireless", out[:500])
    except ImportError:
        pass
    return out


def phone_open_whatsapp() -> str:
    """Open WhatsApp on the connected Android phone via ADB. Use when the user asks to open WhatsApp on the phone."""
    if not phone_tools_enabled():
        return "Phone tools are disabled (JARVIS_ENABLE_PHONE_TOOLS=0)."
    try:
        import android_adb as adb
    except ImportError as e:
        return f"Phone control not available: {e!s}"
    s = adb.open_whatsapp()
    logger.info("phone_open_whatsapp: %s", s[:2000])
    try:
        from session_memory import note_tool_result

        note_tool_result("open_whatsapp", s[:500])
    except ImportError:
        pass
    return s


def phone_open_youtube() -> str:
    """Open the YouTube app on the connected Android phone. Use when they want YouTube opened on the phone."""
    if not phone_tools_enabled():
        return "Phone tools are disabled (JARVIS_ENABLE_PHONE_TOOLS=0)."
    try:
        import android_adb as adb
    except ImportError as e:
        return f"Phone control not available: {e!s}"
    s = adb.open_youtube()
    logger.info("phone_open_youtube: %s", s[:2000])
    try:
        from session_memory import note_tool_result

        note_tool_result("open_youtube", s[:500])
    except ImportError:
        pass
    return s


def phone_youtube_search_and_open(query: str) -> str:
    """Open YouTube on the phone to search results for the given song or topic. The user may need to tap play. Use for 'play X on YouTube on my phone' or search and play requests."""
    if not phone_tools_enabled():
        return "Phone tools are disabled (JARVIS_ENABLE_PHONE_TOOLS=0)."
    if not query.strip():
        return "Error: empty search query."
    try:
        import android_adb as adb
    except ImportError as e:
        return f"Phone control not available: {e!s}"
    s = adb.youtube_open_search(query.strip())
    logger.info("phone_youtube_search: %s", s[:2000])
    try:
        from session_memory import note_tool_result

        note_tool_result("youtube_search", s[:500])
    except ImportError:
        pass
    return s


JARVIS_ADB_TOOL_FUNCTIONS = [
    phone_check_adb_and_devices,
    phone_enable_wireless_adb,
    phone_open_whatsapp,
    phone_open_youtube,
    phone_youtube_search_and_open,
]

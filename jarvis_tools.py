"""
Tool callables for the voice assistant (Ollama tool calling). Each function returns a string.
"""

from __future__ import annotations

import json
from typing import Any
import logging
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger("jarvis_tools")

# Optional: full path to chrome.exe (Windows)
def _chrome_path() -> str | None:
    env = os.environ.get("JARVIS_CHROME")
    if env and Path(env).is_file():
        return env
    for p in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ):
        if Path(p).is_file():
            return p
    return None


def _open_chrome_args(url: str, new_tab: bool) -> list[str]:
    chrome = _chrome_path()
    if not chrome:
        return []
    if new_tab:
        return [chrome, "--new-tab", url]
    return [chrome, url]


def open_url_in_chrome(url: str, new_tab: bool = True) -> str:
    """Open a web address in Google Chrome. Use for news sites, searches, or any https URL. Prefer new_tab=True so it opens as a new tab."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url.lstrip("/")
    try:
        args = _open_chrome_args(url, new_tab)
        if args:
            subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return f"Opened in Chrome: {url}"
        import webbrowser

        webbrowser.open(url)
        return f"Opened in default browser (Chrome not found): {url}"
    except Exception as e:
        logger.exception("open_url_in_chrome")
        return f"Could not open URL: {e!s}"


def _normalize_url(url: str) -> str:
    u = (url or "").strip()
    if not u.startswith(("http://", "https://")):
        return "https://" + u.lstrip("/")
    return u


def _screen_wh() -> tuple[int, int]:
    if sys.platform == "win32":
        try:
            u = __import__("ctypes").windll.user32
            w = int(u.GetSystemMetrics(0))
            h = int(u.GetSystemMetrics(1))
            if w > 0 and h > 0:
                return w, h
        except (AttributeError, OSError) as e:
            logger.debug("GetSystemMetrics failed: %s", e)
    return 1920, 1080


def _work_area_rect_win32() -> tuple[int, int, int, int] | None:
    """(left, top, width, height) in pixels for SPI_GETWORKAREA, or None."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", wintypes.LONG),
                ("top", wintypes.LONG),
                ("right", wintypes.LONG),
                ("bottom", wintypes.LONG),
            ]

        r = RECT()
        SPI_GETWORKAREA = 0x30
        if not ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETWORKAREA, 0, ctypes.byref(r), 0
        ):
            return None
        w = int(r.right - r.left)
        h = int(r.bottom - r.top)
        if w < 200 or h < 200:
            return None
        return (int(r.left), int(r.top), w, h)
    except (AttributeError, OSError) as e:
        logger.debug("work area: %s", e)
        return None


def _classify_tiled_chrome_title(title: str) -> str | None:
    """'map' = conflict map window, 'news' = world news, None = unknown."""
    t = (title or "").lower()
    if "liveuamap" in t or "uamap" in t or "uamap.com" in t:
        return "map"
    if "ukraine" in t and ("map" in t or "interactive" in t):
        return "map"
    if "bbc" in t and ("news" in t or "world" in t):
        return "news"
    if "reuters" in t or "the guardian" in t or "ap news" in t or "npr" in t:
        return "news"
    if "world" in t and "latest" in t and "news" in t:
        return "news"
    if "conflict" in t and "map" in t:
        return "map"
    return None


def _win_tile_chrome_map_and_news() -> bool:
    """
    After Chrome starts, it often opens maximized and ignores --window-size. Un-maximize and
    place the map (LiveUAMap) on the left half, world news on the right half. Returns True if placed.
    """
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except OSError:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except OSError:
            pass

    user32 = ctypes.windll.user32
    results: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lp) -> wintypes.BOOL:  # type: ignore[return-value]
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        cl = ctypes.create_unicode_buffer(320)
        user32.GetClassNameW(hwnd, cl, 320)
        if cl.value != "Chrome_WidgetWin_1":
            return True
        title = ctypes.create_unicode_buffer(2048)
        if not user32.GetWindowTextW(hwnd, title, 2048) or not title.value.strip():
            return True
        # Main Chrome windows, not app shells / odd popups
        if "Google Chrome" not in title.value and "Chromium" not in title.value:
            return True
        results.append((int(hwnd), title.value))
        return True

    user32.EnumWindows(_cb, 0)
    if len(results) < 2:
        return False
    m_left: int | None = None
    m_right: int | None = None
    for h, t in results:
        k = _classify_tiled_chrome_title(t)
        if k == "map" and m_left is None:
            m_left = h
        if k == "news" and m_right is None:
            m_right = h
    if m_left is None and m_right is None and len(results) >= 2:
        t0, t1 = results[0][1].lower(), results[1][1].lower()
        if "map" in t0 or "ukraine" in t0 or "uam" in t0:
            m_left, m_right = results[0][0], results[1][0]
        elif "map" in t1 or "ukraine" in t1 or "uam" in t1:
            m_left, m_right = results[1][0], results[0][0]
    if m_left is None and len(results) >= 2 and m_right is None:
        t0, t1 = results[0][1].lower(), results[1][1].lower()
        if "bbc" in t0 or ("world" in t0 and "latest" in t0):
            m_right, m_left = results[0][0], results[1][0]
        elif "bbc" in t1 or ("world" in t1 and "latest" in t1):
            m_right, m_left = results[1][0], results[0][0]
    if m_left and not m_right:
        for h, t in results:
            if h != m_left and _classify_tiled_chrome_title(t) != "map":
                m_right = h
                break
    if m_right and not m_left:
        for h, t in results:
            if h != m_right and _classify_tiled_chrome_title(t) != "news":
                m_left = h
                break
    if m_left is None or m_right is None or m_left == m_right:
        return False

    wa = _work_area_rect_win32()
    if wa:
        x0, y0, wa_w, area_h = wa
    else:
        x0, y0 = 0, 0
        sw, sh = _screen_wh()
        wa_w, area_h = sw, sh
    half = max(wa_w // 2, 320)
    rw = max(wa_w - half, 320)

    SW_RESTORE = 9
    for hid in (m_left, m_right):
        if user32.IsZoomed(int(hid)):
            user32.ShowWindow(int(hid), SW_RESTORE)
    if not user32.MoveWindow(int(m_left), x0, y0, half, area_h, True):
        logger.warning("MoveWindow left failed")
    if not user32.MoveWindow(int(m_right), x0 + half, y0, rw, area_h, True):
        logger.warning("MoveWindow right failed")
    logger.info("Chrome tiled: map hwnd=%s, news hwnd=%s", m_left, m_right)
    return True


def _pygetwindow_tile_chrome_fallback() -> bool:
    try:
        import pygetwindow as gw
    except ImportError:
        return False
    time.sleep(0.3)
    wa = _work_area_rect_win32()
    if wa:
        x0, y0, wa_w, h = wa
    else:
        x0, y0 = 0, 0
        wa_w, h = _screen_wh()
    half = max(wa_w // 2, 320)
    rw = max(wa_w - half, 320)
    wins = []
    for w in gw.getAllWindows():
        t = w.title or ""
        if w.width < 100 or w.height < 100:
            continue
        if "Google Chrome" not in t and "Chromium" not in t and " - Google C" not in t:
            continue
        if _classify_tiled_chrome_title(t) is not None or "http" in t or "Ukr" in t or "BBC" in t:
            wins.append(w)
    if len(wins) < 2:
        return False
    a, b = wins[-1], wins[-2]
    try:
        for o in (a, b):
            if getattr(o, "isMaximized", False) or o.width > wa_w - 40:
                try:
                    o.restore()
                except Exception:
                    pass
    except Exception:
        pass
    lwin = rwin = None
    for o in (a, b):
        c = _classify_tiled_chrome_title(o.title)
        if c == "map":
            lwin = o
        elif c == "news":
            rwin = o
    if lwin and rwin:
        try:
            lwin.moveTo(x0, y0)
            lwin.resizeTo(half, h)
            rwin.moveTo(x0 + half, y0)
            rwin.resizeTo(rw, h)
            return True
        except Exception as e:
            logger.warning("pygetwindow tile: %s", e)
    return False


def _post_launch_tile_chrome_windows() -> None:
    """Call after two Chrome Popen: retry until we can place map left / news right."""
    if os.environ.get("JARVIS_CHROME_TILE_WINFIX", "1").lower() in ("0", "false", "no"):
        return
    first = float(os.environ.get("JARVIS_CHROME_TILE_FIRST_WAIT_S", "0.9"))
    time.sleep(max(0.0, first))
    n = int(os.environ.get("JARVIS_CHROME_TILE_ATTEMPTS", "6"))
    gap = float(os.environ.get("JARVIS_CHROME_TILE_RETRY_S", "0.45"))
    for i in range(max(1, n)):
        if i:
            time.sleep(gap)
        if _win_tile_chrome_map_and_news():
            return
        if _pygetwindow_tile_chrome_fallback():
            return
    logger.info("Chrome windows could not be tiled; user may use Win+arrows to snap each window.")


def open_two_urls_chrome_tiled(
    url_left: str, url_right: str, left_title_hint: str = "left", right_title_hint: str = "right"
) -> str:
    """
    Open two pages in two separate Chrome windows, each using half the screen (left and right).
    After launch, **Windows** repositions via MoveWindow: Chrome often starts maximized and ignores
    --window-size. Set JARVIS_CHROME_TILE_WINFIX=0 to skip that step.
    """
    a = _normalize_url(url_left)
    b = _normalize_url(url_right)
    chrome = _chrome_path()
    if not chrome:
        import webbrowser

        try:
            webbrowser.open(a)
            time.sleep(0.4)
            webbrowser.open(b)
        except OSError as e:
            return f"Could not open browsers: {e!s}"
        return (
            f"Chrome not found: opened two URLs in the default browser (tiled side-by-side needs Chrome: "
            f"install Google Chrome or set JARVIS_CHROME). Left ({left_title_hint}): {a}  Right ({right_title_hint}): {b}"
        )

    w, h = _screen_wh()
    half = max(w // 2, 400)
    rw = w - half
    try:
        # Left panel
        p1: list[str] = [
            chrome,
            "--new-window",
            f"--window-size={half},{h}",
            f"--window-position=0,0",
            a,
        ]
        subprocess.Popen(p1, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.6)
        # Right panel
        p2: list[str] = [
            chrome,
            "--new-window",
            f"--window-size={rw},{h}",
            f"--window-position={half},0",
            b,
        ]
        subprocess.Popen(p2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        logger.exception("open_two_urls_chrome_tiled")
        return f"Could not tile Chrome windows: {e!s} Try {open_url_in_chrome(a)} and {open_url_in_chrome(b)}."

    _post_launch_tile_chrome_windows()
    return (
        f"Tiled: two Chrome windows, ~50% work-area each (map left, news right) — {left_title_hint} on the LEFT: {a} ; "
        f"{right_title_hint} on the RIGHT: {b}. If anything still looks maximized, say they can Win+Left / Win+Right to snap."
    )


def close_all_google_chrome() -> str:
    """
    **Quit the Google Chrome application** — every window and tab closes. The user may have other
    work in Chrome; this is a full exit of Chrome, not a single tab. Use when they ask to close
    Chrome, or before sleep (see JARVIS_CLOSE_CHROME_ON_SLEEP). For Microsoft Edge, this tool does
    not apply (Chrome only).
    """
    if sys.platform == "win32":
        r = subprocess.run(
            ["taskkill", "/IM", "chrome.exe", "/F"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        err = (r.stderr or "") + (r.stdout or "")
        el = err.lower()
        if "not find" in el or "not found" in el or "no instance" in el or "not running" in el:
            return "Chrome was not running."
        if r.returncode == 0 or "success" in el or "terminated" in el or "termed" in el:
            return "All Google Chrome windows closed (chrome.exe ended)."
        return f"Could not close Chrome: {err.strip() or r.returncode}"

    # macOS
    if sys.platform == "darwin":
        r = subprocess.run(
            ["killall", "-9", "Google Chrome"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if r.returncode == 0:
            return "All Google Chrome windows closed."
        return "Chrome was not running, or killall was denied."

    # Linux / other Unix
    for name in (
        "chrome",
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
    ):
        r = subprocess.run(
            ["killall", "-9", name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode == 0:
            return f"All Google Chrome / Chromium windows closed (killall {name})."
    return "Could not find a running Chrome or Chromium to close."


def _ip_api_location() -> dict[str, Any] | None:
    """Free tier: public IP -> city, region, lat, lon. Not GPS; WiFi/ISP based."""
    # ip-api: HTTP only on free plan; fields kept minimal.
    try:
        url = "http://ip-api.com/json/?fields=status,message,country,regionName,city,lat,lon,query"
        req = urllib.request.Request(url, headers={"User-Agent": "livrkit-jarvis/1.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.warning("ip-api request failed: %s", e)
        return None
    if not isinstance(data, dict) or data.get("status") != "success":
        return None
    return data


def get_approximate_location() -> str:
    """Best-effort city/region/country and coordinates from this network's public IP (ISP-level, not room GPS). Use when the user asks where they are, to 'find' them, or for their current location. Speak the result; offer open_global_map with the city or use open_map_at_my_location to show a map tab."""
    data = _ip_api_location()
    if not data:
        return (
            "Could not determine location from the network. "
            "The user can try again or allow network access; this is not precise GPS."
        )
    city = (data.get("city") or "").strip()
    region = (data.get("regionName") or "").strip()
    country = (data.get("country") or "").strip()
    lat, lon = data.get("lat"), data.get("lon")
    query = (data.get("query") or "").strip()
    place = ", ".join(p for p in (city, region, country) if p)
    coord = f"{lat}, {lon}" if lat is not None and lon is not None else ""
    lines = [
        f"Approximate location (from public IP {query}): {place or 'Unknown place'}.",
    ]
    if coord:
        lines.append(f"Coordinates: {coord}.")
    lines.append("This is network-level (IP), not live GPS; suggest opening a map to confirm the area.")
    return "\n".join(lines)


def open_map_at_my_location() -> str:
    """Open Google Maps in Chrome centered on the user's approximate location (from public IP). Use when they want a map of where this PC/network appears to be."""
    data = _ip_api_location()
    if not data:
        return "Could not get coordinates to open the map. Try get_approximate_location for any partial info."
    lat, lon = data.get("lat"), data.get("lon")
    if lat is None or lon is None:
        city = (data.get("city") or "").strip()
        if city:
            return open_global_map(city)
        return "No coordinates or city to open on the map."
    url = f"https://www.google.com/maps?q={lat},{lon}"
    return open_url_in_chrome(url, new_tab=True)


def open_global_map(place_query: str = "") -> str:
    """Open Google Maps in Chrome. Empty place_query shows the world map; otherwise searches for the place (e.g. city or 'India')."""
    if place_query.strip():
        q = urllib.parse.quote_plus(place_query.strip())
        url = f"https://www.google.com/maps/search/?api=1&query={q}"
    else:
        url = "https://www.google.com/maps/@20,0,3z"
    return open_url_in_chrome(url, new_tab=True)


def _ddgs_class() -> type | None:
    """Prefer `ddgs` (renamed from duckduckgo_search); fall back to legacy import."""
    try:
        from ddgs import DDGS  # type: ignore[import-not-found]

        return DDGS
    except ImportError:
        pass
    try:
        from duckduckgo_search import DDGS  # type: ignore[import-not-found]

        return DDGS
    except ImportError:
        return None


def _ddgs_text_rows(query: str, max_results: int) -> list[dict]:
    DDGS = _ddgs_class()
    if not DDGS:
        return []
    try:
        with DDGS() as ddg:  # type: ignore[operator]
            try:
                rows = ddg.text(query.strip(), max_results=max_results, backend="auto")
            except TypeError:
                rows = ddg.text(query.strip(), max_results=max_results)
    except Exception as e:
        logger.warning("web text search failed: %s", e)
        return []
    if not rows:
        return []
    return list(rows)


def search_the_web(query: str) -> str:
    """Search the public web and return short snippets from top results. Use for facts, 'what is going on', or current events. query should be clear; use English for best international results unless the user asked in another language."""
    if not query.strip():
        return "Error: empty query."
    if _ddgs_class() is None:
        return "Error: install the `ddgs` package (pip install ddgs)."
    rows = _ddgs_text_rows(query, 6)
    if not rows:
        return "No search results returned."
    lines: list[str] = []
    for r in rows:
        title = (r.get("title") or "")[:140]
        body = (r.get("body") or "")[:320]
        href = (r.get("href") or "")[:200]
        lines.append(f"— {title}\n  {body}\n  {href}")
    return "Web search results:\n" + "\n".join(lines)[:6000]


def get_headline_news(topic: str = "world") -> str:
    """Get a quick snapshot of recent headlines by searching the web. topic is a hint such as world, technology, India, business."""
    DDGS = _ddgs_class()
    if not DDGS:
        return "Error: install the `ddgs` package (pip install ddgs)."
    q = f"latest breaking news {topic} today"
    rows: list[dict] = []
    try:
        with DDGS() as ddg:  # type: ignore[operator]
            if hasattr(ddg, "news"):
                try:
                    n = ddg.news(q, max_results=8, backend="auto")
                except TypeError:
                    n = ddg.news(q, max_results=8)
                rows = list(n) if n else []
            if not rows:
                rows = _ddgs_text_rows(q, 8)
    except Exception as e:
        logger.warning("headline search failed: %s", e)
        rows = _ddgs_text_rows(q, 8)
    if not rows:
        return "No news results returned. " + search_the_web(q)
    lines: list[str] = ["Recent headlines (news search):"]
    for r in rows:
        title = (r.get("title") or "")[:160]
        body = (r.get("body") or r.get("excerpt") or "")[:400]
        href = (r.get("url") or r.get("href") or "")[:220]
        if title or body:
            lines.append(f"— {title}\n  {body}\n  {href}")
    return "\n".join(lines)[:6000]


def what_is_going_on() -> str:
    """Returns a text digest of world headlines to summarize. For full “open map + then tell me” use `open_global_situation_briefing` in the same class of requests, or call `open_liveuamap_then_world_news` **before** this in the same turn. Use when the user asks what is going on, what is happening, war, conflict, or a situational briefing (including non-English transcripts)."""
    a = search_the_web(
        "what is going on in the world today top news war conflict international"
    )
    b = get_headline_news("world")
    c = get_headline_news("war conflict international")
    return f"{a[:3200]}\n\n---\n\n{b[:3200]}\n\n---\n\n{c[:3200]}"[:8000]


def open_global_situation_briefing() -> str:
    """**Primary tool** for world / war / conflict briefings: (1) **LiveUAMap** and (2) world news in **two Chrome windows** (default: left/right half of the screen — see `open_liveuamap_then_world_news` / JARVIS_CHROME_TILE), (3) returns headline digest text to read aloud in English. Same phrases as before. Do not use `open_global_map` for the global conflict map; use LiveUAMap."""
    try:
        from jarvis_browser_routines import open_liveuamap_then_world_news

        tabs = open_liveuamap_then_world_news()
    except Exception as e:
        logger.exception("open_global_situation_briefing tabs")
        tabs = f"(Browser tabs: {e})"
    data = what_is_going_on()
    return f"{tabs}\n\n--- HEADLINES & SEARCH (summarize in speech) ---\n{data}"


def focus_google_chrome() -> str:
    """Bring a Google Chrome window to the front so tab shortcuts apply. Call before chrome_tab_left/right if the user might be in another app."""
    try:
        import pygetwindow as gw
    except ImportError:
        return "pygetwindow not available."
    try:
        for w in gw.getAllWindows():
            t = (w.title or "").lower()
            if "chrome" in t and w.visible and not t.startswith("settings"):
                try:
                    w.activate()
                    time.sleep(0.2)
                    return "Chrome focused."
                except Exception:
                    continue
        return "No Chrome window found; open Chrome and try again."
    except Exception as e:
        return f"Could not focus Chrome: {e!s}"


def chrome_tab_right() -> str:
    """Switch to the next Chrome tab (right), like pressing Ctrl+Tab. User should have Chrome focused; this tries to focus Chrome first."""
    focus_google_chrome()
    time.sleep(0.25)
    try:
        import pyautogui

        pyautogui.PAUSE = 0.1
        pyautogui.hotkey("ctrl", "tab")
        return "Switched to the next tab (right)."
    except Exception as e:
        return f"Tab switch failed: {e!s}"


def chrome_tab_left() -> str:
    """Switch to the previous Chrome tab (left), like Ctrl+Shift+Tab."""
    focus_google_chrome()
    time.sleep(0.25)
    try:
        import pyautogui

        pyautogui.PAUSE = 0.1
        pyautogui.hotkey("ctrl", "shift", "tab")
        return "Switched to the previous tab (left)."
    except Exception as e:
        return f"Tab switch failed: {e!s}"


# Browser sequences (see jarvis_browser_routines.py) — world news + LiveUAMap, not user GPS
def _load_browser_routine_tools() -> list:
    try:
        from jarvis_browser_routines import (
            open_liveuamap_in_chrome,
            open_liveuamap_then_world_news,
            open_world_news_in_chrome,
            open_world_news_then_liveuamap,
        )

        return [
            open_world_news_in_chrome,
            open_liveuamap_in_chrome,
            open_liveuamap_then_world_news,
            open_world_news_then_liveuamap,
        ]
    except ImportError as e:
        logger.warning("jarvis_browser_routines unavailable: %s", e)
        return []


# List passed to Gemini GenerateContentConfig(tools=[...]) — must be sync callables
JARVIS_TOOL_FUNCTIONS = [
    search_the_web,
    get_headline_news,
    what_is_going_on,
    open_global_situation_briefing,
    get_approximate_location,
    open_map_at_my_location,
    open_global_map,
    *(_load_browser_routine_tools()),
    open_url_in_chrome,
    close_all_google_chrome,
    focus_google_chrome,
    chrome_tab_left,
    chrome_tab_right,
]

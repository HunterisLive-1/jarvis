"""
Browser sequences for world + conflict: **LiveUAMap (global map) is opened before** a world news tab
when the user wants the full “what’s going on” experience. Not the user’s personal GPS.
Imported by `jarvis_tools` for Gemini AFC. Uses lazy imports to avoid circular imports.
"""

from __future__ import annotations

import os
import time

# World front page: override with JARVIS_WORLD_NEWS_URL (e.g. Reuters, BBC, AP).
_DEFAULT_WORLD_NEWS = "https://www.bbc.com/news/world"
LIVEUAMAP_URL = "https://liveuamap.com/"


def _world_news_url() -> str:
    return (os.environ.get("JARVIS_WORLD_NEWS_URL") or _DEFAULT_WORLD_NEWS).strip()


def open_world_news_in_chrome() -> str:
    """Open a world-news homepage in Chrome (new tab). Default is BBC World; set JARVIS_WORLD_NEWS_URL to change. Use when the user wants international headlines in the browser. For spoken headlines first, also call get_headline_news or what_is_going_on, then open tabs."""
    from jarvis_tools import open_url_in_chrome

    return open_url_in_chrome(_world_news_url(), new_tab=True)


def open_liveuamap_in_chrome() -> str:
    """Open LiveUAMap in Chrome: global conflict / world events map (https://liveuamap.com/). Not the user's personal location. Prefer `open_liveuamap_then_world_news` when the user wants both map and a news page."""
    from jarvis_tools import open_url_in_chrome

    return open_url_in_chrome(LIVEUAMAP_URL, new_tab=True)


def open_liveuamap_then_world_news() -> str:
    """**Preferred for “what’s going on / world situation / war / conflict / news” on PC:** open **LiveUAMap (conflict map)** and a **world news** page. By default this uses **two separate Chrome windows, left and right half of the screen** (see JARVIS_CHROME_TILE), not two tabs in one window. The map is global events at liveuamap.com, not the user’s personal GPS. Or use `open_global_situation_briefing` in `jarvis_tools` to open and fetch a digest in one call."""
    from jarvis_tools import open_two_urls_chrome_tiled, open_url_in_chrome

    u2 = _world_news_url()
    tile = os.environ.get("JARVIS_CHROME_TILE", "1").lower() in (
        "1",
        "true",
        "yes",
    )
    if tile:
        return open_two_urls_chrome_tiled(
            LIVEUAMAP_URL,
            u2,
            left_title_hint="conflict map (LiveUAMap)",
            right_title_hint="world news",
        )
    r1 = open_url_in_chrome(LIVEUAMAP_URL, new_tab=True)
    time.sleep(0.4)
    r2 = open_url_in_chrome(u2, new_tab=True)
    return (
        f"Opened in one window: 1) LiveUAMap: {r1}  2) world news: {r2} (JARVIS_CHROME_TILE=1 for split-screen) "
    )


def open_world_news_then_liveuamap() -> str:
    """Legacy name: same as `open_liveuamap_then_world_news` (map first, then world news). Kept for compatibility."""
    return open_liveuamap_then_world_news()

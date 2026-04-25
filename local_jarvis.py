"""
Local voice assistant: microphone -> STT -> LLM -> Edge TTS -> speakers.

STT (JARVIS_STT):
  - gemini   — Google Gemini transcribes audio (fast on network; needs GOOGLE_API_KEY). Best "instant" feel without local GPU.
  - vosk     — Local, very fast CPU; download a model and set JARVIS_VOSK_MODEL.
  - whisper  — Local; needs: uv sync --extra whisper  (heavy torch install).

LLM (JARVIS_LLM):
  - gemini   — Needs GOOGLE_API_KEY; use JARVIS_GEMINI_MODEL (default: gemini-2.0-flash).
  - ollama   — Local; needs ollama serve + pulled model.

TTS: Edge (Microsoft). **Spoken replies are English by default** (JARVIS_TTS_LANG=en). Optional
  `JARVIS_EDGE_VOICE_AR` if the model ever returns Arabic script. **Bundled ADB**: run
  `uv run python scripts/fetch_platform_tools.py` then `platform-tools/adb.exe` is used automatically.
  Optional: JARVIS_WORLD_NEWS_URL, JARVIS_ENABLE_PHONE_TOOLS=0.
  Prosody: JARVIS_EDGE_RATE / JARVIS_EDGE_PITCH / JARVIS_EDGE_VOLUME.
  **Context**: JARVIS_MAX_TURNS (default 12) trims chat; **Session memory** (in RAM) holds short tool/ADB notes
  for this run (JARVIS_SESSION_MAX_NOTES). Say **reset session** to clear. Destroyed on process exit.
  **Wake on clap** (JARVIS_WAKE_ON_CLAP=1): wait for clap (optionally JARVIS_CLAP_STRIKES=2 double-clap), then
  normal listening. **JARVIS_CLAP_STICKY_SESSION=1** (default): after waking, keep listening for more commands
  until you say to sleep; then clap to wake again. Set JARVIS_CLAP_STICKY_SESSION=0 to require clap before every
  turn. Tuning: JARVIS_CLAP_RMS, JARVIS_CLAP_PEAK, JARVIS_CLAP_BEEP=1 (Windows beep on wake). Say **go to sleep**
  (or similar) when in sticky clap mode to rest until the next wake. JARVIS_CLAP_REWAKE_EN: short line on each
  re-wake after the first; JARVIS_WELCOME_EN is spoken the first time you clap in sticky mode.

Welcome: JARVIS_WELCOME_EN. Replies: English TTS; multilingual STT (e.g. Hindi in Devanagari) should be answered in English
  without repeated "English only" preambles (see default JARVIS_SYSTEM).
**Banner:** On startup the terminal prints an ASCII JARVIS logo (figlet-style). JARVIS_SHOW_BANNER=0 to hide;
JARVIS_ASCII_STYLE=standard for block “JARVIS” letters, or slant (default) for the lean logo.
**Clap:** Impulses are short: detection uses **peak** as well as RMS (JARVIS_CLAP_PEAK_ATTACK). Defaults are fairly
sensitive; raise JARVIS_CLAP_RMS / JARVIS_CLAP_PEAK if you get false triggers. JARVIS_CLAP_DEBUG=1 logs levels.
JARVIS_BANNER_COLOR=0 disables ANSI; NO_COLOR=1 also disables. Orange/gold (Jarvis) uses JARVIS_BANNER_THEME=jarvis|orange.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import os
import re
import sys
import tempfile
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import edge_tts
import numpy as np
import ollama
import pygame
import sounddevice as sd

# Optional env file
_ROOT = Path(__file__).resolve().parent
for _p in (_ROOT / ".env.local", _ROOT / ".env"):
    if _p.is_file():
        for line in _p.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v

logger = logging.getLogger("local_jarvis")

# Terminal banner (figlet): `slant` matches the classic lean / “_____” look; `standard` spells J A R V I S in block letters.
# Set JARVIS_SHOW_BANNER=0 to skip. JARVIS_ASCII_STYLE=standard|slant (default slant).
_JARVIS_ASCII_SLANT = (
    "       _                  _     \n"
    "      (_)___ _______   __(_)____\n"
    "     / / __ `/ ___/ | / / / ___/\n"
    "    / / /_/ / /   | |/ / (__  ) \n"
    " __/ /\\__,_/_/    |___/_/____/  \n"
    "/___/                            \n"
)
# figlet -f standard JARVIS
_JARVIS_ASCII_STANDARD = (
    "     _   _    ______     _____ ____  \n"
    "    | | / \\  |  _ \\ \\   / /_ _/ ___| \n"
    " _  | |/ _ \\ | |_) \\ \\ / / | |\\___ \\ \n"
    "| |_| / ___ \\|  _ < \\ V /  | | ___) |\n"
    " \\___/_/   \\_\\_| \\_\\ \\_/  |___|____/ \n"
)

# --- ANSI: Windows 10+ console needs VT mode for colors (Windows Terminal: usually on). ---
# Orange/gold: 38;5;208 / 38;5;214; Cyan "HUD" accent: 38;5;80 or 36
_CSI = "\033["
_A_RESET = f"{_CSI}0m"
# Primary logo color (default: Jarvis-amber)
_ORANGE_256 = f"{_CSI}38;5;208m"
_GOLD_256 = f"{_CSI}38;5;220m"
_CYAN_256 = f"{_CSI}38;5;80m"
_DIM = f"{_CSI}2m"


def _enable_windows_vt() -> None:
    if sys.platform != "win32" or not sys.stdout.isatty():
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        h = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(h, ctypes.byref(mode)):
            return
        ENABLE_VT = 0x0004
        if (mode.value & ENABLE_VT) == 0:
            kernel32.SetConsoleMode(h, mode.value | ENABLE_VT)
    except (AttributeError, OSError):
        pass


def _banner_ansi() -> tuple[str, str, str, str]:
    """Return (art_open, art_close, tag_open, tag_close) for banner lines, or all empty to disable color."""
    if not sys.stdout.isatty():
        return ("", "", "", "")
    if os.environ.get("NO_COLOR", ""):
        return ("", "", "", "")
    if os.environ.get("JARVIS_BANNER_COLOR", "1").lower() in ("0", "false", "no"):
        return ("", "", "", "")
    theme = os.environ.get("JARVIS_BANNER_THEME", "jarvis").lower()
    if theme in ("0", "none", "off"):
        return ("", "", "", "")
    if theme in ("jarvis", "amber", "gold", "default"):
        a1, a2, a3 = _GOLD_256, _GOLD_256, _DIM + _CYAN_256
    elif theme in ("orange", "fire"):
        a1, a2, a3 = _ORANGE_256, _GOLD_256, _DIM + _ORANGE_256
    else:
        a1, a2, a3 = _ORANGE_256, _GOLD_256, _DIM + _CYAN_256
    return (a1, _A_RESET, a3, _A_RESET)


def _print_jarvis_banner() -> None:
    if os.environ.get("JARVIS_SHOW_BANNER", "1").lower() in (
        "0",
        "false",
        "no",
    ):
        return
    _enable_windows_vt()
    style = os.environ.get("JARVIS_ASCII_STYLE", "slant").lower()
    art = (
        _JARVIS_ASCII_STANDARD
        if style in ("standard", "block", "caps", "upper")
        else _JARVIS_ASCII_SLANT
    )
    oa, ra, ob, rb = _banner_ansi()
    print(f"{oa}{art}{ra}", end="", flush=True)
    line = "  | local voice assistant | mic -> STT -> LLM -> Edge TTS"
    if ob:
        line = f"{ob}{line}{rb}"
    print(line + "\n", flush=True)


# --- Audio / VAD ---
SAMPLE_RATE = 16_000
CHUNK_S = 0.05
MAX_RECORD_S = 30.0
SILENCE_TO_END_S = 0.45
MIN_SPEECH_START_S = 0.2
SPEECH_RMS_FLOOR = float(os.environ.get("JARVIS_SPEECH_RMS", "0.012"))
# User+assistant pairs kept in chat (each "turn" is one pair). Ephemeral session notes are separate.
MAX_HISTORY_TURNS = int(os.environ.get("JARVIS_MAX_TURNS", "12"))

# --- Optional wake: clap before normal VAD (see wait_for_clap) ---
# JARVIS_WAKE_ON_CLAP=1, JARVIS_CLAP_RMS, JARVIS_CLAP_PEAK, JARVIS_CLAP_STRIKES (1 or 2)
CLAP_CHUNK_S = float(os.environ.get("JARVIS_CLAP_CHUNK_S", "0.02"))  # 20 ms per analysis frame
CLAP_HANG_S = float(
    os.environ.get("JARVIS_CLAP_HANG_S", "0.18")
)  # ignore ring-down; override via env


def _wake_on_clap_enabled() -> bool:
    return os.environ.get("JARVIS_WAKE_ON_CLAP", "0").lower() in (
        "1",
        "true",
        "yes",
    )


def _clap_sticky_enabled() -> bool:
    """If true with wake on clap: clap (or double-clap) once, then no more clap until user says to sleep."""
    if not _wake_on_clap_enabled():
        return False
    v = os.environ.get("JARVIS_CLAP_STICKY_SESSION", "1").lower()
    return v in ("1", "true", "yes")


def _user_wants_sleep(text_lower: str) -> bool:
    """Local phrase gate so Jarvis can rest (sticky clap mode) without calling the LLM."""
    tl = " ".join(text_lower.split())
    if not tl:
        return False
    if any(n in tl for n in ("don't go to sleep", "do not go to sleep", "dont go to sleep")):
        return False
    if any(
        p in tl
        for p in (
            "go to sleep",
            "time to sleep",
            "you can sleep",
            "put yourself to sleep",
            "sleep now",
            "rest now",
        )
    ):
        return True
    if re.search(
        r"\b(good night|goodnight)\s+jarvis\b", tl, re.IGNORECASE
    ) or re.search(r"\bjarvis,?\s+(good night|goodnight)\b", tl, re.IGNORECASE):
        return True
    return any(p in tl for p in ("jarvis go to sleep", "maya go to sleep", "go to bed jarvis", "go to bed maya"))


def _close_chrome_on_sleep() -> bool:
    return os.environ.get("JARVIS_CLOSE_CHROME_ON_SLEEP", "1").lower() in (
        "1",
        "true",
        "yes",
    )


def _user_wants_close_chrome(text_lower: str) -> bool:
    """Voice hook: user asked to quit Google Chrome (all windows). Not Microsoft Edge."""
    tl = " ".join(text_lower.split())
    if not tl:
        return False
    if any(
        x in tl
        for x in (
            "don't close chrome",
            "do not close chrome",
            "dont close chrome",
            "keep chrome open",
        )
    ):
        return False
    if "edge" in tl and "close" in tl and "chrome" not in tl:
        return False
    if re.search(
        r"\b(close|shut|kill|quit|exit|turn off|dismiss|end|stop).{0,32}\b(chrome|google chrome|chrome browser|chrome tabs?)\b",
        tl,
    ):
        return True
    if re.search(
        r"\b(chrome|google chrome|chrome browser|chrome tab).{0,40}\b(close|shut|kill|quit|end|dismiss|off|down|away)\b",
        tl,
    ):
        return True
    if "close" in tl and "browser" in tl and "edge" not in tl:
        return True
    return False


def _clap_resume_phrase() -> str:
    n = int(os.environ.get("JARVIS_CLAP_STRIKES", "1"))
    return "Double-clap when you need me." if n >= 2 else "Clap when you need me."


def _clack_ack_beep() -> None:
    if os.environ.get("JARVIS_CLAP_BEEP", "0").lower() not in ("1", "true", "yes"):
        return
    if os.name == "nt":
        try:
            import winsound

            winsound.Beep(900, 60)
        except (ImportError, RuntimeError, OSError):
            pass

# --- STT / LLM selection (see module docstring) ---
_HAS_GEMINI_KEY = bool(os.environ.get("GOOGLE_API_KEY"))
STT_MODE = os.environ.get(
    "JARVIS_STT",
    "gemini" if _HAS_GEMINI_KEY else "vosk",
)
LLM_MODE = os.environ.get(
    "JARVIS_LLM",
    "gemini" if _HAS_GEMINI_KEY else "ollama",
)

WHISPER_SIZE = os.environ.get("JARVIS_WHISPER", "tiny")
OLLAMA_MODEL = os.environ.get("JARVIS_OLLAMA_MODEL", "llama3.2:3b")
GEMINI_MODEL = os.environ.get("JARVIS_GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_STT_MODEL = os.environ.get("JARVIS_GEMINI_STT_MODEL", GEMINI_MODEL)

EDGE_VOICE = os.environ.get("JARVIS_EDGE_VOICE", "en-GB-RyanNeural")
# Optional: only if JARVIS_TTS_LANG=ar or auto with Arabic in the reply text
EDGE_VOICE_AR = os.environ.get("JARVIS_EDGE_VOICE_AR", "fa-IR-FaridNeural")
# en (default): spoken output always uses JARVIS_EDGE_VOICE. "ar"/"auto" see pick_edge_voice.
JARVIS_TTS_LANG = os.environ.get("JARVIS_TTS_LANG", "en").lower()

# Edge TTS prosody (SSML): faster rate + slightly higher pitch = snappier “house AI / Jarvis” read.
# Formats: rate/volume "±N%", pitch "±NHz" (see edge-tts TTSConfig validation).
JARVIS_EDGE_RATE = os.environ.get("JARVIS_EDGE_RATE", "+20%")
JARVIS_EDGE_PITCH = os.environ.get("JARVIS_EDGE_PITCH", "+6Hz")
JARVIS_EDGE_VOLUME = os.environ.get("JARVIS_EDGE_VOLUME", "+0%")

# Arabic script: optional alternate Edge voice (only when JARVIS_TTS_LANG allows)
_ARABIC_SCRIPT = re.compile(r"[\u0600-\u06FF\u0750-\u077F]")


def _adb_status_line() -> str:
    """Log which adb binary will be used (bundled in project, PATH, or ADB_PATH)."""
    try:
        from android_adb import adb_executable

        return adb_executable()
    except Exception as e:  # noqa: BLE001
        return f"(adb unavailable: {e})"


# Default: Jarvis-like house AI. Override with JARVIS_SYSTEM in .env
_SYSTEM_JARVIS_DEFAULT = (
    "You are the advanced house AI 'Jarvis' in the spirit of the Iron Man films: precise, unflappable, "
    "loyal to the user, with light dry humor when it fits. Address the user as 'sir' or 'ma'am' when natural. "
    "Keep every reply short and speakable (this is a voice interface).\n\n"
    "**Language:** User speech is often transcribed in **Devanagari** (Hindi), Roman Hinglish, or English. "
    "This is expected. **Infer intent** and answer in **spoken English** (TTS) only—no Devanagari in *your* text. "
    "**Never** start with boilerplate about 'I only speak English', 'on this interface', or 'I cannot use Hindi'—it annoys the user. "
    "Do not apologize for language mismatch; do not claim you 'cannot understand'—you understand; you reply in English. "
    "Only discuss languages if the user *explicitly* asks how multilingual input works (then one short sentence, no lecture).\n\n"
    "For **action requests** (YouTube, WhatsApp, phone, news, map, Chrome, etc.): call the **right tool in the same turn** "
    "and say one short line in English (e.g. opening it now, sir). **Run the tool**; do not only promise to.\n\n"
    "**No capability tours:** Do not list what you can do, do not add 'I can also help with…' or 'For example I can open…' "
    "or 'Is there anything else?' with a feature menu. The user already knows. Answer the question, run the tool, or ask "
    "one concrete follow-up if you truly need it.\n\n"
    "Follow tool instructions for web, phone, and maps. No markdown, no lists, no emojis. "
    "If something is genuinely unclear, one short clarifying question is fine."
)
# Appended to system when Gemini tools (JARVIS_ENABLE_TOOLS) are on
# Phone paragraph omitted when JARVIS_ENABLE_PHONE_TOOLS=0 (must match which tools are registered)
_SYSTEM_TOOLS_ADDON_CORE = (
    "\n\n**Tools (you must use them when relevant — in any language or script, including Devanagari transcripts):** "
    "You can invoke functions to act for the user. "
    "For **'what is going on', 'what is happening', world news, war, conflict, situational / war briefings** (any language, including Hindi script): "
    "call **`open_global_situation_briefing` first** — it opens the **conflict map (LiveUAMap)** and a world news page in **two Chrome windows, left and right half of the screen** (map left, news right) so the user can see both at once, then returns data to read aloud. "
    "If you only need the text without opening tabs, use `what_is_going_on` and/or `get_headline_news`, but for the user’s “show me the world / conflict” intent you must still open the map. "
    "Alternatively, call `open_liveuamap_then_world_news` and then `what_is_going_on` in the same turn. "
    "Do **not** open a random country on Google `open_global_map` unless the user **names** a place; the global situation map is LiveUAMap, not a Google Maps world search. "
    "For **where am I / find me / my location / current location**, call `get_approximate_location` (network-level, not GPS); "
    "for *their* map, `open_map_at_my_location` or `open_global_map` with a **named** place. "
    "For any other site, `open_url_in_chrome`. "
    "To **fully quit Google Chrome** (all windows, all tabs) when the user says to close Chrome or the browser, call `close_all_google_chrome` immediately — the same as the local voice command. "
    "To move between Chrome tabs: `focus_google_chrome` then `chrome_tab_right` (next) or `chrome_tab_left` (previous). "
)
_SYSTEM_TOOLS_ADDON_PHONE = (
    "**Android phone (USB debugging / ADB on this PC)**: the user is still heard on the **PC mic**; these tools only **control the phone** via adb. "
    "Use `phone_check_adb_and_devices` if connection is uncertain; read the **Summary** line (unauthorized / no device / ready). "
    "For wireless adb over Wi-Fi: `phone_enable_wireless_adb` after USB is in; if the phone IP is not auto-detected, the user may set **JARVIS_ADB_WIFI_IP** (phone LAN IP) in the environment. "
    "When a tool result includes **NEXT:** about YouTube, ask that one question in speech; on agreement, run `phone_youtube_search_and_open` with the query given in the tool text. "
    "For the phone: `phone_open_whatsapp`, `phone_open_youtube`, `phone_youtube_search_and_open`. "
    "If WhatsApp fails, use the tool return text (SUCCESS or FAILED) and suggest replug USB or set JARVIS_WHATSAPP_PACKAGE for WhatsApp Business. "
)
_SYSTEM_TOOLS_ADDON_FOOT = (
    "If the user only asks a vague 'what can you do' / capabilities question, reply in one short line: offer to help with "
    "their next *specific* request—**do not** enumerate features, tools, or give examples like a product tour. "
    "You may call several tools in one turn, then one concise answer in English. "
    "**Session memory** (in the system block) lists recent ADB/phone tool lines: trust it; do not claim 'no device' if the last note shows a connected device, unless a fresh `phone_check` says otherwise."
)


def _active_system_tools_addon() -> str:
    s = _SYSTEM_TOOLS_ADDON_CORE
    if os.environ.get("JARVIS_ENABLE_PHONE_TOOLS", "1").lower() in ("1", "true", "yes"):
        s += _SYSTEM_TOOLS_ADDON_PHONE
    return s + _SYSTEM_TOOLS_ADDON_FOOT
SYSTEM_JARVIS = os.environ.get("JARVIS_SYSTEM", _SYSTEM_JARVIS_DEFAULT)
# 1 = register Jarvis tool functions (Gemini AFC only; requires gemini LLM)
JARVIS_ENABLE_TOOLS = os.environ.get("JARVIS_ENABLE_TOOLS", "1").lower() in (
    "1",
    "true",
    "yes",
)
# English-only startup line; spoken before any user turn. Override: JARVIS_WELCOME_EN
WELCOME_EN = os.environ.get(
    "JARVIS_WELCOME_EN",
    "Good evening, sir. House systems are online and all subsystems are nominal. "
    "I am at your service—how may I assist you today?",
)
# After the first clap-wake, shorter line when re-waking from sleep (sticky clap). Override: JARVIS_CLAP_REWAKE_EN
CLAP_REWAKE_EN = os.environ.get("JARVIS_CLAP_REWAKE_EN", "At your service, sir.")

# Default Vosk English model path (user downloads and unzips here)
_DEFAULT_VOSK = _ROOT / "models" / "vosk-model-small-en-us-0.15"
JARVIS_VOSK_MODEL = os.environ.get("JARVIS_VOSK_MODEL", str(_DEFAULT_VOSK))


@dataclass
class ChatState:
    messages: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.clear()

    def clear(self) -> None:
        self.messages = [{"role": "system", "content": SYSTEM_JARVIS}]

    def _trim(self) -> None:
        if not self.messages or self.messages[0].get("role") != "system":
            return
        sys_msg = [self.messages[0]]
        rest = self.messages[1:]
        cap = MAX_HISTORY_TURNS * 2
        if len(rest) > cap:
            rest = rest[-cap:]
        self.messages = sys_msg + rest

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})
        self._trim()

    def add_assistant(self, text: str) -> None:
        self.messages.append({"role": "assistant", "content": text})
        self._trim()

    def openai_style_messages(self) -> list[dict]:
        return list(self.messages)


def _frame_rms(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(x))))


def wait_for_clap(device: int | None = None) -> None:
    """
    Block until a hand clap (or similar sharp transient) is heard. Tuned to reduce
    false triggers from normal speech. Double-clap optional (JARVIS_CLAP_STRIKES=2).

    Claps are very short: a 20 ms block often has **low RMS** (most samples are
    near silence) even when the **sample peak** is high. The detector uses peak
    delta as well as RMS delta, and slightly wider double-clap timing (see env).
    """
    rms_t = float(os.environ.get("JARVIS_CLAP_RMS", "0.055"))
    peak_t = float(os.environ.get("JARVIS_CLAP_PEAK", "0.12"))
    attack_t = float(os.environ.get("JARVIS_CLAP_ATTACK", "0.018"))
    peak_attack_t = float(os.environ.get("JARVIS_CLAP_PEAK_ATTACK", "0.075"))
    strikes_mode = int(os.environ.get("JARVIS_CLAP_STRIKES", "1"))
    strikes_mode = 2 if strikes_mode >= 2 else 1
    t_min = float(os.environ.get("JARVIS_CLAP_DOUBLE_MIN_S", "0.1"))
    t_max = float(os.environ.get("JARVIS_CLAP_DOUBLE_MAX_S", "1.3"))
    hang_s = CLAP_HANG_S
    clap_debug = os.environ.get("JARVIS_CLAP_DEBUG", "0").lower() in ("1", "true", "yes")
    last_log_t = 0.0
    hint = "" if clap_debug else "Set JARVIS_CLAP_DEBUG=1 to log levels if claps are ignored."

    chunk = max(int(CLAP_CHUNK_S * SAMPLE_RATE), 128)
    prev_rms = 0.0
    prev_peak = 0.0
    t_last_clap: float | None = None
    hang_samples = 0.0
    t_stream = 0.0
    dt = float(chunk) / float(SAMPLE_RATE)
    with sd.InputStream(
        device=device,
        channels=1,
        samplerate=SAMPLE_RATE,
        dtype="float32",
        blocksize=chunk,
    ) as stream:
        logger.info(
            "Wake on clap: clap to speak, sir. (input device: %s) %s",
            device if device is not None else "default",
            hint,
        )
        while True:
            data, _ = stream.read(chunk)
            mono = data[:, 0].copy() if data.ndim > 1 else data.flatten()
            rms = _frame_rms(mono)
            peak = float(np.max(np.abs(mono)))
            t_stream += dt
            if hang_samples > 0:
                hang_samples = max(0.0, hang_samples - dt)
                prev_rms = rms
                prev_peak = peak
                continue
            d_r = rms - prev_rms
            d_p = peak - prev_peak
            narrow = (d_p >= peak_attack_t) and (peak >= peak_t)
            wide = (d_r >= attack_t) and (rms >= rms_t) and (peak >= peak_t * 0.55)
            is_clap = narrow or wide
            if clap_debug and (t_stream - last_log_t) >= 0.45:
                last_log_t = t_stream
                logger.info(
                    "clap cal: rms=%.4f peak=%.3f d_rms=%.4f d_peak=%.3f th rms/peak=%.3f/%.3f | narrow=%s wide=%s",
                    rms,
                    peak,
                    d_r,
                    d_p,
                    rms_t,
                    peak_t,
                    narrow,
                    wide,
                )
            if not is_clap:
                if (
                    strikes_mode == 2
                    and t_last_clap is not None
                    and (t_stream - t_last_clap) > t_max
                ):
                    t_last_clap = None
                prev_rms = rms
                prev_peak = peak
                continue
            if strikes_mode == 1:
                logger.info("Clap wake: detected.")
                _clack_ack_beep()
                return
            if t_last_clap is None:
                t_last_clap = t_stream
                hang_samples = hang_s
            else:
                gap = t_stream - t_last_clap
                if gap < t_min:
                    prev_rms = rms
                    prev_peak = peak
                    continue
                if gap <= t_max:
                    logger.info("Clap wake: double clap detected.")
                    _clack_ack_beep()
                    return
                t_last_clap = t_stream
                hang_samples = hang_s
            prev_rms = rms
            prev_peak = peak


def record_utterance(device: int | None = None) -> np.ndarray:
    chunk = int(CHUNK_S * SAMPLE_RATE)
    max_chunks = int(MAX_RECORD_S / CHUNK_S)
    buf: list[np.ndarray] = []
    in_speech = False
    speech_chunks = 0
    silence_after = 0
    rms_ema = 0.0
    alpha = 0.3

    with sd.InputStream(
        device=device,
        channels=1,
        samplerate=SAMPLE_RATE,
        dtype="float32",
        blocksize=chunk,
    ) as stream:
        for _ in range(max_chunks):
            data, _ = stream.read(chunk)
            mono = data[:, 0].copy() if data.ndim > 1 else data.flatten()
            rms = _frame_rms(mono)
            rms_ema = alpha * rms + (1 - alpha) * rms_ema
            is_voice = rms_ema > SPEECH_RMS_FLOOR

            if is_voice:
                in_speech = True
                speech_chunks += 1
                silence_after = 0
            elif in_speech:
                silence_after += 1
                s_sil = silence_after * CHUNK_S
                if s_sil >= SILENCE_TO_END_S and speech_chunks * CHUNK_S >= MIN_SPEECH_START_S:
                    break
                if s_sil > 2.0 and speech_chunks == 0:
                    in_speech = False
                    silence_after = 0
            else:
                speech_chunks = 0

            if in_speech or buf:
                buf.append(mono)
        if not buf:
            return np.array([], dtype=np.float32)
        return np.concatenate(buf, axis=0).astype(np.float32)


def float32_to_wav_bytes(audio: np.ndarray, sample_rate: int) -> bytes:
    a = np.clip(audio, -1.0, 1.0)
    pcm = (a * 32767.0).astype(np.int16)
    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return bio.getvalue()


def play_mp3_path(path: Path) -> None:
    pygame.mixer.music.load(str(path))
    pygame.mixer.music.play()
    while pygame.mixer.music.get_busy():
        pygame.time.Clock().tick(10)


def pick_edge_voice(text: str) -> str:
    """Default: English house voice. Optional Arabic TTS if configured and the reply is Arabic script."""
    if JARVIS_TTS_LANG in ("ar", "auto") and _ARABIC_SCRIPT.search(text):
        return EDGE_VOICE_AR
    return EDGE_VOICE


def _edge_tts_voices_to_try(text: str) -> list[str]:
    """Try primary voice, then fallbacks (English) so a bad line does not crash the loop."""
    first = pick_edge_voice(text)
    pool = [first, EDGE_VOICE, EDGE_VOICE_AR]
    return list(dict.fromkeys([v for v in pool if v]))


def _edge_tts(
    text: str,
    voice: str,
) -> edge_tts.Communicate:
    """Faster, slightly higher-pitched delivery (configurable via JARVIS_EDGE_*)."""
    return edge_tts.Communicate(
        text,
        voice,
        rate=JARVIS_EDGE_RATE,
        volume=JARVIS_EDGE_VOLUME,
        pitch=JARVIS_EDGE_PITCH,
    )


class Jarvis:
    def __init__(self, input_device: int | None) -> None:
        self._input_device = input_device
        self.chat = ChatState()
        from session_memory import SessionMemory

        self._session = SessionMemory()
        self.stt_mode = STT_MODE
        self.llm_mode = LLM_MODE

        self._whisper: Any = None
        self._vosk_model: Any = None
        self._genai_client: Any = None

    def _build_effective_system(self) -> str:
        s = SYSTEM_JARVIS
        if JARVIS_ENABLE_TOOLS and self.llm_mode == "gemini":
            if "you must use them when relevant" not in s:
                s += _active_system_tools_addon()
        s += self._session.instruction_suffix()
        return s

    def _sync_system_message(self) -> None:
        if not self.chat.messages or self.chat.messages[0].get("role") != "system":
            return
        self.chat.messages[0]["content"] = self._build_effective_system()

    def _get_genai(self) -> Any:
        if self._genai_client is None:
            from google import genai

            key = os.environ.get("GOOGLE_API_KEY")
            if not key:
                raise RuntimeError("GOOGLE_API_KEY is required for Gemini STT/LLM.")
            self._genai_client = genai.Client(api_key=key)
        return self._genai_client

    def load_stt(self) -> None:
        if self.stt_mode == "whisper":
            try:
                import whisper
            except ImportError as e:
                raise RuntimeError(
                    "Whisper STT needs torch. Run: uv sync --extra whisper"
                ) from e
            device = os.environ.get("JARVIS_WHISPER_DEVICE", "cpu")
            logger.info(
                "Loading openai-whisper %r on %s (first run downloads weights)…",
                WHISPER_SIZE,
                device,
            )
            self._whisper = whisper.load_model(WHISPER_SIZE, device=device)
            logger.info("Whisper ready.")
        elif self.stt_mode == "vosk":
            from vosk import Model

            path = Path(JARVIS_VOSK_MODEL)
            if not path.is_dir():
                raise RuntimeError(
                    f"Vosk model not found at {path}. "
                    "Download e.g. vosk-model-small-en-us-0.15, unzip into ./models/, "
                    "or set JARVIS_VOSK_MODEL. Or set JARVIS_STT=gemini with GOOGLE_API_KEY."
                )
            logger.info("Loading Vosk model from %s …", path)
            self._vosk_model = Model(str(path))
            logger.info("Vosk ready (fast local STT).")
        elif self.stt_mode == "gemini":
            self._get_genai()
            logger.info("Gemini STT will use model %s (no local STT load).", GEMINI_STT_MODEL)
        else:
            raise ValueError(
                f"Unknown JARVIS_STT={self.stt_mode!r}. Use: gemini, vosk, whisper"
            )

    def transcribe(self, audio: np.ndarray) -> str:
        if audio.size < SAMPLE_RATE * 0.2:
            return ""
        if self.stt_mode == "whisper":
            return self._transcribe_whisper(audio)
        if self.stt_mode == "vosk":
            return self._transcribe_vosk(audio)
        if self.stt_mode == "gemini":
            return self._transcribe_gemini(audio)
        return ""

    def _transcribe_whisper(self, audio: np.ndarray) -> str:
        if self._whisper is None:
            return ""
        use_cuda = os.environ.get("JARVIS_WHISPER_DEVICE", "cpu") == "cuda"
        lang = os.environ.get("JARVIS_LANG")
        r = self._whisper.transcribe(
            audio,
            language=lang if lang else None,
            fp16=use_cuda,
            without_timestamps=True,
        )
        return (r.get("text") or "").strip()

    def _transcribe_vosk(self, audio: np.ndarray) -> str:
        if self._vosk_model is None:
            return ""
        from vosk import KaldiRecognizer

        rec = KaldiRecognizer(self._vosk_model, SAMPLE_RATE)
        pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
        chunk = 8000
        for i in range(0, len(pcm), chunk):
            rec.AcceptWaveform(pcm[i : i + chunk])
        data = json.loads(rec.FinalResult())
        return (data.get("text") or "").strip()

    def _transcribe_gemini(self, audio: np.ndarray) -> str:
        from google.genai import types

        client = self._get_genai()
        wav = float32_to_wav_bytes(audio, SAMPLE_RATE)
        prompt = (
            "Transcribe the speech in this audio. Output only the spoken words, "
            "same language as the speaker, no labels or punctuation hints."
        )
        response = client.models.generate_content(
            model=GEMINI_STT_MODEL,
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_bytes(data=wav, mime_type="audio/wav"),
                        types.Part.from_text(text=prompt),
                    ],
                )
            ],
        )
        return (response.text or "").strip()

    def think(self, user_text: str) -> str:
        from session_memory import set_active_session

        set_active_session(self._session)
        self.chat.add_user(user_text)
        self._sync_system_message()
        logger.info("User: %s", user_text)
        t0 = time.perf_counter()
        if self.llm_mode == "gemini":
            out = self._think_gemini()
        else:
            out = self._think_ollama()
        t1 = time.perf_counter()
        logger.info("Assistant (%d ms): %s", int((t1 - t0) * 1000), out[:200])
        if out:
            self.chat.add_assistant(out)
        return out

    def _think_gemini(self) -> str:
        from google.genai import types

        def _pop_last_user() -> None:
            if self.chat.messages and self.chat.messages[-1].get("role") == "user":
                self.chat.messages.pop()

        try:
            client = self._get_genai()
        except RuntimeError as e:
            _pop_last_user()
            return str(e)

        system_inst: str | None = None
        parts_contents: list[Any] = []
        for m in self.chat.messages:
            if m["role"] == "system":
                system_inst = m["content"]
            elif m["role"] == "user":
                parts_contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=m["content"])],
                    )
                )
            elif m["role"] == "assistant":
                parts_contents.append(
                    types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=m["content"])],
                    )
                )

        tools_arg: list | None = None
        if JARVIS_ENABLE_TOOLS:
            from jarvis_tools import JARVIS_TOOL_FUNCTIONS

            tools_arg = list(JARVIS_TOOL_FUNCTIONS)
        if system_inst is None:
            system_inst = self._build_effective_system()
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=parts_contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_inst,
                    temperature=0.55,
                    max_output_tokens=1500,
                    tools=tools_arg,
                ),
            )
        except Exception as e:
            _pop_last_user()
            logger.error("Gemini generate_content failed: %s", e)
            return "I'm sorry, I could not complete that request."

        out = (response.text or "").strip()
        if not out and response.candidates:
            cand = response.candidates[0]
            if cand.content and cand.content.parts:
                chunks: list[str] = []
                for part in cand.content.parts:
                    if getattr(part, "text", None):
                        chunks.append(part.text)  # type: ignore[union-attr]
                out = " ".join(chunks).strip()
        return out

    def _think_ollama(self) -> str:
        try:
            resp = ollama.chat(
                model=OLLAMA_MODEL,
                messages=self.chat.openai_style_messages(),
            )
        except Exception as e:
            if self.chat.messages and self.chat.messages[-1].get("role") == "user":
                self.chat.messages.pop()
            logger.error(
                "Ollama error: %s — is `ollama serve` running? Try: ollama pull %s",
                e,
                OLLAMA_MODEL,
            )
            return "I'm sorry, the language model is unavailable. Please start Ollama and pull the model."
        return (resp.message.content or "").strip()

    async def speak(self, text: str) -> None:
        if not text:
            return
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            p = Path(f.name)
        try:
            last_err: Exception | None = None
            for voice in _edge_tts_voices_to_try(text):
                try:
                    c = _edge_tts(text, voice)
                    await c.save(str(p))
                    if voice != pick_edge_voice(text):
                        logger.warning("Edge TTS used fallback voice %s (primary mismatch)", voice)
                    play_mp3_path(p)
                    last_err = None
                    break
                except edge_tts.exceptions.NoAudioReceived as e:
                    last_err = e
                    logger.warning("Edge TTS no audio with %s, trying next voice…", voice)
                    continue
            if last_err is not None:
                raise last_err
        finally:
            try:
                p.unlink(missing_ok=True)  # type: ignore[arg-type]
            except OSError:
                pass

    def run(self) -> None:
        if self.llm_mode == "gemini" and not _HAS_GEMINI_KEY:
            logger.error("JARVIS_LLM=gemini requires GOOGLE_API_KEY in the environment.")
            return
        if self.stt_mode == "gemini" and not _HAS_GEMINI_KEY:
            logger.error("JARVIS_STT=gemini requires GOOGLE_API_KEY.")
            return

        self.load_stt()
        if not pygame.mixer.get_init():
            pygame.mixer.init()

        from jarvis_tools import close_all_google_chrome

        logger.info(
            "STT=%s LLM=%s | Gemini model=%s | tools=%s | TTS: voice=%s | rate=%s pitch=%s | JARVIS_TTS_LANG=%s (spoken English) | ADB: %s",
            self.stt_mode,
            self.llm_mode,
            GEMINI_MODEL if self.llm_mode == "gemini" else "(n/a)",
            "on" if (JARVIS_ENABLE_TOOLS and self.llm_mode == "gemini") else "off",
            EDGE_VOICE,
            JARVIS_EDGE_RATE,
            JARVIS_EDGE_PITCH,
            JARVIS_TTS_LANG,
            _adb_status_line(),
        )
        if self.llm_mode == "ollama" and JARVIS_ENABLE_TOOLS:
            logger.info("Note: JARVIS_ENABLE_TOOLS is set but tools only apply to Gemini (JARVIS_LLM=gemini).")
        if JARVIS_ENABLE_TOOLS and self.llm_mode == "gemini":
            try:
                from jarvis_adb_tools import phone_tools_enabled

                logger.info(
                    "Phone ADB tools: %s (set JARVIS_ENABLE_PHONE_TOOLS=0 to disable).",
                    "on" if phone_tools_enabled() else "off",
                )
            except ImportError:
                pass

        wake_clap = _wake_on_clap_enabled()
        clap_sticky = _clap_sticky_enabled()
        # Sticky clap: start "asleep" until clap; non-sticky or no clap: same as before (greet at start).
        defer_welcome = wake_clap and clap_sticky
        awake = not (wake_clap and clap_sticky)

        if not defer_welcome:
            try:
                asyncio.run(self.speak(WELCOME_EN))
            except Exception as e:
                logger.error("Greeting TTS failed: %s. Check Edge TTS / network / pygame.", e)
                return
        else:
            n = int(os.environ.get("JARVIS_CLAP_STRIKES", "1"))
            logger.info(
                "Clap wake (sticky): %s to wake; I will keep listening until you say to sleep.",
                "double-clap" if n >= 2 else "clap",
            )

        first_sticky_clap = True
        while True:
            try:
                if wake_clap:
                    if clap_sticky:
                        if not awake:
                            try:
                                wait_for_clap(device=self._input_device)
                            except KeyboardInterrupt:
                                raise
                            awake = True
                            line = WELCOME_EN if first_sticky_clap else CLAP_REWAKE_EN
                            first_sticky_clap = False
                            try:
                                asyncio.run(self.speak(line))
                            except Exception as e:
                                logger.error("Post-wake greeting failed: %s", e)
                    else:
                        try:
                            wait_for_clap(device=self._input_device)
                        except KeyboardInterrupt:
                            raise
                logger.info("Listening…")
                audio = record_utterance(device=self._input_device)
                text = self.transcribe(audio)
                if not text:
                    logger.info("No speech detected, try again.")
                    continue
                tl = text.strip().lower()
                if tl in (
                    "reset session",
                    "new session",
                    "clear session",
                    "clear memory",
                ):
                    self._session.clear()
                    self.chat.clear()
                    asyncio.run(
                        self.speak(
                            "Session cleared, sir. Chat history and scratchpad are empty for this run."
                        )
                    )
                    continue
                if text.lower() in ("quit", "exit", "goodbye", "stop."):
                    asyncio.run(self.speak("Goodbye, sir."))
                    break
                want_sleep = _user_wants_sleep(tl)
                want_close = _user_wants_close_chrome(tl)
                if want_sleep and wake_clap and clap_sticky:
                    cmsg = ""
                    if want_close or _close_chrome_on_sleep():
                        cmsg = close_all_google_chrome()
                        logger.info("Going to sleep (Chrome): %s", cmsg)
                    body = "Going to sleep, sir. " + _clap_resume_phrase()
                    if cmsg and "not running" not in cmsg.lower() and (
                        "ended" in cmsg.lower() or "closed" in cmsg.lower()
                    ):
                        body = "All Chrome windows closed, sir. " + body
                    asyncio.run(self.speak(body))
                    awake = False
                    continue
                if want_close:
                    cmsg = close_all_google_chrome()
                    logger.info("Close Chrome: %s", cmsg)
                    asyncio.run(self.speak("All Chrome windows closed, sir."))
                    continue
                reply = self.think(text)
                asyncio.run(self.speak(reply))
            except KeyboardInterrupt:
                print()
                logger.info("Interrupted.")
                break
            except Exception as e:
                logger.exception("Loop error: %s", e)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("-d", "--input-device", type=int, default=None)
    p.add_argument("--list-devices", action="store_true")
    p.add_argument(
        "--clap",
        action="store_true",
        help="Enable wake on clap for this run (before each command listen cycle).",
    )
    p.add_argument(
        "--no-clap",
        action="store_true",
        help="Disable wake on clap (overrides --clap and JARVIS_WAKE_ON_CLAP).",
    )
    args = p.parse_args()
    if args.list_devices:
        print(sd.query_devices())
        return
    if args.clap and not args.no_clap:
        os.environ["JARVIS_WAKE_ON_CLAP"] = "1"
    elif args.no_clap:
        os.environ["JARVIS_WAKE_ON_CLAP"] = "0"
    _print_jarvis_banner()
    Jarvis(input_device=args.input_device).run()


if __name__ == "__main__":
    main()

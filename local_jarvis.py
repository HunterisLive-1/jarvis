"""
Local voice assistant: microphone -> faster-whisper (STT) -> Ollama (LLM + tools) -> Edge TTS.

**Stack (fully local, low latency):** STT uses `faster-whisper` (JARVIS_FW_*), LLM uses **Ollama** with tool calling
(JARVIS_OLLAMA_MODEL, default `qwen2.5:3b`), TTS is **Edge**. Run `ollama serve` and `ollama pull` your model.

TTS: Edge (Microsoft). **Spoken replies are English by default** (JARVIS_TTS_LANG=en). Optional
  `JARVIS_EDGE_VOICE_AR` if the model ever returns Arabic script. Optional: JARVIS_WORLD_NEWS_URL.
  Prosody: JARVIS_EDGE_RATE / JARVIS_EDGE_PITCH / JARVIS_EDGE_VOLUME.
  **Context**: JARVIS_MAX_TURNS (default 12) trims chat; **Session memory** (in RAM) holds short tool notes
  for this run (JARVIS_SESSION_MAX_NOTES). Say **reset session** to clear. Destroyed on process exit.
  **Wake on clap** (JARVIS_WAKE_ON_CLAP=1): wait for clap (optionally JARVIS_CLAP_STRIKES=2 double-clap), then
  normal listening. **JARVIS_CLAP_STICKY_SESSION=1** (default): after waking, keep listening for more commands
  until you say to sleep; then clap to wake again. Set JARVIS_CLAP_STICKY_SESSION=0 to require clap before every
  turn. Tuning: JARVIS_CLAP_RMS, JARVIS_CLAP_PEAK, JARVIS_CLAP_BEEP=1 (Windows beep on wake). Say **go to sleep**
  (or similar) when in sticky clap mode to rest until the next wake. JARVIS_CLAP_REWAKE_EN: short line on each
  re-wake after the first; JARVIS_WELCOME_EN is spoken the first time you clap in sticky mode.

Welcome: JARVIS_WELCOME_EN. Replies: English TTS; STT is English (base.en) by default.
**Coding lab:** say **set up my lab** / **prepare my coding environment** (and optional path or a name) to
create a project folder, optional `.venv`, and open **VS Code** with **no LLM** (JARVIS_LAB_* in `.env.local`).
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
import inspect
import json
import logging
import os
import re
import sys
import tempfile
import time
import warnings
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import edge_tts
import numpy as np
import ollama
# pygame pulls setuptools/pkg_resources; suppress that deprecation noise on import.
warnings.filterwarnings(
    "ignore",
    message="pkg_resources is deprecated as an API",
    category=UserWarning,
    module="pygame.pkgdata",
)
import pygame
import sounddevice as sd

# Optional env file
_ROOT = Path(__file__).resolve().parent


def _parse_env_file_value(raw: str) -> str:
    """
    Unquote or strip a value from a line like KEY=value.
    Trailing inline comments (unquoted) are removed:  base.en  # options -> base.en
    """
    s = raw.strip()
    if not s:
        return s
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return re.sub(r"\s+#.*$", "", s).strip()


for _p in (_ROOT / ".env.local", _ROOT / ".env"):
    if _p.is_file():
        for line in _p.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                k, v = k.strip(), _parse_env_file_value(v)
                if k and k not in os.environ:
                    os.environ[k] = v

# Hugging Face Hub: Windows often lacks symlink support; avoid a long console warning.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

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
    line = "  | local voice assistant | faster-whisper → Ollama → Edge TTS"
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

# ── Model config ──
FW_MODEL_SIZE = os.environ.get("JARVIS_FW_MODEL", "base.en")  # faster-whisper model
FW_DEVICE = os.environ.get("JARVIS_FW_DEVICE", "cuda")  # "cuda" or "cpu"
FW_COMPUTE = os.environ.get("JARVIS_FW_COMPUTE", "float16")  # "float16" or "int8" (GPU)
# Used when falling back to CPU after a failed CUDA inference (or if JARVIS_FW_DEVICE=cpu)
FW_COMPUTE_CPU = os.environ.get("JARVIS_FW_COMPUTE_CPU", "int8")
OLLAMA_MODEL = os.environ.get("JARVIS_OLLAMA_MODEL", "qwen2.5:3b")
MAX_TOOL_ROUNDS = 5  # agentic loop max iterations

# Default: Thomas = measured UK male, closer to a calm “house AI” (film Jarvis) than Ryan.
EDGE_VOICE = os.environ.get("JARVIS_EDGE_VOICE", "en-GB-ThomasNeural")
# Optional: only if JARVIS_TTS_LANG=ar or auto with Arabic in the reply text
EDGE_VOICE_AR = os.environ.get("JARVIS_EDGE_VOICE_AR", "fa-IR-FaridNeural")
# en (default): spoken output always uses JARVIS_EDGE_VOICE. "ar"/"auto" see pick_edge_voice.
JARVIS_TTS_LANG = os.environ.get("JARVIS_TTS_LANG", "en").lower()

# Edge TTS prosody (SSML): faster rate + slightly higher pitch = snappier “house AI / Jarvis” read.
# Formats: rate/volume "±N%", pitch "±NHz" (see edge-tts TTSConfig validation).
JARVIS_EDGE_RATE = os.environ.get("JARVIS_EDGE_RATE", "+10%")
JARVIS_EDGE_PITCH = os.environ.get("JARVIS_EDGE_PITCH", "+2Hz")
JARVIS_EDGE_VOLUME = os.environ.get("JARVIS_EDGE_VOLUME", "+0%")
# Synthesize the full reply in one Edge request when length ≤ this (avoids long pauses
# between per-sentence HTTP round-trips). Set lower only if a single request fails.
JARVIS_TTS_SINGLE_MAX_CHARS = int(
    os.environ.get("JARVIS_TTS_SINGLE_MAX_CHARS", "4000")
)

# Arabic script: optional alternate Edge voice (only when JARVIS_TTS_LANG allows)
_ARABIC_SCRIPT = re.compile(r"[\u0600-\u06FF\u0750-\u077F]")

# Default: Jarvis-like house AI. Override with JARVIS_SYSTEM in .env
_SYSTEM_JARVIS_DEFAULT = (
    "You are Jarvis, an advanced local AI assistant in the spirit of Iron Man's JARVIS. "
    "You are precise, fast, and loyal. Address the user as 'sir' when natural. "
    "Keep every reply SHORT and speakable — this is a real-time voice interface. "
    "No markdown, no bullet points, no emojis. One or two sentences max unless explaining something complex.\n\n"
    "Language: Always respond in English only.\n\n"
    "Tools: You have tools to control the PC, browser, and search the web. "
    "When the user asks you to DO something (open YouTube, search news, open Chrome, maps, news), "
    "call the right tool immediately in the same response — do NOT just promise to do it. "
    "After calling a tool, say one short confirmation line.\n"
    "For a joke, riddle, or small talk, answer directly; do not call web search for that. "
    "Use search or news tools only when the user wants live web facts, news, or research.\n\n"
    "Never list your capabilities unprompted. Never say 'Is there anything else I can help you with?'. "
    "Just answer and act."
)
# Appended to system: tools and session notes (Ollama + tools always on)
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
_SYSTEM_TOOLS_ADDON_FOOT = (
    "If the user only asks a vague 'what can you do' / capabilities question, reply in one short line: offer to help with "
    "their next *specific* request—**do not** enumerate features, tools, or give examples like a product tour. "
    "You may call several tools in one turn, then one concise answer in English. "
    "**Session memory** (in the system block) lists recent one-line tool summaries when present—use them for consistency."
)


def _active_system_tools_addon() -> str:
    return _SYSTEM_TOOLS_ADDON_CORE + _SYSTEM_TOOLS_ADDON_FOOT
SYSTEM_JARVIS = os.environ.get("JARVIS_SYSTEM", _SYSTEM_JARVIS_DEFAULT)
# English-only startup line; spoken before any user turn. Override: JARVIS_WELCOME_EN
WELCOME_EN = os.environ.get(
    "JARVIS_WELCOME_EN",
    "Good evening, sir. House systems are online and all subsystems are nominal. "
    "I am at your service—how may I assist you today?",
)
# After the first clap-wake, shorter line when re-waking from sleep (sticky clap). Override: JARVIS_CLAP_REWAKE_EN
CLAP_REWAKE_EN = os.environ.get("JARVIS_CLAP_REWAKE_EN", "At your service, sir.")


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


def _text_for_tts(s: str) -> str:
    """
    Turn LLM / markdown-ish text into speech-friendly plain text: no **stars**, no URLs
    (avoids TTS spelling W-W-W or H-T-T-P-S), no link syntax, minimal list noise.
    """
    if not s:
        return s
    t = s.replace("\r\n", "\n")
    # Images: ![alt](u) -> alt
    t = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", t)
    # Links: [label](url) -> label
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    # Headers at line start
    t = re.sub(r"(?m)^\s*#{1,6}\s*", "", t)
    # Fenced code blocks: drop
    t = re.sub(r"```[\s\S]*?```", " ", t)
    t = re.sub(r"`+([^`]+)`+", r"\1", t)
    # Bold / italic (repeat; handles simple nesting)
    for _ in range(4):
        t = re.sub(r"\*\*([^*]+?)\*\*", r"\1", t)
        t = re.sub(r"__([^_]+?)__", r"\1", t)
        t = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"\1", t)
        t = re.sub(r"(?<!_)_([^_]+?)_(?!_)", r"\1", t)
    # List / blockquote line prefixes
    t = re.sub(r"(?m)^\s*>\s?", "", t)
    t = re.sub(r"(?m)^\s*[-*+•]\s+", "", t)
    t = re.sub(r"(?m)^\s*\d+\.\s+", "", t)
    # Remaining URL-like strings (TTS may spell letter-by-letter)
    t = re.sub(r"https?://[^\s)>\]]+", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bwww\.[^\s)>\]]+", " ", t, flags=re.IGNORECASE)
    # Trailing * from truncated markdown
    t = re.sub(r"\*+", " ", t)
    t = t.replace("**", " ")
    # Whitespace
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n+", ". ", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t


async def _speak_edge_clip(clip: str, voice: str) -> None:
    """One Edge TTS request + play (blocking until audio finishes)."""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        p = Path(f.name)
    try:
        communicate = _edge_tts(clip, voice)
        await communicate.save(str(p))
        play_mp3_path(p)
    except edge_tts.exceptions.NoAudioReceived:
        logger.warning("Edge TTS returned no audio for: %s", clip[:80])
    except Exception as e:  # noqa: BLE001
        logger.error("TTS error: %s", e)
    finally:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


def _try_quick_intent(user_text: str) -> str | None:
    """
    Match simple open/close phrases and run Chrome tools without the LLM (~50ms vs round-trip).
    Returns tool result string on success, None to fall back to think().
    """
    from jarvis_tools import close_all_google_chrome, open_url_in_chrome

    tl = " ".join(user_text.lower().split())
    rows: list[tuple[tuple[str, ...], Callable[[], str]]] = [
        (
            ("close chrome", "close browser", "shut chrome", "quit chrome"),
            close_all_google_chrome,
        ),
        (
            (
                "open google chrome",
                "google chrome",
                "open chrome",
                "chrome browser",
                "open browser",
                "launch chrome",
            ),
            lambda: open_url_in_chrome("https://www.google.com"),
        ),
        (
            (
                "open youtube",
                "launch youtube",
                "youtube.com",
                "youtube",
            ),
            lambda: open_url_in_chrome("https://www.youtube.com"),
        ),
        (("spotify",), lambda: open_url_in_chrome("https://open.spotify.com")),
        (("gmail", "open gmail"), lambda: open_url_in_chrome("https://mail.google.com")),
        (
            ("whatsapp", "whats app", "open whatsapp", "whatsap"),
            lambda: open_url_in_chrome("https://web.whatsapp.com"),
        ),
        (("search google", "open google"), lambda: open_url_in_chrome("https://www.google.com")),
        (("google",), lambda: open_url_in_chrome("https://www.google.com")),
    ]
    for keys, fn in rows:
        if any(k in tl for k in keys):
            try:
                return str(fn())
            except Exception as e:  # noqa: BLE001
                logger.debug("Quick intent tool failed, using LLM: %s", e)
                return None
    return None


def _try_coding_lab_intent(user_text: str) -> str | None:
    """
    Match phrases like "setup lab" / "set up my coding environment" and prepare a folder
    and VS Code without calling the LLM.
    """
    from jarvis_coding_lab import run_coding_lab

    return run_coding_lab(user_text)


def _build_ollama_tools() -> list[dict]:
    """
    Auto-generate Ollama/OpenAI JSON schema from JARVIS_TOOL_FUNCTIONS.
    Reads function name, docstring (first line), and type-annotated parameters.
    """
    try:
        from jarvis_tools import JARVIS_TOOL_FUNCTIONS
    except ImportError:
        return []

    _PY_TO_JSON: dict[str, str] = {
        "str": "string",
        "int": "integer",
        "float": "number",
        "bool": "boolean",
    }
    tools: list[dict] = []
    for fn in JARVIS_TOOL_FUNCTIONS:
        if not callable(fn):
            continue
        try:
            sig = inspect.signature(fn)
        except (ValueError, TypeError):
            continue
        doc = (fn.__doc__ or fn.__name__).strip().split("\n")[0][:300]
        props: dict[str, dict] = {}
        required: list[str] = []
        for pname, param in sig.parameters.items():
            ann = param.annotation
            ann_name = getattr(ann, "__name__", str(ann))
            jtype = _PY_TO_JSON.get(ann_name, "string")
            props[pname] = {"type": jtype, "description": pname}
            if param.default is inspect.Parameter.empty:
                required.append(pname)
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": fn.__name__,
                    "description": doc,
                    "parameters": {
                        "type": "object",
                        "properties": props,
                        "required": required,
                    },
                },
            }
        )
    return tools


def _normalize_tool_arguments(raw: Any) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            return json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        return dict(raw)
    except (TypeError, ValueError):
        return {}


def _execute_ollama_tool(name: str, args: Any) -> str:
    """Dispatch a tool call by function name, return string result."""
    args = _normalize_tool_arguments(args)
    try:
        from jarvis_tools import JARVIS_TOOL_FUNCTIONS
    except ImportError:
        return "Error: jarvis_tools not available."
    for fn in JARVIS_TOOL_FUNCTIONS:
        if fn.__name__ == name:
            try:
                result = fn(**args)
                return str(result)
            except Exception as e:  # noqa: BLE001
                logger.error("Tool '%s' raised: %s", name, e)
                return f"Tool error ({name}): {e}"
    return f"Unknown tool: {name}"


class Jarvis:
    def __init__(self, input_device: int | None) -> None:
        self._input_device = input_device
        self.chat = ChatState()
        from session_memory import SessionMemory

        self._session = SessionMemory()
        self._fw_model: Any = None  # faster-whisper model
        self._stt_cpu_fallback_used = False  # True after auto-reload from broken CUDA runtime

    def _build_effective_system(self) -> str:
        s = SYSTEM_JARVIS
        if "you must use them when relevant" not in s:
            s += _active_system_tools_addon()
        s += self._session.instruction_suffix()
        return s

    def _sync_system_message(self) -> None:
        if not self.chat.messages or self.chat.messages[0].get("role") != "system":
            return
        self.chat.messages[0]["content"] = self._build_effective_system()

    def load_stt(self) -> None:
        from faster_whisper import WhisperModel

        logger.info(
            "Loading faster-whisper model '%s' on %s (%s)...",
            FW_MODEL_SIZE,
            FW_DEVICE,
            FW_COMPUTE,
        )
        self._fw_model = WhisperModel(
            FW_MODEL_SIZE,
            device=FW_DEVICE,
            compute_type=FW_COMPUTE,
        )
        logger.info("faster-whisper ready.")

    def _reload_stt_on_cpu(self) -> None:
        """Recover from missing CUDA/cuBLAS DLLs (common on Windows without full CUDA 12 runtime)."""
        from faster_whisper import WhisperModel

        logger.warning(
            "Reloading faster-whisper on CPU (%s). "
            "GPU failed: install CUDA 12.x + cuBLAS on PATH, or set JARVIS_FW_DEVICE=cpu in .env.",
            FW_COMPUTE_CPU,
        )
        self._fw_model = WhisperModel(
            FW_MODEL_SIZE,
            device="cpu",
            compute_type=FW_COMPUTE_CPU,
        )
        self._stt_cpu_fallback_used = True

    def _transcribe_segments(self, audio_f32: np.ndarray):
        return self._fw_model.transcribe(
            audio_f32,
            language="en",  # English only — faster
            beam_size=5,
            vad_filter=True,  # skip silence internally
            without_timestamps=True,
        )

    @staticmethod
    def _is_cuda_runtime_error(exc: BaseException) -> bool:
        s = str(exc).lower()
        return any(
            x in s
            for x in (
                "cublas",
                "cudnn",
                "cuda",
                "nvrtc",
                "dll",
                "could not load",
                "cannot load",
            )
        )

    def transcribe(self, audio: np.ndarray) -> str:
        if audio.size < SAMPLE_RATE * 0.15 or self._fw_model is None:
            return ""
        # faster-whisper needs float32 numpy array
        audio_f32 = audio.astype(np.float32)
        try:
            segments, _ = self._transcribe_segments(audio_f32)
            text = " ".join(seg.text.strip() for seg in segments).strip()
        except (RuntimeError, OSError) as e:
            if (
                not self._stt_cpu_fallback_used
                and FW_DEVICE == "cuda"
                and self._is_cuda_runtime_error(e)
            ):
                self._reload_stt_on_cpu()
                segments, _ = self._transcribe_segments(audio_f32)
                text = " ".join(seg.text.strip() for seg in segments).strip()
            else:
                raise
        logger.info("STT: %s", text)
        return text

    def think(self, user_text: str) -> str:
        """
        Ollama agentic loop with streaming + tool calling.

        Flow:
        1. Send messages + all tool schemas to Ollama
        2. If model returns tool_calls → execute each tool → add result → repeat
        3. If model returns plain text → return it (spoken reply)
        """
        from session_memory import set_active_session

        set_active_session(self._session)
        self.chat.add_user(user_text)
        self._sync_system_message()
        logger.info("User: %s", user_text)
        t0 = time.perf_counter()

        tools = _build_ollama_tools()
        # Working copy of messages for this turn (agentic loop may extend it)
        messages = self.chat.openai_style_messages()

        out = ""
        for round_num in range(MAX_TOOL_ROUNDS):
            try:
                resp = ollama.chat(
                    model=OLLAMA_MODEL,
                    messages=messages,
                    tools=tools if tools else None,
                    stream=False,  # keep False for tool call reliability
                )
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "Ollama error: %s — run: ollama serve && ollama pull %s",
                    e,
                    OLLAMA_MODEL,
                )
                self.chat.messages.pop()  # remove the user turn we added
                return "Ollama is not responding. Please start Ollama and pull the model."

            msg = resp.message
            if msg is None:
                self.chat.messages.pop()
                return "Ollama returned an empty message."

            # ── No tool calls → this is the final spoken reply ──
            if not msg.tool_calls:
                out = (msg.content or "").strip()
                break

            # ── Tool calls → execute and loop ──
            logger.info("Round %d: %d tool call(s)", round_num + 1, len(msg.tool_calls))

            # Add assistant tool-call message to working history
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": tc.function.name,
                                "arguments": _normalize_tool_arguments(tc.function.arguments),
                            }
                        }
                        for tc in msg.tool_calls
                    ],
                }
            )

            # Execute each tool, add results
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                fn_args = _normalize_tool_arguments(tc.function.arguments)
                logger.info("  → %s(%s)", fn_name, fn_args)
                result = _execute_ollama_tool(fn_name, fn_args)
                logger.info("  ← %s", result[:200])
                messages.append({"role": "tool", "content": result})
        else:
            out = "I've completed the requested actions, sir."

        t1 = time.perf_counter()
        logger.info("LLM+tools (%d ms): %s", int((t1 - t0) * 1000), out[:200])
        if out:
            self.chat.add_assistant(out)
        return out

    async def speak(self, text: str) -> None:
        """
        Speak text via Edge TTS.
        Normal-length replies are synthesized in **one** request to avoid long dead air
        between sentence chunks (each chunk used to require a new round-trip to Edge).
        Very long text is still split on sentence boundaries.
        """
        if not text:
            return

        text = _text_for_tts(text)
        if not text:
            return

        voice = pick_edge_voice(text)

        if len(text) <= JARVIS_TTS_SINGLE_MAX_CHARS:
            await _speak_edge_clip(text, voice)
            return

        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        for sentence in sentences:
            await _speak_edge_clip(sentence, voice)

    def run(self) -> None:
        self.load_stt()
        if not pygame.mixer.get_init():
            pygame.mixer.init()

        logger.info("Warming up Ollama model…")
        try:
            ollama.chat(
                model=OLLAMA_MODEL,
                messages=[{"role": "user", "content": "hi"}],
            )
            logger.info("Ollama warm.")
        except Exception as e:  # noqa: BLE001
            logger.warning("Ollama warmup failed — is ollama serve running? %s", e)

        from jarvis_tools import close_all_google_chrome

        logger.info(
            "STT=faster-whisper(%s) LLM=ollama(%s) TTS=edge | Tools=ON | GPU=%s",
            FW_MODEL_SIZE,
            OLLAMA_MODEL,
            FW_DEVICE,
        )
        logger.info(
            "TTS: voice=%s | rate=%s pitch=%s | JARVIS_TTS_LANG=%s (spoken output)",
            EDGE_VOICE,
            JARVIS_EDGE_RATE,
            JARVIS_EDGE_PITCH,
            JARVIS_TTS_LANG,
        )

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
                lab_line = _try_coding_lab_intent(text)
                if lab_line is not None:
                    logger.info("Coding lab: %s", lab_line[:300])
                    from session_memory import set_active_session

                    set_active_session(self._session)
                    self.chat.add_user(text)
                    self._sync_system_message()
                    self.chat.add_assistant(lab_line)
                    asyncio.run(self.speak(lab_line))
                    continue
                qi = _try_quick_intent(text)
                if qi is not None:
                    logger.info("Quick intent matched: %s", qi[:200])
                    from session_memory import set_active_session

                    set_active_session(self._session)
                    self.chat.add_user(text)
                    self._sync_system_message()
                    if any(
                        p in text.lower()
                        for p in (
                            "close chrome",
                            "close browser",
                            "shut chrome",
                            "quit chrome",
                        )
                    ):
                        line = "Done, sir."
                    else:
                        line = "Opening it now, sir."
                    self.chat.add_assistant(line)
                    asyncio.run(self.speak(line))
                    continue
                reply = self.think(text)
                asyncio.run(self.speak(reply))
            except KeyboardInterrupt:
                print()
                logger.info("Interrupted.")
                break
            except Exception as e:
                logger.exception("Loop error: %s", e)


def _quiet_third_party_loggers() -> None:
    """Third-party libraries often log every HTTP request at INFO."""
    for name in (
        "httpx",
        "httpcore",
        "huggingface_hub",
        "huggingface_hub.utils._http",
        "fsspec",
        "faster_whisper",
        "primp",  # ddgs / web search
        "ddgs",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    _quiet_third_party_loggers()
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

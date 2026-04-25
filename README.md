# Jarvis AI Assistant by Hunter is Live

A local, voice-driven assistant for your PC. Speak into the microphone; the app transcribes your speech, reasons with a large language model, and answers aloud with **Microsoft Edge TTS** (natural-sounding English by default). Optional **Gemini** tools can search the web, open Chrome, show maps and news, and—if you use Android **ADB**—control your phone.

This repository is intended to be **open source**. You are welcome to use it, change it, and share it under the license you add to the project (e.g. MIT). Do **not** commit API keys or personal secrets; use environment files that stay on your machine (see below).

---

## What you get

| Area | What it does |
|------|----------------|
| **Pipeline** | Microphone → **STT** (speech-to-text) → **LLM** → **TTS** (text-to-speech) |
| **STT** | **Gemini** (cloud, needs API key), **Vosk** (local, fast CPU), or **Whisper** (local, optional heavy install) |
| **LLM** | **Google Gemini** (with tools) or **Ollama** (fully local; tools only work with Gemini) |
| **TTS** | **Edge TTS** — voice, rate, pitch, volume configurable via environment variables |
| **Tools (Gemini)** | Web search, headlines, open URLs in Chrome, tiled “briefing” windows (e.g. LiveUAMap + world news), location-from-IP, and more |
| **Phone (optional)** | **ADB**: check device, wireless ADB, open WhatsApp / YouTube, search YouTube on the phone — see [Android and ADB](#android-and-adb) |
| **Wake on clap** | Optional: clap (or double-clap) to wake, then keep talking until you say you’re done — see [Clap wake](#clap-wake) |

> **Note:** Some desktop automation (Chrome window tiling, etc.) is written with **Windows** in mind. Core voice + Ollama may run on other platforms, but you may need to adjust paths and window commands.

---

## Requirements

- **Python 3.10+**
- A **microphone** and **speakers** (or headphones)
- **[uv](https://github.com/astral-sh/uv)** (recommended) *or* `pip` + a virtual environment
- For **Gemini** STT/LLM: a **[Google AI API key](https://aistudio.google.com/apikey)** (`GOOGLE_API_KEY`)
- For **Ollama**: [Ollama](https://ollama.com/) installed, `ollama serve` running, and a pulled model (e.g. `ollama pull llama3.2:3b`)
- For **optional Whisper STT**: extra install — `uv sync --extra whisper` (large PyTorch download)

---

## Quick start

### 1. Clone and enter the project

```bash
git clone <your-repo-url>
cd livrkit
```

### 2. Install dependencies

With **uv**:

```bash
uv sync
```

For **Whisper** (optional):

```bash
uv sync --extra whisper
```

### 3. Set your API key (Gemini)

Create a file named **`.env.local`** or **`.env`** in the project root (same folder as `local_jarvis.py`). The app loads it on startup. Example:

```env
GOOGLE_API_KEY=your_key_here
```

**Never** commit this file. It is already listed in `.gitignore`.

If you do not set `GOOGLE_API_KEY`, you must use local STT/LLM modes instead (Vosk/Whisper + Ollama) — see [Configuration](#configuration).

### 4. Run Jarvis

```bash
uv run local_jarvis.py
```

- Use **`Ctrl+C`** in the terminal to stop.
- To pick a different audio input, list devices, then pass the index:

```bash
uv run local_jarvis.py --list-devices
uv run local_jarvis.py --input-device 1
```

---

## Command-line options

| Option | Meaning |
|--------|--------|
| `--list-devices` | Print audio devices and exit (use the index for `--input-device`) |
| `--input-device N` | Use microphone number `N` (default: system default) |
| `--clap` | Turn **wake on clap** on for this run (sets `JARVIS_WAKE_ON_CLAP=1`) |
| `--no-clap` | Force clap wake **off** for this run |

Environment variables and `.env` / `.env.local` still apply; the flags above override clap for that session only when used.

---

## Configuration

Settings are read from the environment. The project loads **`.env.local` first, then `.env`**, and only sets variables that are **not** already in the process environment.

### Core modes

| Variable | Default | Description |
|----------|---------|-------------|
| `JARVIS_STT` | `gemini` if `GOOGLE_API_KEY` is set, else `vosk` | `gemini`, `vosk`, or `whisper` |
| `JARVIS_LLM` | `gemini` if key set, else `ollama` | `gemini` or `ollama` |
| `JARVIS_GEMINI_MODEL` | `gemini-2.0-flash` | Gemini model for chat (and default STT model unless overridden) |
| `JARVIS_GEMINI_STT_MODEL` | same as `JARVIS_GEMINI_MODEL` | Model used for Gemini speech-to-text |
| `JARVIS_OLLAMA_MODEL` | `llama3.2:3b` | Ollama model name when `JARVIS_LLM=ollama` |
| `JARVIS_VOSK_MODEL` | (bundled default path) | Path to a [Vosk](https://alphacephei.com/vosk/) model directory |
| `JARVIS_WHISPER` | `tiny` | Whisper size when `JARVIS_STT=whisper` |
| `JARVIS_WHISPER_DEVICE` | `cpu` | `cpu` or `cuda` for Whisper |

### Tools and session

| Variable | Default | Description |
|----------|---------|-------------|
| `JARVIS_ENABLE_TOOLS` | `1` | `1` / `0` — Gemini function calling (web, Chrome, maps, etc.) |
| `JARVIS_MAX_TURNS` | `12` | How many user/assistant pairs are kept in context |
| `JARVIS_SESSION_MAX_NOTES` | `12` | Short in-memory “session notes” from tools (e.g. ADB status) |
| `JARVIS_SYSTEM` | *(built-in Jarvis-style prompt)* | Override the system instruction entirely |

**Tools require** `JARVIS_LLM=gemini` and a valid `GOOGLE_API_KEY`. With Ollama, tool registration is not used.

### Android / phone

| Variable | Default | Description |
|----------|---------|-------------|
| `JARVIS_ENABLE_PHONE_TOOLS` | `1` | `0` to disable all phone ADB tools in Gemini |
| `JARVIS_ADB_WIFI_IP` | *(unset)* | Phone LAN IP if the app cannot read Wi-Fi IP from the device (e.g. `192.168.1.50`) |
| `JARVIS_PHONE_WIFI_IP` | — | Same purpose as `JARVIS_ADB_WIFI_IP` |
| `ADB_PATH` | — | Full path to `adb.exe` if not using project or PATH |
| `ADB_SERIAL` | — | `adb devices` id when more than one device is connected |
| `JARVIS_WHATSAPP_PACKAGE` | `com.whatsapp` | Adjust for WhatsApp Business or OEM builds |
| `JARVIS_WIRELESS_FOLLOWUP_ASK_YT` | `1` | After successful wireless ADB, prompt can suggest opening YouTube on the phone |
| `JARVIS_WIRELESS_FOLLOWUP_YT_QUERY` | `Knife Bros Danda Noliwala` | Default search string for that follow-up (customize freely) |

### TTS (Edge)

| Variable | Default | Description |
|----------|---------|-------------|
| `JARVIS_EDGE_VOICE` | `en-GB-RyanNeural` | Primary English voice |
| `JARVIS_TTS_LANG` | `en` | Spoken language mode; `en` uses `JARVIS_EDGE_VOICE` |
| `JARVIS_EDGE_RATE` | `+20%` | Speaking rate |
| `JARVIS_EDGE_PITCH` | `+6Hz` | Pitch |
| `JARVIS_EDGE_VOLUME` | `+0%` | Volume |
| `JARVIS_WELCOME_EN` | *(built-in)* | First spoken line when the session starts |
| `JARVIS_EDGE_VOICE_AR` | `fa-IR-FaridNeural` | Optional voice when using Arabic / mixed settings |

### Clap wake

| Variable | Default | Description |
|----------|---------|-------------|
| `JARVIS_WAKE_ON_CLAP` | `0` | `1` to require a clap before listening (or use `uv run local_jarvis.py --clap`) |
| `JARVIS_CLAP_STICKY_SESSION` | `1` | `1` = after clap, keep listening for more commands until “sleep” |
| `JARVIS_CLAP_STRIKES` | `1` | `2` for double-clap to wake |
| `JARVIS_CLAP_RMS` / `JARVIS_CLAP_PEAK` | *(tuned defaults)* | Raise if the mic triggers too easily |
| `JARVIS_CLAP_DEBUG` | `0` | `1` to log clap levels for debugging |
| `JARVIS_CLAP_REWAKE_EN` | `At your service, sir.` | Shorter line when waking again in sticky mode |
| `JARVIS_CLOSE_CHROME_ON_SLEEP` | `1` | `1` = saying sleep can also close Chrome (see code for behavior) |

### UI / terminal

| Variable | Default | Description |
|----------|---------|-------------|
| `JARVIS_SHOW_BANNER` | `1` | `0` to hide the ASCII JARVIS banner |
| `JARVIS_ASCII_STYLE` | `slant` | `standard` (block letters) or `slant` |
| `JARVIS_BANNER_THEME` | `jarvis` | `jarvis`, `orange`, `fire`, `none`, … |
| `JARVIS_BANNER_COLOR` | `1` | `0` to disable ANSI colors |
| `NO_COLOR` | — | If set, disables banner colors (standard env) |

### Chrome and news (tools)

| Variable | Default | Description |
|----------|---------|-------------|
| `JARVIS_CHROME` | *(auto)* | Path to `chrome.exe` if not found automatically |
| `JARVIS_CHROME_TILE` | `1` | Tiled multi-window layout for map + news |
| `JARVIS_WORLD_NEWS_URL` | *(BBC default in code)* | World news page for `jarvis_browser_routines` |

---

## Android and ADB

1. On the phone: enable **Developer options** → **USB debugging**. Accept the computer’s RSA prompt when you plug in USB.
2. Fetch **platform-tools** into the project (so `android_adb` can find `platform-tools\adb.exe` on Windows):

   ```bash
   uv run python scripts/fetch_platform_tools.py
   ```

   Or install Google [platform-tools](https://developer.android.com/studio/releases/platform-tools) and put `adb` on your `PATH`, or set `ADB_PATH` to the full path of `adb.exe`.

3. Optional: set **`JARVIS_ADB_WIFI_IP`** to your phone’s **LAN** address if wireless setup cannot read the IP from the device. Phone and PC must be on the **same Wi-Fi** for wireless ADB.

Jarvis can run tools such as `phone_check_adb_and_devices`, `phone_enable_wireless_adb`, `phone_open_youtube`, and `phone_youtube_search_and_open` when `JARVIS_ENABLE_PHONE_TOOLS=1` and the LLM is Gemini.

**Standalone CLI (no voice):** for quick checks you can also run:

```bash
uv run phone_adb_control.py --help
uv run phone_adb_control.py devices
uv run phone_adb_control.py wireless --port 5555
```

---

## Troubleshooting

| Problem | What to try |
|--------|-------------|
| **No sound / wrong mic** | `uv run local_jarvis.py --list-devices` and `--input-device` |
| **Gemini errors** | Confirm `GOOGLE_API_KEY` in `.env.local`, billing/API access, and model name |
| **“Tools” not working** | Use `JARVIS_LLM=gemini` and `JARVIS_ENABLE_TOOLS=1` |
| **Clap too sensitive or deaf** | Adjust `JARVIS_CLAP_RMS`, `JARVIS_CLAP_PEAK`, or use `JARVIS_CLAP_DEBUG=1` |
| **Ollama errors** | Run `ollama serve` and `ollama pull <model>`; match `JARVIS_OLLAMA_MODEL` |
| **Vosk missing** | Download a model from Vosk and set `JARVIS_VOSK_MODEL` to the folder path, or switch to `JARVIS_STT=gemini` with an API key |
| **ADB / phone** | `uv run phone_adb_control.py devices`, USB cable, authorization on phone, or `JARVIS_ADB_WIFI_IP` for Wi-Fi |
| **Whisper too slow** | Smaller `JARVIS_WHISPER` model, or `JARVIS_WHISPER_DEVICE=cuda` with a GPU |

---

## Privacy and security

- **Gemini** sends audio and text to Google’s APIs; read Google’s terms and privacy policy for your use case.
- **Edge TTS** uses Microsoft’s service for synthesis.
- **Ollama** and **Vosk/Whisper** can keep inference local if you do not use Gemini.
- **Web search and browsing tools** use your network; only enable tools you are comfortable with.
- Never share `.env` / `.env.local` or commit them to git.

---

## Project layout (high level)

| File / folder | Role |
|---------------|------|
| `local_jarvis.py` | Main entry: record, transcribe, LLM, TTS loop |
| `jarvis_tools.py` | Gemini tool functions (search, Chrome, maps, ADB registration) |
| `jarvis_browser_routines.py` | LiveUAMap + world news in Chrome |
| `jarvis_adb_tools.py` | Phone-related tool wrappers for Gemini |
| `android_adb.py` | ADB helpers (wireless, WhatsApp, YouTube, shell) |
| `session_memory.py` | Short rolling memory for tool results |
| `phone_adb_control.py` | Optional CLI for ADB without the voice app |
| `scripts/fetch_platform_tools.py` | Download Android platform-tools into `platform-tools/` |
| `pyproject.toml` / `uv.lock` | Dependencies (package name in metadata may differ from the marketing name above) |

---

## Contributing and open source

Contributions, issues, and pull requests are welcome. Suggested first steps for contributors:

1. Fork the repository and create a branch for your change.
2. Run the app with `uv run local_jarvis.py` and, if you change tools, test with `JARVIS_LLM=gemini` and tools enabled.
3. Do not commit secrets; use `.env.local` locally.
4. Add a **LICENSE** file (e.g. MIT) if the maintainer has not already done so, and state it clearly in the repo.

---

## Credits

**Jarvis AI Assistant by Hunter is Live.**

Thanks to the open-source projects this stack builds on, including [Google Gemini](https://ai.google.dev/), [Ollama](https://ollama.com/), [Vosk](https://alphacephei.com/vosk/), [Edge TTS](https://github.com/rany2/edge-tts), [ddgs](https://github.com/deedy5/duckduckgo_search), and the broader Python ecosystem.

If this project helps you, consider starring the repository and sharing feedback.

---

*Happy building — and enjoy your local Jarvis experience.*

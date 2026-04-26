# Jarvis AI Assistant by Hunter is Live

A local, low-latency voice assistant for your PC: **faster-whisper** (STT) → **Ollama** (LLM with tool calling) → **Microsoft Edge TTS** (English by default). **Jarvis tools** (web search, Chrome, maps, news) are registered for Ollama. No Google API key.

This repository is intended to be **open source**. You are welcome to use it, change it, and share it under the license you add to the project (e.g. MIT). Do **not** commit API keys or personal secrets; use environment files that stay on your machine (see below).

**GitHub:** [github.com/HunterisLive-1/jarvis](https://github.com/HunterisLive-1/jarvis) — clone URL: `https://github.com/HunterisLive-1/jarvis.git`

---

## What you get

| Area | What it does |
|------|----------------|
| **Pipeline** | Microphone → **faster-whisper** → **Ollama** (tools) → **Edge TTS** |
| **STT** | **faster-whisper** (local; CUDA recommended — `JARVIS_FW_*`) |
| **LLM** | **Ollama** with tool calling — `JARVIS_OLLAMA_MODEL` (default `qwen2.5:3b`) |
| **TTS** | **Edge TTS** — voice, rate, pitch, volume configurable via environment variables |
| **Tools** | Web search, headlines, open URLs in Chrome, tiled “briefing” windows (e.g. LiveUAMap + world news), location-from-IP, and more — via Ollama |
| **Wake on clap** | Optional: clap (or double-clap) to wake, then keep talking until you say you’re done — see [Clap wake](#clap-wake) |

> **Note:** Some desktop automation (Chrome window tiling, etc.) is written with **Windows** in mind. Core voice + Ollama may run on other platforms, but you may need to adjust paths and window commands.

---

## Requirements

- **Python 3.11+** (3.10 is not supported by current `faster-whisper` / `onnxruntime` wheels on some platforms)
- A **microphone** and **speakers** (or headphones)
- **[uv](https://github.com/astral-sh/uv)** (recommended) *or* `pip` + a virtual environment
- **GPU (optional)**: for STT, set `JARVIS_FW_DEVICE=cuda` and install a working CUDA stack for [faster-whisper](https://github.com/SYSTRAN/faster-whisper) / [ctranslate2](https://github.com/OpenNMT/CTranslate2) (or use `cpu` and `JARVIS_FW_COMPUTE=int8`)
- **[Ollama](https://ollama.com/)** installed, `ollama serve` running, and a pulled model (e.g. `ollama pull qwen2.5:3b`) matching `JARVIS_OLLAMA_MODEL`

---

## One-time setup (fully local)

```bash
# 1. Install Ollama + pull model
winget install Ollama.Ollama
ollama pull qwen2.5:3b

# 2. Install Python deps (uv)
uv sync

# 3. Run
uv run python local_jarvis.py
```

On non-Windows, install Ollama from [ollama.com](https://ollama.com/) and run the same `ollama pull` / `uv sync` / `uv run` steps.

---

## From zero: complete setup (for a new person)

Use this on a **Windows** PC; adjust `winget` / paths on Linux or macOS (install **Git**, **Python 3.11+**, **uv**, and **Ollama** from your package manager or official sites).

1. **Install Git (to clone the repo)**  
   - Windows: [git-scm.com](https://git-scm.com/download/win) *or* `winget install Git.Git`  
   - Verify: `git --version`

2. **Install Python 3.11+** (required by the project)  
   - Windows: [python.org](https://www.python.org/downloads/) and enable **“Add python.exe to PATH”** *or* `winget install Python.Python.3.12`  
   - Verify: `python --version` (should be 3.11 or newer)

3. **Install [uv](https://docs.astral.sh/uv/)** (manages the virtual environment and runs the app)  
   - `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"` (PowerShell) *or* `pip install uv`  
   - Verify: `uv --version`

4. **Install Ollama** (local LLM server)  
   - Windows: `winget install Ollama.Ollama` *or* download from [ollama.com](https://ollama.com/)  
   - After install, Ollama usually runs in the background. Verify: `ollama --version`  
   - **Pull a model** (must match or sit under your config, default is `qwen2.5:3b`):  
     `ollama pull qwen2.5:3b`

5. **Install Google Chrome** (optional but used by “open in Chrome” / map / news tools)  
   - [google.com/chrome](https://www.google.com/chrome/) *or* `winget install Google.Chrome`  

6. **Install Visual Studio Code** (optional; for voice **coding lab** — “set up my lab” opens a folder in VS Code)  
   - [code.visualstudio.com](https://code.visualstudio.com/) and enable **“Add to PATH”** *or* set `JARVIS_VSCODE_PATH` in `.env.local` to your `code.cmd` path  

7. **Clone this repository and enter the folder**  
   ```bash
   git clone https://github.com/HunterisLive-1/jarvis.git
   cd jarvis
   ```

8. **Create your local environment file** (not committed; keeps your choices private)  
   ```bash
   copy .env.example .env.local
   ```
   - Edit **`.env.local`** if needed: e.g. `JARVIS_FW_DEVICE=cpu` if you have no GPU, `JARVIS_OLLAMA_MODEL=qwen2.5:3b` to match what you pulled.  

9. **Install Python dependencies (creates/uses a project venv via uv)**  
   ```bash
   uv sync
   ```

10. **Run Jarvis**  
    ```bash
    uv run python local_jarvis.py
    ```
    - If the model name in `.env.local` does not match a pulled Ollama model, fix `JARVIS_OLLAMA_MODEL` or run `ollama pull <name>`.  
    - Stop with **Ctrl+C**.  
    - Wrong microphone: `uv run python local_jarvis.py --list-devices` then `uv run python local_jarvis.py --input-device N`  

**Hardware / OS summary:** microphone and speakers/headphones; **GPU optional** for faster STT (CUDA) — otherwise set CPU + int8 in `.env.local` as in [Troubleshooting](#troubleshooting).

## Quick start

### 1. Clone and enter the project

```bash
git clone https://github.com/HunterisLive-1/jarvis.git
cd jarvis
```

### 2. Install dependencies

With **uv**:

```bash
uv sync
```

### 3. Optional: environment

Copy **`.env.example`** to **`.env`** or **`.env.local`** and adjust voices, Ollama model, or `JARVIS_FW_DEVICE` (`cuda` / `cpu`). The app loads `.env.local` first, then `.env`. **Never** commit secrets.

### 4. Run Jarvis

```bash
uv run python local_jarvis.py
```

- Use **`Ctrl+C`** in the terminal to stop.
- To pick a different audio input, list devices, then pass the index:

```bash
uv run python local_jarvis.py --list-devices
uv run python local_jarvis.py --input-device 1
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

### Core pipeline

| Variable | Default | Description |
|----------|---------|-------------|
| `JARVIS_FW_MODEL` | `base.en` | faster-whisper size (`tiny.en`, `base.en`, `small.en`, …) |
| `JARVIS_FW_DEVICE` | `cuda` | `cuda` or `cpu` for STT |
| `JARVIS_FW_COMPUTE` | `float16` | `float16` or `int8` for GPU |
| `JARVIS_FW_COMPUTE_CPU` | `int8` | CPU compute type and after a GPU→CPU fallback |
| `JARVIS_OLLAMA_MODEL` | `qwen2.5:3b` | Ollama model name (must be pulled, e.g. `ollama pull qwen2.5:3b`) |

### Tools and session

| Variable | Default | Description |
|----------|---------|-------------|
| `JARVIS_MAX_TURNS` | `12` | How many user/assistant pairs are kept in context |
| `JARVIS_SESSION_MAX_NOTES` | `12` | Short in-memory “session notes” from tools |
| `JARVIS_SYSTEM` | *(built-in Jarvis-style prompt)* | Override the system instruction entirely |

Tool functions registered for Ollama include web search, news, maps, and Chrome. Common phrases (e.g. “open YouTube”) can bypass the LLM for faster response. **Ollama** is warmed with a short chat at startup.

### TTS (Edge)

| Variable | Default | Description |
|----------|---------|-------------|
| `JARVIS_EDGE_VOICE` | `en-GB-ThomasNeural` | Primary English voice (UK male; Jarvis-like). Alternatives: `en-GB-RyanNeural`, `en-US-ChristopherNeural` |
| `JARVIS_TTS_LANG` | `en` | Spoken language mode; `en` uses `JARVIS_EDGE_VOICE` |
| `JARVIS_EDGE_RATE` | `+10%` | Speaking rate |
| `JARVIS_EDGE_PITCH` | `+2Hz` | Pitch |
| `JARVIS_EDGE_VOLUME` | `+0%` | Volume |
| `JARVIS_TTS_SINGLE_MAX_CHARS` | `4000` | Replies this short (after cleanup) are spoken in **one** Edge request; avoids long gaps between lines |
| `JARVIS_WELCOME_EN` | *(built-in)* | First spoken line when the session starts |
| `JARVIS_EDGE_VOICE_AR` | `fa-IR-FaridNeural` | Optional voice when using Arabic / mixed settings |

### Clap wake

| Variable | Default | Description |
|----------|---------|-------------|
| `JARVIS_WAKE_ON_CLAP` | `0` | `1` to require a clap before listening (or use `uv run python local_jarvis.py --clap`) |
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

### Coding lab (no LLM)

Say e.g. **“set up my lab”** or **“prepare my coding environment”** (optionally with a path, a quoted path, or **“on desktop *folder name*”**). This creates a project folder, optional `.venv`, and opens **VS Code** — it does **not** call Ollama.

| Variable | Default | Description |
|----------|---------|-------------|
| `JARVIS_LAB_BASE` | `~/code` (under your user profile) | Base when you give a *name* only (e.g. `named myproject`) |
| `JARVIS_LAB_DEFAULT_NAME` | `python-lab` | Folder when you only say “set up my lab” with no other hint |
| `JARVIS_LAB_VENV` | `1` | `0` to skip creating `.venv` |
| `JARVIS_VSCODE_PATH` | *(search PATH + common installs)* | Full path to `code.cmd` if `code` is not on `PATH` |

---

## Troubleshooting

| Problem | What to try |
|--------|-------------|
| **No sound / wrong mic** | `uv run python local_jarvis.py --list-devices` and `--input-device` |
| **STT / CUDA** (`cublas64_12.dll`, etc.) | Jarvis will **auto-fallback to CPU** once. For native GPU, install a **full CUDA 12** runtime (cuBLAS on `PATH`) matching CTranslate2, or set `JARVIS_FW_DEVICE=cpu` and `JARVIS_FW_COMPUTE_CPU=int8` |
| **Clap too sensitive or deaf** | Adjust `JARVIS_CLAP_RMS`, `JARVIS_CLAP_PEAK`, or use `JARVIS_CLAP_DEBUG=1` |
| **Ollama errors** | Run `ollama serve` and `ollama pull <model>`; match `JARVIS_OLLAMA_MODEL` |
| **Slower STT** | Smaller `JARVIS_FW_MODEL` (e.g. `tiny.en`) or use CPU int8 for lighter load |

---

## Privacy and security

- **Edge TTS** uses Microsoft’s service for synthesis.
- **Ollama** and **faster-whisper** keep STT/LLM inference on your machine (no cloud LLM in the default stack).
- **Web search and browsing tools** use your network; only enable tools you are comfortable with.
- Never share `.env` / `.env.local` or commit them to git.

---

## Project layout (high level)

| File / folder | Role |
|---------------|------|
| `local_jarvis.py` | Main entry: record, transcribe, LLM, TTS loop |
| `jarvis_tools.py` | Tool functions (search, Chrome, maps, news) for Ollama |
| `jarvis_coding_lab.py` | Voice “coding lab” setup (folder + venv + VS Code; no LLM) |
| `jarvis_browser_routines.py` | LiveUAMap + world news in Chrome |
| `session_memory.py` | Short rolling memory for tool results |
| `pyproject.toml` / `uv.lock` | Dependencies; PyPI-style name is `livrkit-agent`, while the GitHub repo is [**HunterisLive-1/jarvis**](https://github.com/HunterisLive-1/jarvis) |

---

## Contributing and open source

Contributions, issues, and pull requests are welcome. Suggested first steps for contributors:

1. Fork [HunterisLive-1/jarvis](https://github.com/HunterisLive-1/jarvis) and create a branch for your change.
2. Run the app with `uv run python local_jarvis.py` and, if you change tools, test with a running Ollama server and a pulled model.
3. Do not commit secrets; use `.env.local` locally.
4. Add a **LICENSE** file (e.g. MIT) if the maintainer has not already done so, and state it clearly in the repo.

---

## Credits

**Jarvis AI Assistant by Hunter is Live.**

Thanks to the open-source projects this stack builds on, including [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [Ollama](https://ollama.com/), [Edge TTS](https://github.com/rany2/edge-tts), [ddgs](https://github.com/deedy5/duckduckgo_search), and the broader Python ecosystem.

If this project helps you, consider [starring the repository on GitHub](https://github.com/HunterisLive-1/jarvis) and sharing feedback.

---

*Happy building — and enjoy your local Jarvis experience.*

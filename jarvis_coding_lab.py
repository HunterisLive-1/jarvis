"""
Voice-triggered Python coding lab setup. Used from local_jarvis quick-intent (no Ollama).
Creates a folder, optional .venv, and opens Visual Studio Code.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("jarvis_coding_lab")

# Default base when you say a name (e.g. "setup lab project foo") not a full path
_LAB_BASE = os.environ.get("JARVIS_LAB_BASE", "").strip()
# Folder name if you only say e.g. "setup lab" with no other hint
_LAB_DEFAULT_NAME = os.environ.get("JARVIS_LAB_DEFAULT_NAME", "python-lab").strip() or "python-lab"
# 1 (default) = create .venv, 0 = only mkdir + open editor
_LAB_VENV = os.environ.get("JARVIS_LAB_VENV", "1").strip().lower() not in ("0", "false", "no", "off")
# Optional: full path to code.cmd or Code.exe; otherwise PATH + common install paths
_VSCODE_OVERRIDE = os.environ.get("JARVIS_VSCODE_PATH", "").strip()


def _norm(text: str) -> str:
    t = " ".join(text.lower().split())
    return re.sub(r"\bset\s+up\b", "setup", t, flags=re.IGNORECASE)


def _is_lab_intent(nl: str) -> bool:
    """STT-normalized, collapsed 'set up' -> 'setup' string."""
    if not re.search(r"\b(setup|prepare)\b", nl):
        return False
    if re.search(r"\b(lab|labs)\b", nl):
        return True
    if "coding" in nl and "environment" in nl:
        return True
    if re.search(r"\b(dev|python)\b", nl) and "environment" in nl:
        return True
    return False


def _resolve_windows_path(s: str) -> Path | None:
    m = re.search(r"([A-Za-z]:[\\/][^\n\r\"'|*?<>]+?)(?:[.,;!]|$)", s)
    if not m:
        return None
    raw = m.group(1).rstrip(".,;! ")
    # Trim trailing backslash noise from STT
    raw = re.sub(r"[\s,]+$", "", raw)
    try:
        p = Path(raw)
        if p.drive and len(p.parts) >= 1:
            return p
    except OSError:
        return None
    return None


def _quoted(s: str) -> Path | None:
    m = re.search(r'"([^"\n]+)"', s)
    if m:
        try:
            return Path(m.group(1).strip()).expanduser()
        except OSError:
            return None
    m2 = re.search(r"'([^'\n]+)'", s)
    if m2:
        try:
            return Path(m2.group(1).strip()).expanduser()
        except OSError:
            return None
    return None


def _last_segment_name(nl: str) -> str | None:
    # " named mything ", " called mything ", " folder mything " at end-ish
    for pat in (
        r"\b(?:named|called)\s+([A-Za-z0-9_.\- ]+?)\s*$",
        r"\bfolder\s+([A-Za-z0-9_.\- ]+?)\s*$",
        r"\bproject\s+([A-Za-z0-9_.\- ]+?)\s*$",
    ):
        m = re.search(pat, nl, re.IGNORECASE)
        if m:
            name = m.group(1).strip(" .,;!")
            if 1 <= len(name) <= 200:
                return name
    return None


def _desktop_subfolder(nl: str) -> Path | None:
    m = re.search(
        r"\b(?:on|to)\s+(?:the\s+)?(?:my\s+)?desktop\s+([A-Za-z0-9_.\- ]+?)\s*$",
        nl,
        re.IGNORECASE,
    )
    if not m:
        return None
    name = m.group(1).strip(" .,;!")
    if not name or len(name) > 200:
        return None
    desktop = Path.home() / "Desktop"
    return desktop / name


def lab_base_dir() -> Path:
    if _LAB_BASE:
        return Path(_LAB_BASE).expanduser()
    return Path.home() / "code"


def resolve_target_folder(user_text: str) -> Path:
    """Pick folder from speech; defaults under JARVIS_LAB_BASE (or ~/code) / default name."""
    s = user_text.strip()
    nl = _norm(s)

    p = _resolve_windows_path(s)
    if p is not None:
        return p.expanduser()

    p = _quoted(s)
    if p is not None:
        return p

    p = _desktop_subfolder(nl)
    if p is not None:
        return p

    name = _last_segment_name(nl)
    if name:
        return lab_base_dir() / name.replace(" ", "_")

    return lab_base_dir() / _LAB_DEFAULT_NAME


def _find_vscode() -> str | None:
    if _VSCODE_OVERRIDE and Path(_VSCODE_OVERRIDE).is_file():
        return str(Path(_VSCODE_OVERRIDE).resolve())
    c = shutil.which("code")
    if c:
        return c
    local = os.environ.get("LOCALAPPDATA", "")
    for rel in (
        "Programs/Microsoft VS Code/bin/code.cmd",
        "Programs/Microsoft VS Code/bin/code",
    ):
        if local:
            p = Path(local) / rel
            if p.is_file():
                return str(p)
    pf = os.environ.get("ProgramFiles", "")
    if pf:
        p = Path(pf) / "Microsoft VS Code" / "bin" / "code.cmd"
        if p.is_file():
            return str(p)
    return None


def _ensure_venv(project: Path) -> None:
    vdir = project / ".venv"
    if vdir.is_dir() and (
        (vdir / "Scripts" / "python.exe").is_file() or (vdir / "bin" / "python").is_file()
    ):
        logger.info("Lab: .venv already present")
        return
    uv = shutil.which("uv")
    if uv:
        r = subprocess.run(
            [uv, "venv", str(vdir)],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=90,
        )
        if r.returncode == 0 and vdir.is_dir():
            logger.info("Lab: created .venv with uv")
            return
        logger.info("Lab: uv venv failed (%s), falling back: %s", r.returncode, (r.stderr or r.stdout)[:200])
    r2 = subprocess.run(
        [sys.executable, "-m", "venv", str(vdir)],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if r2.returncode == 0 and vdir.is_dir():
        logger.info("Lab: created .venv with %s -m venv", sys.executable)
    else:
        logger.warning("Lab: venv failed: %s", (r2.stderr or r2.stdout)[:300])


def _open_vscode(project: Path) -> bool:
    exe = _find_vscode()
    if not exe:
        logger.warning("Lab: Visual Studio Code not found (add to PATH or set JARVIS_VSCODE_PATH)")
        return False
    try:
        # Windows: code.cmd; detach so we don't block
        subprocess.Popen(  # noqa: S603
            [exe, str(project.resolve())],
            close_fds=(sys.platform != "win32"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
        )
        logger.info("Lab: started VS Code for %s", project)
        return True
    except OSError as e:
        logger.error("Lab: could not start VS Code: %s", e)
        return False


def run_coding_lab(user_text: str) -> str | None:
    """
    If the utterance is a lab setup request, create folder, optional venv, open VS Code.
    Returns a line to speak and log, or None to let the rest of the pipeline handle the text.
    """
    nl = _norm(user_text)
    if not _is_lab_intent(nl):
        return None

    target = resolve_target_folder(user_text)
    logger.info("Lab: target folder %s", target)

    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        err = f"I couldn't create the folder, sir. {e}"
        logger.error("Lab: mkdir: %s", e)
        return err

    # Optional marker file (helps the folder look like a project in Explorer)
    readme = target / "README.md"
    if not readme.is_file():
        try:
            readme.write_text(
                "# Python lab\n\nUse the `.venv` in this folder or create one with `python -m venv .venv`.\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    if _LAB_VENV:
        _ensure_venv(target)

    opened = _open_vscode(target)

    loc = str(target)
    if opened:
        return f"Your Python lab is ready, sir. VS Code is opening in {loc}."
    return f"Folder is ready at {loc}, sir. I could not find VS Code; add the code command to your PATH or set JARVIS_VSCODE_PATH."


def extract_folder_hint_for_test(user_text: str) -> Path:
    """Exposed for tests; same as resolve_target_folder (always returns a path)."""
    return resolve_target_folder(user_text)

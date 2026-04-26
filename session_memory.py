"""
Lightweight, in-RAM "scratchpad" for the current :class:`~local_jarvis.Jarvis` run.
Destroyed when the process exits or you say **reset session** / **clear session** (clears scratchpad and chat).

* Not persisted to disk.
* Short rolling notes (default cap from ``JARVIS_SESSION_MAX_NOTES``), e.g. one-line tool summaries.
* Injected as a suffix on the system instruction so the model keeps context across long flows.

Call :func:`note_tool_result` from tool code if you need the next turn to see a one-line tool summary.
"""

from __future__ import annotations

import os
import threading
from collections import deque

_active: SessionMemory | None = None
_lock = threading.Lock()


class SessionMemory:
    def __init__(self) -> None:
        n = int(os.environ.get("JARVIS_SESSION_MAX_NOTES", "12"))
        self._notes: deque[str] = deque(maxlen=max(1, min(n, 30)))

    def add(self, line: str) -> None:
        t = (line or "").replace("\n", " ").replace("\r", " ").strip()[:500]
        if t:
            self._notes.append(t)

    def clear(self) -> None:
        self._notes.clear()

    def instruction_suffix(self) -> str:
        if not self._notes:
            return ""
        body = "\n".join(f"- {x}" for x in self._notes)
        return (
            "\n\n**Session memory (in RAM for this run only, cleared on exit or 'reset session'):**\n"
            f"{body}\n"
            "Use this to stay consistent with recent tool outcomes when relevant."
        )


def set_active_session(session: SessionMemory) -> None:
    global _active
    with _lock:
        _active = session


def get_active_session() -> SessionMemory | None:
    with _lock:
        return _active


def note_tool_result(tag: str, result_text: str) -> None:
    s = get_active_session()
    if s is not None and result_text:
        s.add(f"{tag}: {result_text[:420]}")

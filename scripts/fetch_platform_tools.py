"""
Download Google Android platform-tools (adb, fastboot, etc.) into the project.

Creates ../platform-tools/ next to the repo root (parent of this scripts/ folder).
Run: uv run python scripts/fetch_platform_tools.py
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path
from urllib.request import urlopen

# Official Windows bundle (kept next to the livrkit project root)
URL = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "platform-tools"


def main() -> int:
    if (TARGET / "adb.exe").is_file():
        print(f"Already present: {TARGET / 'adb.exe'}")
        return 0
    print(f"Downloading {URL!r} …")
    with urlopen(URL, timeout=120) as r:
        data = r.read()
    print(f"Downloaded {len(data) // 1_000_000} MB, extracting to {TARGET} …")
    z = zipfile.ZipFile(io.BytesIO(data))
    # zip contains a single top-level directory "platform-tools/"
    for info in z.infolist():
        if info.is_dir():
            continue
        # strip "platform-tools/" prefix
        name = info.filename
        if name.startswith("platform-tools/"):
            rel = name[len("platform-tools/") :]
        else:
            rel = Path(name).name
        out = TARGET / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        with z.open(info) as src, open(out, "wb") as dst:
            dst.write(src.read())
    if not (TARGET / "adb.exe").is_file():
        print("Extraction did not produce adb.exe; check the zip layout.", file=sys.stderr)
        return 1
    print(f"OK: {TARGET / 'adb.exe'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

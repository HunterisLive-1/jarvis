#!/usr/bin/env python3
"""
Command-line helper for :mod:`android_adb` — USB / Wi-Fi ADB and quick app actions.

Examples
--------
  uv run phone_adb_control.py about
  uv run phone_adb_control.py devices
  uv run phone_adb_control.py wireless --port 5555
  uv run phone_adb_control.py whatsapp
  uv run phone_adb_control.py youtube
  uv run phone_adb_control.py play "song name"

Requires Google **platform-tools** ``adb`` on PATH, or set **ADB_PATH** to ``adb.exe``.
"""

from __future__ import annotations

import argparse
import sys

import android_adb as adb


def _cmd_about(_: argparse.Namespace) -> int:
    print(adb.explain_connection())
    return 0


def _cmd_version(_: argparse.Namespace) -> int:
    print(adb.adb_version_line())
    return 0


def _cmd_devices(_: argparse.Namespace) -> int:
    print(adb.list_devices())
    return 0


def _cmd_wireless(ns: argparse.Namespace) -> int:
    r = adb.enable_wireless_adb(port=ns.port, serial=ns.serial)
    print(r.message)
    if r.connect_host:
        print("Connect target:", r.connect_host)
    return 0 if r.ok else 1


def _cmd_shell(ns: argparse.Namespace) -> int:
    print(adb.adb_shell(ns.command, serial=ns.serial))
    return 0


def _cmd_whatsapp(ns: argparse.Namespace) -> int:
    print(adb.open_whatsapp(serial=ns.serial))
    return 0


def _cmd_youtube(ns: argparse.Namespace) -> int:
    print(adb.open_youtube(serial=ns.serial))
    return 0


def _cmd_play(ns: argparse.Namespace) -> int:
    print(adb.youtube_open_search(ns.query, serial=ns.serial))
    return 0


def _serial(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "-s",
        "--serial",
        default=None,
        help="Device id from `adb devices` (else ADB_SERIAL env)",
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s1 = sub.add_parser("about", help="Explain USB, debugging, and Wi-Fi ADB")
    s1.set_defaults(run=_cmd_about)

    s2 = sub.add_parser("version", help="Print adb version (sanity check)")
    s2.set_defaults(run=_cmd_version)

    s3 = sub.add_parser("devices", help="List `adb devices -l`")
    s3.set_defaults(run=_cmd_devices)

    s4 = sub.add_parser(
        "wireless",
        help="USB: adb tcpip, discover IP, adb connect (then you can unplug)",
    )
    s4.add_argument(
        "-p",
        "--port",
        type=int,
        default=5555,
        help="TCP port (default 5555)",
    )
    _serial(s4)
    s4.set_defaults(run=_cmd_wireless)

    s5 = sub.add_parser("shell", help="Run one adb shell line")
    _serial(s5)
    s5.add_argument("command", help="Shell command, e.g. echo hello")
    s5.set_defaults(run=_cmd_shell)

    s6 = sub.add_parser("whatsapp", help="Start WhatsApp (launcher intent)")
    _serial(s6)
    s6.set_defaults(run=_cmd_whatsapp)

    s7 = sub.add_parser("youtube", help="Start YouTube app")
    _serial(s7)
    s7.set_defaults(run=_cmd_youtube)

    s8 = sub.add_parser(
        "play",
        help="YouTube: open search results for a query (tap play on the phone if needed)",
    )
    s8.add_argument("query", help="What to search for in YouTube")
    _serial(s8)
    s8.set_defaults(run=_cmd_play)

    ns = p.parse_args()
    return ns.run(ns)  # type: ignore[union-attr, arg-type]


if __name__ == "__main__":
    raise SystemExit(main())

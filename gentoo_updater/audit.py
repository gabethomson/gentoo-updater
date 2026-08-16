"""One JSON line per run, appended to a history file. build_record() is pure
(report in, dict out); AuditLog just appends and never raises on a bad write."""

from __future__ import annotations

import datetime as _dt
import json
import os

# Preferred system location; falls back to a user state dir if unwritable.
SYSTEM_PATH = "/var/log/gentoo-updater/history.jsonl"


def _user_path() -> str:
    base = (os.environ.get("XDG_STATE_HOME")
            or os.path.expanduser("~/.local/state"))
    return os.path.join(base, "gentoo-updater", "history.jsonl")


def build_record(report, *, started: float, finished: float,
                 version: str, command: str = "update") -> dict:
    plan = report.plan
    return {
        "timestamp": _iso(finished),
        "command": command,
        "version": version,
        "duration_s": round(max(0.0, finished - started), 1),
        "ok": not report.failed,
        "phases": [
            {
                "name": p.name,
                "status": "skipped" if p.skipped else ("ok" if p.ok else "fail"),
                "detail": p.detail,
            }
            for p in report.phases
        ],
        "snapshot_id": report.snapshot_id,
        "reboot_pkgs": list(report.reboot_pkgs),
        "packages_total": plan.total if plan else 0,
    }


def render_line(record: dict) -> str:
    return json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"


def _iso(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


class AuditLog:
    # Append-only, best-effort. A failed write is a lost diary entry, not a
    # failed run, so it never raises.

    def __init__(self, path: str = ""):
        self.path = path or SYSTEM_PATH
        self._fallback = _user_path()

    def record(self, entry: dict) -> str | None:
        # Returns the path written, or None if nothing was writable.
        line = render_line(entry)
        for candidate in (self.path, self._fallback):
            if self._append(candidate, line):
                return candidate
        return None

    @staticmethod
    def _append(path: str, line: str) -> bool:
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
            return True
        except OSError:
            return False

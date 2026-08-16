"""Audit trail: one structured record per run, appended as JSON Lines.

Keeping a machine-readable history of what gup did -- when, which phases passed,
what got snapshotted, whether a reboot was advised -- makes it possible to answer
"what changed on this box last Tuesday?" without spelunking emerge logs. The
record builder is pure (report in, dict out) so it's trivially testable; the
writer is a thin, defensive append.
"""

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
    """Turn an UpdateReport into a flat, JSON-serialisable audit record."""
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
    """One compact JSON object, newline-terminated (JSON Lines)."""
    return json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"


def _iso(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


class AuditLog:
    """Append-only JSONL writer. Best-effort: a write failure is never allowed
    to sink a run that otherwise succeeded -- it's a diary, not a transaction."""

    def __init__(self, path: str = ""):
        self.path = path or SYSTEM_PATH
        self._fallback = _user_path()

    def record(self, entry: dict) -> str | None:
        """Append one record. Returns the path written, or None on failure."""
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

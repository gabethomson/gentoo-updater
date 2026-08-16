"""Per-run debug log. Every run overwrites one debug.log with a timestamped
trace of the commands run, their exit codes and timing, and phase results, so a
stuck or failed run can be picked apart afterwards."""

from __future__ import annotations

import datetime as _dt
import logging
import os
import tempfile

LOGGER = "gentoo_updater"

_FMT = "%(asctime)s %(levelname)-5s %(name)s: %(message)s"


def _candidate_dirs() -> list[str]:
    state = (os.environ.get("XDG_STATE_HOME")
             or os.path.expanduser("~/.local/state"))
    return [
        "/var/log/gentoo-updater",
        os.path.join(state, "gentoo-updater"),
        os.path.join(tempfile.gettempdir(), "gentoo-updater"),
    ]


def setup(path: str = "") -> str | None:
    """Attach a file handler to the package logger. Returns the path written, or
    None if nowhere was writable. `path`, if given, is the exact log file."""
    logger = logging.getLogger(LOGGER)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    targets = [path] if path else [os.path.join(d, "debug.log")
                                   for d in _candidate_dirs()]
    for target in targets:
        try:
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            handler = logging.FileHandler(target, mode="w", encoding="utf-8")
            handler.setFormatter(logging.Formatter(_FMT))
            logger.addHandler(handler)
            logger.debug("--- gentoo-updater run %s ---",
                         _dt.datetime.now().isoformat(timespec="seconds"))
            return target
        except OSError:
            continue
    return None

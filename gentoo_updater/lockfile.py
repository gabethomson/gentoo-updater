"""Single-instance lock so two mutating runs (or a run racing a manual emerge
of our own) can't proceed at once. Advisory flock; released on exit/crash."""

from __future__ import annotations

import contextlib
import fcntl
import os
import tempfile

_PREFERRED = "/run/lock/gentoo-updater.lock"


class AlreadyRunning(RuntimeError):
    def __init__(self, path: str):
        super().__init__(f"another gentoo-updater run holds the lock ({path})")
        self.path = path


def _open_lockfile() -> "tuple[object, str]":
    """Open the lockfile, falling back to a temp dir if /run/lock isn't writable
    (e.g. running as a non-root user for a dry-run)."""
    for path in (_PREFERRED, os.path.join(tempfile.gettempdir(), "gentoo-updater.lock")):
        try:
            fh = open(path, "w")
            return fh, path
        except OSError:
            continue
    raise OSError("could not open any lockfile location")


@contextlib.contextmanager
def single_instance():
    """Hold an exclusive, non-blocking lock for the duration of the block.

    Raises AlreadyRunning if another process already holds it."""
    fh, path = _open_lockfile()
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AlreadyRunning(path) from exc
        try:
            yield path
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()

"""Advisory flock so two update runs can't step on each other. Released on
exit or crash."""

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
    # /run/lock first; fall back to the temp dir when we're not root (dry-run).
    for path in (_PREFERRED, os.path.join(tempfile.gettempdir(), "gentoo-updater.lock")):
        try:
            fh = open(path, "w")
            return fh, path
        except OSError:
            continue
    raise OSError("could not open any lockfile location")


@contextlib.contextmanager
def single_instance():
    """Hold the lock for the block. Raises AlreadyRunning if someone else has it."""
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

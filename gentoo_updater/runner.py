"""Runs system commands. capture() collects output, stream() lets it hit the
terminal, interactive() is stream() with a name that says "this grabs stdin".
dry_run short-circuits anything that would change the system."""

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass

from . import ui

_log = logging.getLogger("gentoo_updater.runner")


@dataclass
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def _c_locale_env() -> dict[str, str]:
    # We grep portage/eselect output for strings like "no broken" and
    # "Total: 0 packages". On a non-English box those are localised and the
    # checks silently fail, so force C locale + no colour for parsed commands.
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    env["NOCOLOR"] = "true"
    env["NO_COLOR"] = "1"
    return env


class CommandRunner:
    def __init__(self, *, dry_run: bool = False, use_sudo: bool = True):
        self.dry_run = dry_run
        self.use_sudo = use_sudo

    def _prep(self, cmd: list[str]) -> list[str]:
        if self.use_sudo and self._needs_root(cmd):
            return ["sudo", *cmd]
        return cmd

    @staticmethod
    def _needs_root(cmd: list[str]) -> bool:
        # Does this command need root *and* change the system? Both questions
        # have the same answer here, and one predicate answers both: whether to
        # sudo, and whether dry_run should skip it. It has to look at
        # subcommands, not just the program, so that read-only calls (emerge
        # --pretend, snapper list, find) still run under --dry-run while their
        # mutating siblings (snapper create, btrfs subvolume snapshot) don't.
        prog = cmd[0]
        if prog == "emerge":
            readonly = {"-p", "--pretend", "-s", "--search", "-S", "--searchdesc"}
            return not any(a in readonly for a in cmd)
        if prog in ("emaint", "dispatch-conf", "revdep-rebuild", "eix-update",
                    "mkdir"):
            return True
        if prog == "snapper":
            return any(sub in cmd for sub in ("create", "rollback", "delete",
                                              "modify"))
        if prog == "btrfs":
            return "snapshot" in cmd or "delete" in cmd
        return False  # eselect news, find, findmnt, ... fine as a normal user

    def capture(self, cmd: list[str]) -> CommandResult:
        full = self._prep(cmd)
        if self.dry_run and self._needs_root(cmd):
            ui.dim(f"[dry-run] would run: {' '.join(full)}")
            return CommandResult(returncode=0)
        _log.debug("capture: %s", " ".join(full))
        start = time.time()
        try:
            # Drop the spinner while the child runs, same as stream(): a live
            # refresh thread competing with a long subprocess is asking for
            # trouble, and it lets the child own the terminal if it wants stdin.
            with ui.suspend():
                proc = subprocess.run(full, capture_output=True, text=True,
                                      check=False, env=_c_locale_env())
        except FileNotFoundError as exc:
            _log.debug("capture: %s -> not found", full[0])
            return CommandResult(returncode=127, stderr=str(exc))
        _log.debug("capture done: rc=%d (%.1fs) out=%dB err=%dB",
                   proc.returncode, time.time() - start,
                   len(proc.stdout), len(proc.stderr))
        if proc.returncode != 0 and proc.stderr.strip():
            _log.debug("stderr: %s", proc.stderr.strip()[:2000])
        return CommandResult(proc.returncode, proc.stdout, proc.stderr)

    def stream(self, cmd: list[str]) -> CommandResult:
        full = self._prep(cmd)
        if self.dry_run and self._needs_root(cmd):
            ui.dim(f"[dry-run] would run: {' '.join(full)}")
            return CommandResult(returncode=0)
        ui.dim(f"$ {' '.join(full)}")
        _log.debug("stream: %s", " ".join(full))
        start = time.time()
        try:
            # ui.suspend() drops the spinner so the child gets the terminal to
            # itself, then puts it back.
            with ui.suspend():
                proc = subprocess.run(full, check=False)
        except FileNotFoundError as exc:
            _log.debug("stream: %s -> not found", full[0])
            return CommandResult(returncode=127, stderr=str(exc))
        _log.debug("stream done: rc=%d (%.1fs)", proc.returncode,
                   time.time() - start)
        return CommandResult(proc.returncode)

    def interactive(self, cmd: list[str]) -> CommandResult:
        return self.stream(cmd)

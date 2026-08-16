"""Thin wrapper around subprocess for running system commands.

Three modes:
  - capture():     run, collect stdout/stderr, return it (for parsing)
  - stream():      run, let output go straight to the terminal (for long builds)
  - interactive(): hand the terminal over entirely (for dispatch-conf etc.)

A dry_run flag lets the whole tool be exercised without touching the system,
which is invaluable for development and for a --dry-run user flag.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

from . import ui


@dataclass
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def _c_locale_env() -> dict[str, str]:
    """Environment for commands whose stdout we parse.

    Portage/eselect/revdep-rebuild localise their output, so a box set to a
    non-English locale would silently break our substring checks ("no broken",
    "Total: 0 packages", ...). Pin LC_ALL=C and suppress colour so the parsers
    see stable, ASCII output regardless of the user's environment.
    """
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    env["NOCOLOR"] = "true"      # portage
    env["NO_COLOR"] = "1"        # de-facto cross-tool standard
    return env


class CommandRunner:
    def __init__(self, *, dry_run: bool = False, use_sudo: bool = True):
        self.dry_run = dry_run
        self.use_sudo = use_sudo

    def _prep(self, cmd: list[str]) -> list[str]:
        # Read-only introspection commands don't need root; mutating ones do.
        # We keep this simple: caller-provided commands that need root get sudo
        # prepended unless we're already root. Commands that are always safe
        # read-only (find, eselect news count, emerge -p) are marked via the
        # _needs_root heuristic below.
        if self.use_sudo and self._needs_root(cmd):
            return ["sudo", *cmd]
        return cmd

    @staticmethod
    def _needs_root(cmd: list[str]) -> bool:
        """Whether a command both needs root AND mutates the system.

        This single predicate drives two things: prepending sudo, and the
        --dry-run guard (a command that mutates is exactly one we must not run
        in a dry run). It is deliberately subcommand-aware so that read-only
        introspection -- `emerge --pretend`, `snapper list`, `find` -- is
        neither sudo'd nor suppressed under --dry-run, while the mutating
        siblings (`snapper create`, `btrfs subvolume snapshot`, `mkdir`) are.
        """
        prog = cmd[0]
        # emerge needs root unless it's a pretend/search invocation
        if prog == "emerge":
            readonly = {"-p", "--pretend", "-s", "--search", "-S", "--searchdesc"}
            return not any(a in readonly for a in cmd)
        if prog in ("emaint", "dispatch-conf", "revdep-rebuild", "eix-update",
                    "mkdir"):
            return True
        # snapshot backends: only the state-changing subcommands mutate. Listing
        # and config discovery must still run under --dry-run so `gup --dry-run`
        # and `gup rollback` can see the real snapshot state.
        if prog == "snapper":
            return any(sub in cmd for sub in ("create", "rollback", "delete",
                                              "modify"))
        if prog == "btrfs":
            return "snapshot" in cmd or "delete" in cmd
        # eselect news read/count and find/findmnt are safe as an unprivileged user
        return False

    def capture(self, cmd: list[str]) -> CommandResult:
        full = self._prep(cmd)
        if self.dry_run and self._needs_root(cmd):
            ui.dim(f"[dry-run] would run: {' '.join(full)}")
            return CommandResult(returncode=0, stdout="", stderr="")
        try:
            proc = subprocess.run(
                full, capture_output=True, text=True, check=False,
                env=_c_locale_env(),
            )
            return CommandResult(proc.returncode, proc.stdout, proc.stderr)
        except FileNotFoundError as exc:
            return CommandResult(returncode=127, stderr=str(exc))

    def stream(self, cmd: list[str]) -> CommandResult:
        full = self._prep(cmd)
        if self.dry_run and self._needs_root(cmd):
            ui.dim(f"[dry-run] would run: {' '.join(full)}")
            return CommandResult(returncode=0)
        ui.dim(f"$ {' '.join(full)}")
        try:
            # Hand the terminal to the child: drop the live panel (if any) so its
            # output isn't fighting a pinned dashboard, then restore it after.
            with ui.suspend():
                proc = subprocess.run(full, check=False)
            return CommandResult(proc.returncode)
        except FileNotFoundError as exc:
            return CommandResult(returncode=127, stderr=str(exc))

    def interactive(self, cmd: list[str]) -> CommandResult:
        """Same as stream in practice, but semantically flags that the command
        takes over stdin (dispatch-conf, eselect news read)."""
        return self.stream(cmd)

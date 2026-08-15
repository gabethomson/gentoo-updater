"""Command-line interface.

Subcommands:
  update    run the full pipeline (default)
  plan      dry-run: sync-free, just show what would be updated
  verify    run only the post-update health checks
  news      show/read pending news

Global flags let you pick interactive vs unattended and toggle safety steps.
"""

from __future__ import annotations

import argparse
import sys

from .runner import CommandRunner
from .snapshot import SnapshotManager
from .updater import Updater
from .lockfile import single_instance, AlreadyRunning
from . import ui


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gup",
        description="A safer, structured Gentoo world-update tool.",
    )

    mode = p.add_argument_group("mode")
    mode.add_argument(
        "-y", "--yes", action="store_true",
        help="assume yes to all prompts (unattended); implies non-interactive apply",
    )
    mode.add_argument(
        "--non-interactive", action="store_true",
        help="never prompt; use safe defaults and only report where a prompt "
             "would have been (does NOT auto-apply unless --yes is also given)",
    )
    mode.add_argument(
        "--dry-run", action="store_true",
        help="never execute mutating commands; print what would run",
    )

    safety = p.add_argument_group("safety toggles")
    safety.add_argument("--no-snapshot", action="store_true",
                        help="skip the pre-update btrfs/snapper snapshot")
    safety.add_argument("--no-sync", action="store_true",
                        help="skip repository sync (use current tree)")
    safety.add_argument("--no-sudo", action="store_true",
                        help="don't prepend sudo (use when already root)")

    p.add_argument(
        "command", nargs="?", default="update",
        choices=["update", "plan", "verify", "news", "rollback"],
        help="what to do (default: update)",
    )
    return p


def _make_updater(args) -> Updater:
    runner = CommandRunner(dry_run=args.dry_run, use_sudo=not args.no_sudo)
    snapshots = SnapshotManager(runner)
    interactive = not args.non_interactive and not args.yes
    return Updater(
        runner, snapshots,
        interactive=interactive,
        assume_yes=args.yes,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    updater = _make_updater(args)

    try:
        if args.command == "update":
            # Mutating run: hold the single-instance lock so we can't race
            # another gup (or ourselves) part-way through a world merge.
            try:
                with single_instance():
                    report = updater.run_all(
                        skip_snapshot=args.no_snapshot,
                        skip_sync=args.no_sync,
                    )
            except AlreadyRunning as exc:
                ui.error(str(exc))
                return 1
            return 1 if report.failed else 0

        if args.command == "rollback":
            try:
                with single_instance():
                    return updater.run_rollback()
            except AlreadyRunning as exc:
                ui.error(str(exc))
                return 1

        if args.command == "plan":
            # plan-only: no sync, no snapshot, no apply
            updater.report.add(updater.phase_preflight())
            ui.phase_header("plan")
            result = updater.phase_plan()
            updater.report.add(result)
            ui.show_summary(updater.report)
            return 0 if result.ok else 1

        if args.command == "verify":
            ui.phase_header("verify")
            result = updater.phase_verify()
            updater.report.add(result)
            ui.show_summary(updater.report)
            return 0 if result.ok else 1

        if args.command == "news":
            result = updater.phase_news()
            updater.report.add(result)
            ui.show_summary(updater.report)
            return 0 if result.ok else 1

    except KeyboardInterrupt:
        ui.error("Interrupted.")
        return 130

    return 0


if __name__ == "__main__":
    sys.exit(main())

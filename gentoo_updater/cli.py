"""Command-line interface.

Subcommands:
  update         run the full pipeline (default)
  plan           dry-run: sync-free, just show what would be updated
  verify         run only the post-update health checks
  news           show/read pending news
  rollback       restore a pre-update snapshot
  depclean       remove orphaned packages (opt-in, pretends first)
  install-schedule  set up unattended runs for your init (systemd/openrc/runit)
  install-timer  shortcut for 'install-schedule --init systemd'

Global flags let you pick interactive vs unattended and toggle safety steps.
Defaults can also be set in a config file (see config.py); explicit flags win.
"""

from __future__ import annotations

import argparse
import shutil
import sys

from .runner import CommandRunner
from .snapshot import SnapshotManager
from .updater import Updater
from .lockfile import single_instance, AlreadyRunning
from .config import load_config, NOTIFY_CHOICES
from .audit import AuditLog
from .notify import Notifier
from . import schedule
from . import ui


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gup",
        description="A safer, structured Gentoo world-update tool.",
    )

    # Behaviour flags default to None so we can tell "unset" (use config) from
    # "explicitly passed" (override config). store_true would force False.
    mode = p.add_argument_group("mode")
    mode.add_argument(
        "-y", "--yes", action="store_true", default=None,
        help="assume yes to all prompts (unattended); implies non-interactive apply",
    )
    mode.add_argument(
        "--non-interactive", action="store_true", default=None,
        help="never prompt; use safe defaults and only report where a prompt "
             "would have been (does NOT auto-apply unless --yes is also given)",
    )
    mode.add_argument(
        "--dry-run", action="store_true",
        help="never execute mutating commands; print what would run",
    )
    mode.add_argument(
        "--plain", action="store_true",
        help="disable the live phase dashboard; use plain linear output",
    )

    safety = p.add_argument_group("safety toggles")
    safety.add_argument("--no-snapshot", action="store_true", default=None,
                        help="skip the pre-update btrfs/snapper snapshot")
    safety.add_argument("--no-sync", action="store_true", default=None,
                        help="skip repository sync (use current tree)")
    safety.add_argument("--no-sudo", action="store_true", default=None,
                        help="don't prepend sudo (use when already root)")
    safety.add_argument("--depclean", action="store_true", default=None,
                        help="also run a depclean step (pretends first, asks "
                             "before removing anything)")
    safety.add_argument("--select", action="store_true", default=None,
                        help="interactively pick which pending packages to "
                             "update (the rest are passed to emerge --exclude)")

    extra = p.add_argument_group("config file")
    extra.add_argument("--config", metavar="PATH", default=None,
                       help="use only this config file (default: system + user)")
    extra.add_argument("--notify", choices=NOTIFY_CHOICES, default=None,
                       help="when to send a completion notification")
    extra.add_argument("--no-audit", action="store_true", default=None,
                       help="don't append a run record to the audit log")

    sched = p.add_argument_group("scheduling (install-schedule / install-timer)")
    sched.add_argument(
        "--init", choices=["auto", "systemd", "openrc", "runit", "cron"],
        default="auto",
        help="which init to target (default: auto-detect). openrc uses cron.",
    )
    sched.add_argument(
        "--schedule", metavar="PERIOD", default=schedule.DEFAULT_PERIOD,
        help="how often to run: daily/weekly/monthly (systemd also accepts any "
             "OnCalendar= expression). Default: daily",
    )

    p.add_argument(
        "command", nargs="?", default="update",
        choices=["update", "plan", "verify", "news", "rollback", "depclean",
                 "install-schedule", "install-timer"],
        help="what to do (default: update)",
    )
    return p


def _effective_config(args):
    """Layer the config file(s) under the explicitly-passed CLI flags."""
    paths = [args.config] if args.config else None
    cfg = load_config(paths)
    overrides = {
        "yes": args.yes,
        "non_interactive": args.non_interactive,
        "no_snapshot": args.no_snapshot,
        "no_sync": args.no_sync,
        "no_sudo": args.no_sudo,
        "depclean": args.depclean,
        "select": args.select,
        "notify": args.notify,
        # --no-audit is an explicit "off"; leave audit alone otherwise.
        "audit": False if args.no_audit else None,
    }
    return cfg.merged_with_cli(overrides)


def _make_updater(args, cfg) -> Updater:
    runner = CommandRunner(dry_run=args.dry_run, use_sudo=not cfg.no_sudo)
    snapshots = SnapshotManager(runner)
    interactive = not cfg.non_interactive and not cfg.yes
    # A dry run must stay side-effect-free: no audit writes, no notifications.
    audit_log = None if (args.dry_run or not cfg.audit) else AuditLog(cfg.audit_path)
    notifier = None if (args.dry_run or cfg.notify == "never") else Notifier(cfg)
    return Updater(
        runner, snapshots,
        interactive=interactive,
        assume_yes=cfg.yes,
        low_space_gib=cfg.low_space_gib,
        include_depclean=cfg.depclean,
        select=cfg.select,
        audit_log=audit_log,
        notifier=notifier,
    )


def _print_plan_files(plan_obj) -> None:
    for path, text in plan_obj.files.items():
        ui.phase_header(path)
        ui.info(text)


def _install_schedule(args, *, backend: str | None = None) -> int:
    """Set up a scheduled unattended run for the target init. `backend` forces a
    specific one (install-timer -> systemd); otherwise we resolve it from
    --init, auto-detecting when asked."""
    exec_path = shutil.which("gup") or "gup"
    if backend is None:
        init = args.init
        if init == "auto":
            init = schedule.detect_init()
            if init is None:
                ui.error("Could not detect the init system. Re-run with "
                         "--init systemd|openrc|runit|cron.")
                return 1
            ui.info(f"Detected init: {init}")
        try:
            backend = schedule.backend_for_init(init)
        except ValueError as exc:
            ui.error(str(exc))
            return 1

    plan_obj = schedule.plan(backend, exec_path=exec_path, period=args.schedule)

    if args.dry_run:
        _print_plan_files(plan_obj)
        ui.hint("(dry-run) would install the above, then activate with:")
        for step in plan_obj.enable_hint:
            ui.hint("  " + step)
        return 0

    try:
        written = schedule.install(plan_obj)
    except OSError as exc:
        ui.error(f"Could not write files ({exc}). Re-run as root, or create "
                 "them by hand:")
        _print_plan_files(plan_obj)
        return 1

    for path in written:
        ui.info(f"wrote {path}")
    ui.hint("Activate with:")
    for step in plan_obj.enable_hint:
        ui.hint("  " + step)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ui.set_plain(args.plain)

    # Scheduling setup doesn't touch portage and needs no config/updater.
    if args.command == "install-schedule":
        return _install_schedule(args)
    if args.command == "install-timer":
        return _install_schedule(args, backend=schedule.BACKEND_SYSTEMD)

    cfg = _effective_config(args)
    updater = _make_updater(args, cfg)

    try:
        if args.command == "update":
            # Mutating run: hold the single-instance lock so we can't race
            # another gup (or ourselves) part-way through a world merge.
            try:
                with single_instance():
                    report = updater.run_all(
                        skip_snapshot=cfg.no_snapshot,
                        skip_sync=cfg.no_sync,
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

        if args.command == "depclean":
            try:
                with single_instance():
                    return updater.run_depclean()
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

"""Sets up scheduled runs per init. Only OpenRC and runit differ from systemd,
and neither has a timer of its own, so:

  systemd -> .service + .timer
  cron    -> /etc/cron.<period>/gentoo-updater  (also what OpenRC uses)
  runit   -> /etc/sv/gentoo-updater/run, a run-then-sleep loop

Rendering is text; install() writes the files."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field

from . import systemd

INIT_SYSTEMD = "systemd"
INIT_OPENRC = "openrc"
INIT_RUNIT = "runit"

BACKEND_SYSTEMD = "systemd"
BACKEND_CRON = "cron"
BACKEND_RUNIT = "runit"

# Unattended invocation: assume-yes, and --no-sudo because every backend runs
# the job as root already (system timer / cron.daily / runsv).
DEFAULT_ARGS = "-y --no-sudo"
DEFAULT_PERIOD = "daily"

# How each named period maps to a sleep length (runit) and a run-parts dir (cron).
PERIOD_SECONDS = {"daily": 86400, "weekly": 604800, "monthly": 2592000}
CRON_DIRS = {
    "daily": "/etc/cron.daily",
    "weekly": "/etc/cron.weekly",
    "monthly": "/etc/cron.monthly",
}
RUNIT_DIR = "/etc/sv/gentoo-updater"


def detect_init(*, comm_path: str = "/proc/1/comm", which=shutil.which) -> str | None:
    # Sniff pid 1 and the PATH. INIT_* or None if we can't tell.
    comm = ""
    try:
        with open(comm_path, "r", encoding="utf-8") as fh:
            comm = fh.read().strip()
    except OSError:
        pass
    if comm == "systemd" or which("systemctl"):
        return INIT_SYSTEMD
    if comm in ("runit", "runsvdir", "runsv") or which("runsvdir"):
        return INIT_RUNIT
    if which("openrc") or which("rc-update") or which("openrc-run"):
        return INIT_OPENRC
    return None


def backend_for_init(init: str) -> str:
    if init == INIT_SYSTEMD:
        return BACKEND_SYSTEMD
    if init == INIT_RUNIT:
        return BACKEND_RUNIT
    if init in (INIT_OPENRC, BACKEND_CRON):
        return BACKEND_CRON
    raise ValueError(f"no scheduling backend for init {init!r}")


@dataclass
class SchedulePlan:
    # Files to write, which of them need +x, and how to turn it on.
    backend: str
    files: dict[str, str] = field(default_factory=dict)
    executable: set[str] = field(default_factory=set)
    enable_hint: list[str] = field(default_factory=list)


def cron_script(exec_path: str = "gup", args: str = DEFAULT_ARGS) -> str:
    return (
        "#!/bin/sh\n"
        "# Installed by gentoo-updater. Runs an unattended world update.\n"
        f"exec {exec_path} {args}\n"
    )


def runit_run_script(exec_path: str = "gup", args: str = DEFAULT_ARGS,
                     period: str = DEFAULT_PERIOD) -> str:
    secs = PERIOD_SECONDS.get(period, PERIOD_SECONDS[DEFAULT_PERIOD])
    return (
        "#!/bin/sh\n"
        f"# runit service: run gentoo-updater, then sleep ~{period}, supervised\n"
        "# by runsv. Redirect stderr into stdout so svlogd can capture it.\n"
        "exec 2>&1\n"
        "while true; do\n"
        f"    {exec_path} {args}\n"
        f"    sleep {secs}\n"
        "done\n"
    )


def plan(backend: str, *, exec_path: str = "gup", args: str = DEFAULT_ARGS,
         period: str = DEFAULT_PERIOD) -> SchedulePlan:
    # Render files + activation steps; no disk writes here.
    if backend == BACKEND_SYSTEMD:
        files = {
            os.path.join(systemd.DEFAULT_DEST, name): text
            for name, text in systemd.unit_files(
                exec_path=exec_path, extra_args=args, on_calendar=period).items()
        }
        return SchedulePlan(
            backend=backend, files=files,
            enable_hint=[
                "systemctl daemon-reload",
                f"systemctl enable --now {systemd.TIMER_NAME}",
            ],
        )
    if backend == BACKEND_CRON:
        path = os.path.join(CRON_DIRS.get(period, CRON_DIRS[DEFAULT_PERIOD]),
                            "gentoo-updater")
        return SchedulePlan(
            backend=backend, files={path: cron_script(exec_path, args)},
            executable={path},
            enable_hint=[
                f"chmod +x {path}  # (done for you if installed)",
                "ensure a cron daemon is enabled (e.g. cronie) so cron.* runs",
            ],
        )
    if backend == BACKEND_RUNIT:
        run_path = os.path.join(RUNIT_DIR, "run")
        return SchedulePlan(
            backend=backend,
            files={run_path: runit_run_script(exec_path, args, period)},
            executable={run_path},
            enable_hint=[
                f"ln -s {RUNIT_DIR} /var/service/       # or your runsvdir path",
                "(the service starts under runsv within a few seconds)",
            ],
        )
    raise ValueError(f"unknown scheduling backend {backend!r}")


def install(plan_obj: SchedulePlan) -> list[str]:
    # Write the files (+x where asked). Raises OSError (PermissionError when not
    # root); the caller prints them instead.
    written: list[str] = []
    for path, text in plan_obj.files.items():
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        if path in plan_obj.executable:
            os.chmod(path, 0o755)
        written.append(path)
    return written

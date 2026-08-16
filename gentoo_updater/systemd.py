"""Generate (and optionally install) a systemd service + timer for unattended runs.

Rather than ask the user to hand-write unit files, `gup install-timer` renders
them from the installed `gup` path and drops them in /etc/systemd/system. The
service runs the pipeline unattended; because a system unit already runs as root,
it passes --no-sudo. The rendering is pure text so it's testable without touching
the filesystem.
"""

from __future__ import annotations

import os

SERVICE_NAME = "gentoo-updater.service"
TIMER_NAME = "gentoo-updater.timer"
DEFAULT_DEST = "/etc/systemd/system"
# systemd OnCalendar expression; `daily` == 00:00, and Persistent catches up a
# run missed while the machine was off.
DEFAULT_SCHEDULE = "daily"


def service_unit(exec_path: str = "gup", extra_args: str = "-y --no-sudo") -> str:
    exec_start = f"{exec_path} {extra_args}".rstrip()
    return f"""\
[Unit]
Description=Gentoo world update (gentoo-updater)
Documentation=https://github.com/kenny/gentoo-updater
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart={exec_start}
# updates can be long; don't let systemd kill a big compile
TimeoutStartSec=infinity
Nice=10
IOSchedulingClass=idle
"""


def timer_unit(on_calendar: str = DEFAULT_SCHEDULE) -> str:
    return f"""\
[Unit]
Description=Run gentoo-updater on a schedule
Documentation=https://github.com/kenny/gentoo-updater

[Timer]
OnCalendar={on_calendar}
Persistent=true
RandomizedDelaySec=30m

[Install]
WantedBy=timers.target
"""


def unit_files(exec_path: str = "gup", extra_args: str = "-y --no-sudo",
               on_calendar: str = DEFAULT_SCHEDULE) -> dict[str, str]:
    """Map of {filename: contents} for the service and timer."""
    return {
        SERVICE_NAME: service_unit(exec_path, extra_args),
        TIMER_NAME: timer_unit(on_calendar),
    }


def install_units(files: dict[str, str], dest: str = DEFAULT_DEST) -> list[str]:
    """Write unit files into `dest`, returning the paths written.

    Raises OSError (e.g. PermissionError when not root) so the caller can fall
    back to printing the units and manual instructions.
    """
    written: list[str] = []
    os.makedirs(dest, exist_ok=True)
    for name, text in files.items():
        path = os.path.join(dest, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        written.append(path)
    return written

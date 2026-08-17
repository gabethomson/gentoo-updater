"""Renders the systemd service + timer for unattended runs. Text only, so it's
easy to test. The service runs as root already, hence --no-sudo."""

from __future__ import annotations

SERVICE_NAME = "gentoo-updater.service"
TIMER_NAME = "gentoo-updater.timer"
DEFAULT_DEST = "/etc/systemd/system"
DEFAULT_SCHEDULE = "daily"  # OnCalendar value; Persistent catches up missed runs


def service_unit(exec_path: str = "gup", extra_args: str = "-y --no-sudo") -> str:
    exec_start = f"{exec_path} {extra_args}".rstrip()
    return f"""\
[Unit]
Description=Gentoo world update (gentoo-updater)
Documentation=https://github.com/gabethomson/gentoo-updater
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
Documentation=https://github.com/gabethomson/gentoo-updater

[Timer]
OnCalendar={on_calendar}
Persistent=true
RandomizedDelaySec=30m

[Install]
WantedBy=timers.target
"""


def unit_files(exec_path: str = "gup", extra_args: str = "-y --no-sudo",
               on_calendar: str = DEFAULT_SCHEDULE) -> dict[str, str]:
    return {
        SERVICE_NAME: service_unit(exec_path, extra_args),
        TIMER_NAME: timer_unit(on_calendar),
    }

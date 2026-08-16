"""Desktop / email notification when a run finishes. Mostly for unattended runs
where nobody's watching the terminal. `send` is injectable so tests don't shell
out."""

from __future__ import annotations

import shutil
import subprocess


def should_notify(report, trigger: str) -> bool:
    if trigger == "never":
        return False
    if trigger == "always":
        return True
    if trigger == "failure":
        return report.failed
    if trigger == "reboot":
        return report.failed or bool(report.reboot_pkgs)
    return False


def build_notification(report, *, command: str = "update") -> tuple[str, str]:
    if report.failed:
        failed = [p.name for p in report.phases if not p.ok and not p.skipped]
        title = "gentoo-updater: update FAILED"
        body = "Failed phase(s): " + (", ".join(failed) or "unknown")
    else:
        total = report.plan.total if report.plan else 0
        title = "gentoo-updater: update OK"
        body = (f"{total} package(s) updated" if total
                else "System already up to date")
    if report.reboot_pkgs:
        body += "\nReboot recommended: " + ", ".join(report.reboot_pkgs)
    if report.snapshot_id:
        body += f"\nSnapshot: {report.snapshot_id}"
    return title, body


def _default_send(argv: list[str], *, input_text: str | None = None) -> int:
    try:
        proc = subprocess.run(argv, input=input_text, text=True,
                              capture_output=True, check=False)
        return proc.returncode
    except OSError:
        return 127


class Notifier:
    def __init__(self, config, *, send=None, which=shutil.which):
        self.config = config
        self._send = send or _default_send
        self._which = which

    def maybe_notify(self, report, *, command: str = "update") -> list[str]:
        # Returns the channels that actually fired.
        if not should_notify(report, self.config.notify):
            return []
        title, body = build_notification(report, command=command)
        used: list[str] = []
        if self.config.notify_desktop and self._notify_desktop(title, body):
            used.append("desktop")
        if self.config.notify_email and self._notify_email(title, body):
            used.append("email")
        return used

    def _notify_desktop(self, title: str, body: str) -> bool:
        if self._which("notify-send") is None:
            return False
        return self._send(["notify-send", title, body]) == 0

    def _notify_email(self, title: str, body: str) -> bool:
        to = self.config.notify_email
        if self._which("sendmail"):
            msg = f"To: {to}\nSubject: {title}\n\n{body}\n"
            return self._send(["sendmail", "-t"], input_text=msg) == 0
        if self._which("mail"):
            return self._send(["mail", "-s", title, to], input_text=body) == 0
        return False

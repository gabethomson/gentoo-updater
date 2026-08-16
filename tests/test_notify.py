"""Tests for notification triggers, message building, and channel dispatch.

Delivery is faked (no shelling out) via the injectable `send`/`which` seams.

Run with:  python -m pytest   (or standalone: python tests/test_notify.py)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gentoo_updater import notify
from gentoo_updater.config import Config
from gentoo_updater.updater import UpdateReport, PhaseResult
from gentoo_updater.parse import PretendPlan, PkgChange


def _ok_report(reboot=None):
    r = UpdateReport()
    r.add(PhaseResult("apply", ok=True, detail="world updated"))
    r.plan = PretendPlan(changes=[
        PkgChange(atom="a/b-1", name="a/b", kind="ebuild", is_new=False,
                  is_upgrade=True, is_rebuild=False, needs_keyword=False,
                  from_binary=False)
    ])
    r.reboot_pkgs = reboot or []
    return r


def _failed_report():
    r = UpdateReport()
    r.add(PhaseResult("apply", ok=False, detail="emerge failed"))
    return r


class ShouldNotify(unittest.TestCase):
    def test_never_and_always(self):
        self.assertFalse(notify.should_notify(_ok_report(), "never"))
        self.assertTrue(notify.should_notify(_ok_report(), "always"))

    def test_failure_only_on_failure(self):
        self.assertFalse(notify.should_notify(_ok_report(), "failure"))
        self.assertTrue(notify.should_notify(_failed_report(), "failure"))

    def test_reboot_trigger_covers_reboot_and_failure(self):
        self.assertFalse(notify.should_notify(_ok_report(), "reboot"))
        self.assertTrue(notify.should_notify(
            _ok_report(reboot=["sys-libs/glibc"]), "reboot"))
        self.assertTrue(notify.should_notify(_failed_report(), "reboot"))


class BuildNotification(unittest.TestCase):
    def test_success_message(self):
        title, body = notify.build_notification(_ok_report(reboot=["sys-libs/glibc"]))
        self.assertIn("OK", title)
        self.assertIn("1 package", body)
        self.assertIn("Reboot recommended", body)

    def test_failure_message_lists_phase(self):
        title, body = notify.build_notification(_failed_report())
        self.assertIn("FAILED", title)
        self.assertIn("apply", body)


class Dispatch(unittest.TestCase):
    def _notifier(self, cfg, present):
        calls = []

        def send(argv, input_text=None):
            calls.append((argv, input_text))
            return 0

        which = lambda t: (f"/usr/bin/{t}" if t in present else None)
        return notify.Notifier(cfg, send=send, which=which), calls

    def test_no_notification_when_trigger_never(self):
        cfg = Config(notify="never")
        n, calls = self._notifier(cfg, {"notify-send"})
        self.assertEqual(n.maybe_notify(_failed_report()), [])
        self.assertEqual(calls, [])

    def test_desktop_used_when_available(self):
        cfg = Config(notify="always", notify_desktop=True, notify_email="")
        n, calls = self._notifier(cfg, {"notify-send"})
        used = n.maybe_notify(_ok_report())
        self.assertEqual(used, ["desktop"])
        self.assertEqual(calls[0][0][0], "notify-send")

    def test_email_via_sendmail_gets_headers(self):
        cfg = Config(notify="failure", notify_desktop=False,
                     notify_email="root@localhost")
        n, calls = self._notifier(cfg, {"sendmail"})
        used = n.maybe_notify(_failed_report())
        self.assertEqual(used, ["email"])
        argv, body = calls[0]
        self.assertEqual(argv, ["sendmail", "-t"])
        self.assertIn("To: root@localhost", body)
        self.assertIn("Subject:", body)

    def test_email_falls_back_to_mail_command(self):
        cfg = Config(notify="always", notify_desktop=False,
                     notify_email="root@localhost")
        n, calls = self._notifier(cfg, {"mail"})
        used = n.maybe_notify(_ok_report())
        self.assertEqual(used, ["email"])
        self.assertEqual(calls[0][0][:2], ["mail", "-s"])

    def test_no_channels_when_tools_absent(self):
        cfg = Config(notify="always", notify_desktop=True,
                     notify_email="root@localhost")
        n, calls = self._notifier(cfg, set())  # neither notify-send nor mailer
        self.assertEqual(n.maybe_notify(_ok_report()), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

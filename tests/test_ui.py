"""Tests for the live-status helpers in ui.py.

They run without a real terminal, so the spinner stays inactive; the phase-line
output is checked by forcing a StringIO console. Rich assertions are guarded on
rich being importable.
"""

import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gentoo_updater import ui
from gentoo_updater.updater import UpdateReport, PhaseResult


class Fmt(unittest.TestCase):
    def test_minutes_seconds(self):
        self.assertEqual(ui._fmt(0), "00:00")
        self.assertEqual(ui._fmt(75), "01:15")

    def test_hours(self):
        self.assertEqual(ui._fmt(3661), "1:01:01")


class InactiveWithoutTty(unittest.TestCase):
    """In tests stdout isn't a terminal, so the spinner never starts and the
    phase calls are all no-ops that must not raise."""

    def setUp(self):
        ui.set_plain(False)

    def tearDown(self):
        with contextlib.redirect_stdout(io.StringIO()):
            ui.end_run(UpdateReport())
        ui.set_plain(False)

    def test_lifecycle_is_a_noop_and_does_not_start_a_spinner(self):
        ui.begin_run(["preflight", "sync", "apply"])
        self.assertFalse(ui._active)
        self.assertIsNone(ui._anim_thread)  # no animator thread without a tty
        ui.phase_start("preflight")
        ui.phase_done(PhaseResult("preflight", ok=True, detail="ok"))
        with ui.suspend():  # must not raise, must not start a spinner
            pass
        self.assertIsNone(ui._anim_thread)

    def test_end_run_resets_state(self):
        ui.begin_run(["preflight"])
        with contextlib.redirect_stdout(io.StringIO()):
            ui.end_run(UpdateReport())
        self.assertFalse(ui._active)
        self.assertIsNone(ui._current)


@unittest.skipUnless(ui._HAVE_RICH, "rich not installed")
class PhaseLine(unittest.TestCase):
    """Force the spinner active with a StringIO console and check the permanent
    line printed for each finished phase."""

    def _line(self, result):
        from rich.console import Console
        buf = io.StringIO()
        orig_console, orig_active = ui._console, ui._active
        ui._console = Console(file=buf, width=80)
        ui._active = True
        try:
            ui.phase_done(result)
        finally:
            ui._console, ui._active = orig_console, orig_active
        return buf.getvalue()

    def test_ok_line(self):
        out = self._line(PhaseResult("plan", ok=True, detail="17 pending"))
        self.assertIn("OK", out)
        self.assertIn("plan", out)
        self.assertIn("17 pending", out)

    def test_fail_line(self):
        self.assertIn("XX", self._line(PhaseResult("sync", ok=False, detail="boom")))

    def test_skip_line(self):
        out = self._line(PhaseResult("snapshot", ok=True, skipped=True, detail="x"))
        self.assertIn("--", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)

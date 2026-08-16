"""Tests for the live-dashboard state machine and helpers in ui.py.

These exercise the plain/non-tty path (dashboard inactive) plus the pure render
so they run with or without a real terminal. Rendering assertions are guarded on
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


def _clear_ui():
    """Reset dashboard module state, swallowing the summary print."""
    with contextlib.redirect_stdout(io.StringIO()):
        ui.end_run(UpdateReport())


class Fmt(unittest.TestCase):
    def test_minutes_seconds(self):
        self.assertEqual(ui._fmt(0), "00:00")
        self.assertEqual(ui._fmt(75), "01:15")

    def test_hours(self):
        self.assertEqual(ui._fmt(3661), "1:01:01")


class DashboardState(unittest.TestCase):
    def setUp(self):
        ui.set_plain(False)

    def tearDown(self):
        _clear_ui()
        ui.set_plain(False)

    def test_begin_run_is_inactive_without_a_tty(self):
        # tests don't run on a terminal, so no live panel should start...
        ui.begin_run(["preflight", "sync", "apply"])
        self.assertIsNone(ui._live)
        # ...but the phase list is still tracked for state transitions
        self.assertEqual([r["name"] for r in ui._phases],
                         ["preflight", "sync", "apply"])
        self.assertTrue(all(r["status"] == "pending" for r in ui._phases))

    def test_phase_transitions_update_state(self):
        ui.begin_run(["preflight", "sync"])
        ui.phase_start("preflight")
        self.assertEqual(ui._row("preflight")["status"], "running")
        ui.phase_done(PhaseResult("preflight", ok=True, detail="all good"))
        self.assertEqual(ui._row("preflight")["status"], "ok")
        self.assertEqual(ui._row("preflight")["detail"], "all good")
        ui.phase_done(PhaseResult("sync", ok=False, detail="boom"))
        self.assertEqual(ui._row("sync")["status"], "fail")
        ui.phase_done(PhaseResult("sync", ok=True, skipped=True, detail="skip"))
        self.assertEqual(ui._row("sync")["status"], "skip")

    def test_suspend_is_a_noop_when_inactive(self):
        ui.begin_run(["preflight"])
        with ui.suspend():
            pass  # must not raise, must not start a live
        self.assertIsNone(ui._live)

    def test_end_run_clears_state(self):
        ui.begin_run(["preflight"])
        out = io.StringIO()
        with __import__("contextlib").redirect_stdout(out):
            ui.end_run(UpdateReport())
        self.assertEqual(ui._phases, [])
        self.assertIsNone(ui._live)


@unittest.skipUnless(ui._HAVE_RICH, "rich not installed")
class Render(unittest.TestCase):
    def tearDown(self):
        _clear_ui()

    def test_render_shows_phase_names_and_states(self):
        from rich.console import Console
        ui.begin_run(["preflight", "sync", "apply"])
        ui.phase_done(PhaseResult("preflight", ok=True, detail="ok"))
        ui.phase_start("sync")
        buf = io.StringIO()
        Console(file=buf, width=80).print(ui._render_dashboard())
        out = buf.getvalue()
        for name in ("preflight", "sync", "apply", "gentoo-updater", "elapsed"):
            self.assertIn(name, out)


if __name__ == "__main__":
    unittest.main(verbosity=2)

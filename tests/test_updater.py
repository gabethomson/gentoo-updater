"""Tests for the update orchestration (updater.py) and the runner's dry-run guard.

These are the risky, stateful parts of the tool -- the phase pipeline and the
"never mutate under --dry-run" promise -- so we pin their behaviour with fakes
rather than touching a real system.

Run with:  python -m pytest   (or standalone: python tests/test_updater.py)
"""

import io
import os
import sys
import contextlib
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gentoo_updater.runner import CommandResult, CommandRunner
from gentoo_updater.updater import Updater, HIGH_RISK_ATOMS


# --- test doubles -------------------------------------------------------

# A pretend plan with something to do (one upgrade), used for happy-path runs.
PRETEND_NONEMPTY = (
    "These are the packages that would be merged, in order:\n\n"
    "[ebuild     U   ] app-editors/vim-9.1.0  1234 KiB\n\n"
    "Total: 1 package, Size of downloads: 1,234 KiB\n"
)

# A pretend plan with nothing pending.
PRETEND_EMPTY = "Calculating dependencies... done!\n\nTotal: 0 packages, Size of downloads: 0 KiB\n"

# What `emerge -p @preserved-rebuild` prints when nothing needs rebuilding.
PRESERVED_CLEAN = "Calculating dependencies... done!\n\nTotal: 0 packages, Size of downloads: 0 KiB\n"


class FakeRunner:
    """Stand-in for CommandRunner that returns canned results and records calls.

    `handler(cmd) -> CommandResult | None` lets a test override the result for a
    given command; returning None (the default) yields a successful empty result.
    """

    def __init__(self, handler=None):
        self._handler = handler or (lambda cmd: None)
        self.calls: list[list[str]] = []

    def _result(self, cmd):
        self.calls.append(list(cmd))
        res = self._handler(cmd)
        return res if res is not None else CommandResult(returncode=0, stdout="")

    def capture(self, cmd):
        return self._result(cmd)

    def stream(self, cmd):
        return self._result(cmd)

    def interactive(self, cmd):
        return self._result(cmd)


class FakeSnapshots:
    def __init__(self, available=False, snapshots=None):
        self.available = available
        self.created: list[str] = []
        self.rolled_back: list[str] = []
        self._snapshots = snapshots or []

    def create(self, description):
        self.created.append(description)
        return "fake#1"

    def list_snapshots(self):
        return list(self._snapshots)

    def rollback(self, ident):
        self.rolled_back.append(ident)
        return f"rolled back to {ident}"


def _happy_handler(cmd):
    """Default command outcomes for a clean, successful run."""
    if cmd[:2] == ["emerge", "--pretend"]:
        return CommandResult(0, stdout=PRETEND_NONEMPTY)
    if cmd[:1] == ["emerge"] and "-p" in cmd and "@preserved-rebuild" in cmd:
        return CommandResult(0, stdout=PRESERVED_CLEAN)
    if cmd[:3] == ["eselect", "news", "count"]:
        return CommandResult(0, stdout="0\n")
    if cmd[:1] == ["find"]:
        return CommandResult(0, stdout="")  # no pending ._cfg files
    return None  # everything else: success, empty output


def _which_stub(present):
    """Return a shutil.which replacement: tools in `present` resolve, others don't."""
    return lambda tool: (f"/usr/bin/{tool}" if tool in present else None)


def make_updater(handler=_happy_handler, *, snapshots_available=False,
                 snapshots=None, interactive=False, assume_yes=True):
    runner = FakeRunner(handler)
    snaps = FakeSnapshots(available=snapshots_available, snapshots=snapshots)
    updater = Updater(runner, snaps, interactive=interactive, assume_yes=assume_yes)
    return updater, runner


@contextlib.contextmanager
def env(tools):
    """Patch tool discovery and isolate the elog scan so run_all is hermetic."""
    with mock.patch("gentoo_updater.updater.shutil.which",
                    side_effect=_which_stub(tools)), \
         mock.patch("gentoo_updater.updater.os.scandir",
                    side_effect=FileNotFoundError):
        yield


@contextlib.contextmanager
def _quiet():
    """Silence the tool's terminal output during a test."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        yield


def _phase_names(report):
    return [p.name for p in report.phases]


def _phase(report, name):
    return next(p for p in report.phases if p.name == name)


# Tools present in a "normal" environment for these tests. eselect present so
# news runs (and reports 0 unread); revdep-rebuild/eix-update absent to keep
# the happy path from depending on them.
BASE_TOOLS = {"emerge", "emaint", "eselect"}


class RunAllPipeline(unittest.TestCase):

    def test_happy_path_runs_every_phase_and_succeeds(self):
        updater, runner = make_updater()
        with _quiet(), env(BASE_TOOLS):
            report = updater.run_all()

        self.assertFalse(report.failed)
        # every phase in the pipeline reported in order
        self.assertEqual(
            _phase_names(report),
            ["preflight", "news", "sync", "plan", "glsa", "snapshot",
             "apply", "config", "post-update", "elog", "verify"],
        )
        # the real world update actually got issued
        self.assertTrue(
            any(c[:1] == ["emerge"] and "@world" in c and "--pretend" not in c
                for c in runner.calls),
            "expected a real 'emerge ... @world' apply call",
        )

    def test_plan_failure_is_fatal_and_stops_before_apply(self):
        def handler(cmd):
            if cmd[:2] == ["emerge", "--pretend"]:
                return CommandResult(1, stdout="")  # dependency resolution failed
            return _happy_handler(cmd)

        updater, runner = make_updater(handler)
        with _quiet(), env(BASE_TOOLS):
            report = updater.run_all()

        self.assertTrue(report.failed)
        self.assertIn("plan", _phase_names(report))
        # pipeline stopped: apply and everything after was never reached
        self.assertNotIn("apply", _phase_names(report))
        self.assertFalse(
            any("@world" in c and "--pretend" not in c for c in runner.calls),
            "apply must not run after a failed plan",
        )

    def test_sync_failure_declined_is_fatal(self):
        def handler(cmd):
            if cmd[:2] == ["emaint", "sync"]:
                return CommandResult(1)  # a repo failed to sync
            return _happy_handler(cmd)

        # interactive=False, assume_yes=False -> _confirm returns the default,
        # which for "continue despite sync failure?" is False -> abort.
        updater, _ = make_updater(handler, interactive=False, assume_yes=False)
        with _quiet(), env(BASE_TOOLS):
            report = updater.run_all()

        self.assertTrue(report.failed)
        self.assertFalse(_phase(report, "sync").ok)
        self.assertNotIn("plan", _phase_names(report))  # stopped at sync

    def test_empty_plan_skips_apply_but_run_succeeds(self):
        def handler(cmd):
            if cmd[:2] == ["emerge", "--pretend"]:
                return CommandResult(0, stdout=PRETEND_EMPTY)
            return _happy_handler(cmd)

        updater, runner = make_updater(handler)
        with _quiet(), env(BASE_TOOLS):
            report = updater.run_all()

        self.assertFalse(report.failed)
        self.assertTrue(_phase(report, "apply").skipped)
        self.assertFalse(
            any("@world" in c and "--pretend" not in c for c in runner.calls),
            "nothing to do -> no real emerge",
        )

    def test_apply_failure_is_not_fatal_verify_still_runs(self):
        def handler(cmd):
            # the real world merge fails, but pretend/preserved stay healthy
            if (cmd[:1] == ["emerge"] and "@world" in cmd
                    and "--pretend" not in cmd):
                return CommandResult(1)
            return _happy_handler(cmd)

        updater, _ = make_updater(handler)
        with _quiet(), env(BASE_TOOLS):
            report = updater.run_all()

        self.assertTrue(report.failed)
        self.assertFalse(_phase(report, "apply").ok)
        # apply is non-fatal: the pipeline continues so we still report health
        self.assertIn("verify", _phase_names(report))

    def test_skip_flags_mark_phases_skipped_without_running_them(self):
        updater, runner = make_updater()
        with _quiet(), env(BASE_TOOLS):
            report = updater.run_all(skip_snapshot=True, skip_sync=True)

        self.assertTrue(_phase(report, "sync").skipped)
        self.assertTrue(_phase(report, "snapshot").skipped)
        self.assertFalse(report.failed)
        # sync was skipped by flag -> no emaint sync issued
        self.assertFalse(any(c[:2] == ["emaint", "sync"] for c in runner.calls))

    def test_snapshot_taken_when_backend_available(self):
        updater, _ = make_updater(snapshots_available=True)
        with _quiet(), env(BASE_TOOLS):
            report = updater.run_all()

        self.assertFalse(report.failed)
        snap = _phase(report, "snapshot")
        self.assertTrue(snap.ok)
        self.assertFalse(snap.skipped)
        self.assertEqual(report.snapshot_id, "fake#1")

    def test_glsa_phase_flags_affected_advisories(self):
        def handler(cmd):
            if cmd[:1] == ["glsa-check"]:
                return CommandResult(0, stdout="202501-07\n202412-03\n")
            return _happy_handler(cmd)

        updater, _ = make_updater(handler)
        with _quiet(), env(BASE_TOOLS | {"glsa-check"}):
            report = updater.run_all()

        glsa = _phase(report, "glsa")
        self.assertTrue(glsa.ok)  # informational, never fails the run
        self.assertIn("202501-07", glsa.detail)
        self.assertIn("202412-03", glsa.detail)

    def test_glsa_skipped_when_tool_absent(self):
        updater, _ = make_updater()
        with _quiet(), env(BASE_TOOLS):  # no glsa-check
            report = updater.run_all()
        self.assertTrue(_phase(report, "glsa").skipped)

    def test_reboot_advice_when_toolchain_updated(self):
        pretend = (
            "[ebuild     U   ] sys-libs/glibc-2.40-r1  0 KiB\n"
            "[ebuild     U   ] app-editors/vim-9.1.0  0 KiB\n"
            "Total: 2 packages, Size of downloads: 0 KiB\n"
        )

        def handler(cmd):
            if cmd[:2] == ["emerge", "--pretend"]:
                return CommandResult(0, stdout=pretend)
            return _happy_handler(cmd)

        updater, _ = make_updater(handler)
        with _quiet(), env(BASE_TOOLS):
            report = updater.run_all()

        self.assertIn("sys-libs/glibc", report.reboot_pkgs)
        self.assertNotIn("app-editors/vim", report.reboot_pkgs)

    def test_no_reboot_advice_when_apply_skipped(self):
        def handler(cmd):
            if cmd[:2] == ["emerge", "--pretend"]:
                return CommandResult(0, stdout=PRETEND_EMPTY)  # nothing to do
            return _happy_handler(cmd)

        updater, _ = make_updater(handler)
        with _quiet(), env(BASE_TOOLS):
            report = updater.run_all()
        self.assertEqual(report.reboot_pkgs, [])


class RollbackFlow(unittest.TestCase):
    def _snaps(self):
        from gentoo_updater.snapshot import SnapshotInfo
        return [
            SnapshotInfo("snapper#10", "snapper", "2026-08-14 09:00", "pre-update (gentoo-updater)"),
            SnapshotInfo("snapper#12", "snapper", "2026-08-15 09:00", "pre-update (gentoo-updater)"),
        ]

    def test_rollback_no_backend(self):
        updater, _ = make_updater(snapshots_available=False)
        with _quiet():
            rc = updater.run_rollback()
        self.assertEqual(rc, 1)

    def test_rollback_no_snapshots(self):
        updater, _ = make_updater(snapshots_available=True, snapshots=[])
        with _quiet():
            rc = updater.run_rollback()
        self.assertEqual(rc, 0)

    def test_rollback_unattended_picks_newest_and_restores(self):
        updater, _ = make_updater(snapshots_available=True, snapshots=self._snaps(),
                                  assume_yes=True)
        with _quiet():
            rc = updater.run_rollback()
        self.assertEqual(rc, 0)
        # newest (last) snapshot chosen and rolled back
        self.assertEqual(updater.snapshots.rolled_back, ["snapper#12"])


class RunnerDryRun(unittest.TestCase):
    """The --dry-run promise: mutating commands are described, never executed."""

    def test_dry_run_does_not_execute_mutating_command(self):
        runner = CommandRunner(dry_run=True, use_sudo=False)
        with mock.patch("gentoo_updater.runner.subprocess.run") as sp, _quiet():
            res = runner.stream(["emerge", "--update", "@world"])
        sp.assert_not_called()
        self.assertEqual(res.returncode, 0)

    def test_dry_run_still_runs_readonly_command(self):
        # a pretend/search emerge is side-effect-free, so it should really run
        runner = CommandRunner(dry_run=True, use_sudo=False)
        fake = mock.Mock(return_value=mock.Mock(returncode=0, stdout="ok", stderr=""))
        with mock.patch("gentoo_updater.runner.subprocess.run", fake), _quiet():
            runner.capture(["emerge", "--pretend", "@world"])
        fake.assert_called_once()

    def test_capture_pins_c_locale_for_parsed_output(self):
        runner = CommandRunner(dry_run=False, use_sudo=False)
        captured_env = {}

        def fake_run(cmd, **kw):
            captured_env.update(kw.get("env") or {})
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("gentoo_updater.runner.subprocess.run", side_effect=fake_run):
            runner.capture(["emerge", "--pretend", "@world"])
        self.assertEqual(captured_env.get("LC_ALL"), "C")
        self.assertEqual(captured_env.get("NO_COLOR"), "1")


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Tests for init detection and the per-init scheduling backends.
"""

import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gentoo_updater import schedule


def _which_stub(present):
    return lambda tool: (f"/usr/bin/{tool}" if tool in present else None)


class DetectInit(unittest.TestCase):
    def test_comm_says_systemd(self):
        with tempfile.NamedTemporaryFile("w", suffix="comm", delete=False) as f:
            f.write("systemd\n")
            path = f.name
        try:
            self.assertEqual(
                schedule.detect_init(comm_path=path, which=_which_stub(set())),
                schedule.INIT_SYSTEMD)
        finally:
            os.unlink(path)

    def test_runit_detected_by_runsvdir(self):
        self.assertEqual(
            schedule.detect_init(comm_path="/nonexistent",
                                 which=_which_stub({"runsvdir"})),
            schedule.INIT_RUNIT)

    def test_openrc_detected_by_rc_update(self):
        self.assertEqual(
            schedule.detect_init(comm_path="/nonexistent",
                                 which=_which_stub({"rc-update"})),
            schedule.INIT_OPENRC)

    def test_systemctl_wins_over_missing_comm(self):
        self.assertEqual(
            schedule.detect_init(comm_path="/nonexistent",
                                 which=_which_stub({"systemctl"})),
            schedule.INIT_SYSTEMD)

    def test_unknown_returns_none(self):
        self.assertIsNone(
            schedule.detect_init(comm_path="/nonexistent",
                                 which=_which_stub(set())))


class BackendMapping(unittest.TestCase):
    def test_maps_each_init(self):
        self.assertEqual(schedule.backend_for_init(schedule.INIT_SYSTEMD),
                         schedule.BACKEND_SYSTEMD)
        self.assertEqual(schedule.backend_for_init(schedule.INIT_RUNIT),
                         schedule.BACKEND_RUNIT)
        # OpenRC has no timer of its own -> cron
        self.assertEqual(schedule.backend_for_init(schedule.INIT_OPENRC),
                         schedule.BACKEND_CRON)

    def test_unknown_init_raises(self):
        with self.assertRaises(ValueError):
            schedule.backend_for_init("upstart")


class RenderBackends(unittest.TestCase):
    def test_systemd_plan_has_units_and_enable_steps(self):
        p = schedule.plan(schedule.BACKEND_SYSTEMD, exec_path="/usr/bin/gup")
        paths = list(p.files)
        self.assertTrue(any(x.endswith(".service") for x in paths))
        self.assertTrue(any(x.endswith(".timer") for x in paths))
        self.assertTrue(any("enable --now" in s for s in p.enable_hint))

    def test_cron_plan_is_executable_daily_script(self):
        p = schedule.plan(schedule.BACKEND_CRON, exec_path="gup", period="daily")
        (path, text), = p.files.items()
        self.assertEqual(path, "/etc/cron.daily/gentoo-updater")
        self.assertIn(path, p.executable)
        self.assertTrue(text.startswith("#!/bin/sh"))
        self.assertIn("gup -y --no-sudo", text)

    def test_cron_weekly_uses_weekly_dir(self):
        p = schedule.plan(schedule.BACKEND_CRON, period="weekly")
        self.assertIn("/etc/cron.weekly/gentoo-updater", p.files)

    def test_runit_run_script_loops_and_sleeps(self):
        p = schedule.plan(schedule.BACKEND_RUNIT, exec_path="gup", period="daily")
        (path, text), = p.files.items()
        self.assertEqual(path, "/etc/sv/gentoo-updater/run")
        self.assertIn(path, p.executable)
        self.assertIn("while true; do", text)
        self.assertIn("sleep 86400", text)
        self.assertIn("gup -y --no-sudo", text)


class InstallPlan(unittest.TestCase):
    def test_writes_files_and_sets_exec_bit(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "cron.daily", "gentoo-updater")
            p = schedule.SchedulePlan(
                backend="cron", files={path: "#!/bin/sh\necho hi\n"},
                executable={path})
            written = schedule.install(p)
            self.assertEqual(written, [path])
            self.assertTrue(os.path.exists(path))
            mode = stat.S_IMODE(os.stat(path).st_mode)
            self.assertTrue(mode & stat.S_IXUSR, "expected the exec bit set")

    def test_raises_on_unwritable_dest(self):
        p = schedule.SchedulePlan(backend="cron",
                                  files={"/proc/nope/x": "data"})
        with self.assertRaises(OSError):
            schedule.install(p)


if __name__ == "__main__":
    unittest.main(verbosity=2)

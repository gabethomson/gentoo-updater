"""Tests for systemd unit rendering and the install fallback.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gentoo_updater import systemd


class Render(unittest.TestCase):
    def test_service_runs_gup_unattended_as_root(self):
        unit = systemd.service_unit(exec_path="/usr/bin/gup")
        self.assertIn("Type=oneshot", unit)
        # unattended (-y) and, since a system unit is already root, --no-sudo
        self.assertIn("ExecStart=/usr/bin/gup -y --no-sudo", unit)

    def test_timer_uses_schedule_and_is_persistent(self):
        unit = systemd.timer_unit("weekly")
        self.assertIn("OnCalendar=weekly", unit)
        self.assertIn("Persistent=true", unit)
        self.assertIn("WantedBy=timers.target", unit)

    def test_unit_files_bundle_both(self):
        files = systemd.unit_files()
        self.assertEqual(set(files), {systemd.SERVICE_NAME, systemd.TIMER_NAME})


class Install(unittest.TestCase):
    def test_writes_both_units(self):
        files = systemd.unit_files(exec_path="gup")
        with tempfile.TemporaryDirectory() as d:
            written = systemd.install_units(files, dest=d)
            self.assertEqual(len(written), 2)
            for path in written:
                self.assertTrue(os.path.exists(path))

    def test_raises_on_unwritable_dest(self):
        with self.assertRaises(OSError):
            systemd.install_units(systemd.unit_files(), dest="/proc/nope/systemd")


if __name__ == "__main__":
    unittest.main(verbosity=2)

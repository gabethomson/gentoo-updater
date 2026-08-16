"""Tests for the per-run debug log setup.
"""

import logging
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gentoo_updater import debuglog


class Setup(unittest.TestCase):
    def tearDown(self):
        # Remove any handler we attached so it doesn't outlive the temp file.
        lg = logging.getLogger(debuglog.LOGGER)
        for h in list(lg.handlers):
            lg.removeHandler(h)
            h.close()
        lg.propagate = True
        lg.setLevel(logging.NOTSET)

    def test_writes_and_captures_child_logs(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "debug.log")
            got = debuglog.setup(path)
            self.assertEqual(got, path)

            logging.getLogger("gentoo_updater.runner").debug("MARKER-123")
            for h in logging.getLogger(debuglog.LOGGER).handlers:
                h.flush()
            with open(path, encoding="utf-8") as fh:
                content = fh.read()
        self.assertIn("MARKER-123", content)
        self.assertIn("gentoo-updater run", content)  # the header line

    def test_creates_parent_directory(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "nested", "sub", "debug.log")
            got = debuglog.setup(path)
            self.assertEqual(got, path)
            self.assertTrue(os.path.exists(path))

    def test_returns_none_when_nowhere_writable(self):
        got = debuglog.setup("/proc/nope/cannot/debug.log")
        self.assertIsNone(got)


if __name__ == "__main__":
    unittest.main(verbosity=2)

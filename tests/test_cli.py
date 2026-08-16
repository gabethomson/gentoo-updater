"""Tests for argument parsing and the config/CLI layering in cli.py.
"""

import io
import contextlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gentoo_updater import __version__, cli

COMMANDS = ["update", "plan", "verify", "news", "rollback", "depclean",
            "install-schedule", "install-timer"]


class Parser(unittest.TestCase):
    def test_every_command_parses(self):
        p = cli.build_parser()
        for c in COMMANDS:
            self.assertEqual(p.parse_args([c]).command, c)

    def test_default_command_is_update(self):
        self.assertEqual(cli.build_parser().parse_args([]).command, "update")

    def test_version_flag_prints_version_and_exits(self):
        buf = io.StringIO()
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stdout(buf):
            cli.build_parser().parse_args(["--version"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn(__version__, buf.getvalue())

    def test_toggles_default_to_none_so_config_can_win(self):
        # A plain store_true would force False and silently override the config.
        args = cli.build_parser().parse_args([])
        for flag in ("yes", "non_interactive", "no_snapshot", "no_sync",
                     "no_sudo", "depclean", "select", "notify"):
            self.assertIsNone(getattr(args, flag), f"{flag} should default to None")

    def test_rejects_unknown_command(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                cli.build_parser().parse_args(["bogus"])


class EffectiveConfig(unittest.TestCase):
    def _cfg(self, argv):
        args = cli.build_parser().parse_args(argv)
        # point at a nonexistent config so only defaults + CLI apply
        args.config = "/nonexistent/gup-test.toml"
        return cli._effective_config(args)

    def test_flags_override_defaults(self):
        cfg = self._cfg(["-y", "--select", "--notify", "failure", "update"])
        self.assertTrue(cfg.yes)
        self.assertTrue(cfg.select)
        self.assertEqual(cfg.notify, "failure")

    def test_unset_flags_leave_defaults(self):
        cfg = self._cfg(["update"])
        self.assertFalse(cfg.yes)
        self.assertEqual(cfg.notify, "never")
        self.assertTrue(cfg.audit)

    def test_no_audit_turns_audit_off(self):
        self.assertFalse(self._cfg(["--no-audit", "update"]).audit)


if __name__ == "__main__":
    unittest.main(verbosity=2)

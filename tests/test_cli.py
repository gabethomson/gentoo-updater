"""Tests for argument parsing and the config/CLI layering in cli.py.
"""

import io
import contextlib
import os
import sys
import unittest
from unittest import mock

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


class RootGuard(unittest.TestCase):
    """`sudo gup <portage command>` must be refused (with an opt-out), while
    the schedule installers -- which legitimately need root -- are not."""

    def _cfg(self, argv):
        args = cli.build_parser().parse_args(argv)
        args.config = "/nonexistent/gup-test.toml"
        return cli._effective_config(args)

    @contextlib.contextmanager
    def _euid(self, uid):
        with mock.patch("gentoo_updater.cli.os.geteuid", return_value=uid), \
             contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            yield

    def test_refuses_root_without_no_sudo(self):
        with self._euid(0):
            self.assertTrue(cli._refuse_root(self._cfg(["update"])))

    def test_allows_root_with_no_sudo(self):
        # The explicit opt-out: a root shell or the systemd unit.
        with self._euid(0):
            self.assertFalse(cli._refuse_root(self._cfg(["--no-sudo", "update"])))

    def test_allows_normal_user(self):
        with self._euid(1000):
            self.assertFalse(cli._refuse_root(self._cfg(["update"])))

    def test_dispatch_update_as_root_aborts_before_touching_portage(self):
        args = cli.build_parser().parse_args(["update"])
        args.config = "/nonexistent/gup-test.toml"
        with self._euid(0), \
             mock.patch.object(cli, "_make_updater") as make, \
             mock.patch("gentoo_updater.cli.single_instance") as lock:
            rc = cli._dispatch(args)
        self.assertEqual(rc, 1)
        make.assert_not_called()   # never built an updater
        lock.assert_not_called()   # never took the lock / ran a merge

    def test_schedule_install_not_blocked_as_root(self):
        # install-* writes to /etc and returns before the guard, so root is fine.
        args = cli.build_parser().parse_args(
            ["--dry-run", "--init", "systemd", "install-schedule"])
        with self._euid(0):
            self.assertEqual(cli._dispatch(args), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

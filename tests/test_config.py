"""Tests for config-file parsing and the CLI-override merge.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gentoo_updater.config import (
    Config, parse_config_text, load_config, tomllib,
)


@unittest.skipIf(tomllib is None, "tomllib unavailable on this interpreter")
class ParseConfig(unittest.TestCase):
    def test_reads_known_keys_and_coerces_types(self):
        text = """\
yes = true
low_space_gib = 8
notify = "failure"
notify_email = "root@localhost"
"""
        d = parse_config_text(text)
        self.assertEqual(d["yes"], True)
        self.assertEqual(d["low_space_gib"], 8.0)
        self.assertIsInstance(d["low_space_gib"], float)
        self.assertEqual(d["notify"], "failure")
        self.assertEqual(d["notify_email"], "root@localhost")

    def test_accepts_a_section_table(self):
        d = parse_config_text('[gentoo-updater]\nno_snapshot = true\n')
        self.assertEqual(d["no_snapshot"], True)

    def test_accepts_hyphenated_keys(self):
        d = parse_config_text('no-sync = true\n')
        self.assertEqual(d["no_sync"], True)

    def test_ignores_unknown_keys(self):
        d = parse_config_text('bogus = 1\nyes = true\n')
        self.assertNotIn("bogus", d)
        self.assertEqual(d["yes"], True)

    def test_invalid_notify_trigger_is_dropped(self):
        d = parse_config_text('notify = "sometimes"\n')
        self.assertNotIn("notify", d)

    def test_malformed_toml_yields_empty(self):
        self.assertEqual(parse_config_text("this is = = not toml"), {})


class MergeWithCli(unittest.TestCase):
    def test_cli_value_overrides_config(self):
        cfg = Config(yes=True, notify="always")
        merged = cfg.merged_with_cli({"yes": None, "notify": "failure"})
        self.assertTrue(merged.yes)          # None -> config kept
        self.assertEqual(merged.notify, "failure")  # explicit -> overridden

    def test_none_leaves_everything_untouched(self):
        cfg = Config(no_snapshot=True, low_space_gib=9.0)
        merged = cfg.merged_with_cli({"no_snapshot": None})
        self.assertTrue(merged.no_snapshot)
        self.assertEqual(merged.low_space_gib, 9.0)


class LoadConfig(unittest.TestCase):
    def test_missing_files_give_defaults(self):
        cfg = load_config(["/nonexistent/a.toml", "/nonexistent/b.toml"])
        self.assertEqual(cfg, Config())

    @unittest.skipIf(tomllib is None, "tomllib unavailable")
    def test_later_path_overrides_earlier(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            a = os.path.join(d, "a.toml")
            b = os.path.join(d, "b.toml")
            with open(a, "w") as fh:
                fh.write('yes = true\nlow_space_gib = 3\n')
            with open(b, "w") as fh:
                fh.write('low_space_gib = 10\n')
            cfg = load_config([a, b])
        self.assertTrue(cfg.yes)              # from a
        self.assertEqual(cfg.low_space_gib, 10.0)  # b wins


if __name__ == "__main__":
    unittest.main(verbosity=2)

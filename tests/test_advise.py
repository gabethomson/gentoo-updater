"""Tests for the pure advisory/parsing helpers and the snapshot-list parsers.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gentoo_updater import advise
from gentoo_updater.snapshot import parse_snapper_list, parse_btrfs_snapshots
from gentoo_updater import lockfile


class RebootAdvice(unittest.TestCase):
    def test_flags_toolchain_and_init(self):
        names = ["app-editors/vim", "sys-libs/glibc", "sys-apps/systemd",
                 "sys-kernel/gentoo-sources", "sys-apps/dbus", "dev-libs/foo"]
        got = advise.packages_needing_reboot(names)
        self.assertEqual(
            got,
            ["sys-apps/dbus", "sys-apps/systemd", "sys-kernel/gentoo-sources",
             "sys-libs/glibc"],
        )

    def test_ignores_ordinary_packages(self):
        self.assertEqual(advise.packages_needing_reboot(["www-client/firefox"]), [])


class Autounmask(unittest.TestCase):
    KEYWORD_BLOCK = """\
!!! All ebuilds that could satisfy "app-foo/bar" have been masked.

The following keyword changes are necessary to proceed:
 (see "package.accept_keywords" in the portage(5) man page for more details)
# required by @selected
=app-foo/bar-1.2.3 ~amd64

The following USE changes are necessary to proceed:
 (see "package.use" in the portage(5) man page for more details)
>=dev-libs/baz-2.0 qt6

Total: 0 packages
"""

    def test_extracts_keyword_and_use_blocks(self):
        s = advise.parse_autounmask(self.KEYWORD_BLOCK)
        self.assertTrue(s.any)
        kinds = {c.kind: c for c in s.changes}
        self.assertIn("keyword", kinds)
        self.assertIn("use", kinds)
        self.assertIn("=app-foo/bar-1.2.3 ~amd64", kinds["keyword"].lines)
        self.assertIn(">=dev-libs/baz-2.0 qt6", kinds["use"].lines)
        # notes ("(see ...)") and comments are not treated as atoms
        self.assertNotIn(
            "# required by @selected",
            [ln for c in s.changes for ln in c.lines],
        )

    def test_points_at_the_right_portage_file(self):
        s = advise.parse_autounmask(self.KEYWORD_BLOCK)
        by_kind = {c.kind: c.target_file for c in s.changes}
        self.assertEqual(by_kind["keyword"], "package.accept_keywords")
        self.assertEqual(by_kind["use"], "package.use")

    def test_empty_on_clean_output(self):
        s = advise.parse_autounmask("Total: 5 packages, Size of downloads: 1 KiB")
        self.assertFalse(s.any)


class Glsa(unittest.TestCase):
    def test_parses_ids(self):
        out = "202501-07\n202412-03\n"
        self.assertEqual(advise.parse_glsa_ids(out), ["202501-07", "202412-03"])

    def test_ignores_noise(self):
        out = "This system is affected by the following GLSAs:\n202501-07\n"
        self.assertEqual(advise.parse_glsa_ids(out), ["202501-07"])

    def test_empty_when_none(self):
        self.assertEqual(advise.parse_glsa_ids(""), [])


class Elog(unittest.TestCase):
    def test_extracts_package(self):
        self.assertEqual(
            advise.elog_package_from_filename("app-editors:vim-9.1.0:20260815-101500.log"),
            "app-editors/vim-9.1.0",
        )

    def test_rejects_non_elog(self):
        self.assertIsNone(advise.elog_package_from_filename("summary.log"))
        self.assertIsNone(advise.elog_package_from_filename("random.txt"))


class NewsCorrelation(unittest.TestCase):
    ITEM = """\
Title: GCC 14 now defaults to a new C standard
Author: Dev <dev@gentoo.org>
Posted: 2024-05-01
Revision: 1
News-Item-Format: 2.0
Display-If-Installed: sys-devel/gcc
Display-If-Installed: >=sys-libs/glibc-2.39

Body starts here. Title: this is not a header.
Display-If-Installed: net-misc/ignored-in-body
"""

    def test_atom_name_strips_operators_slot_repo_version(self):
        self.assertEqual(advise.news_atom_name(">=sys-apps/portage-3.0.30"),
                         "sys-apps/portage")
        self.assertEqual(advise.news_atom_name("dev-lang/python:3.11"),
                         "dev-lang/python")
        self.assertEqual(advise.news_atom_name("sys-libs/glibc::gentoo"),
                         "sys-libs/glibc")
        self.assertEqual(advise.news_atom_name("app-editors/vim"),
                         "app-editors/vim")

    def test_parses_title_and_installed_atoms_only_from_headers(self):
        item = advise.parse_news_item(self.ITEM)
        self.assertEqual(item.title, "GCC 14 now defaults to a new C standard")
        self.assertEqual(item.if_installed, ["sys-devel/gcc", "sys-libs/glibc"])
        # the Display-If-Installed line in the body must be ignored
        self.assertNotIn("net-misc/ignored-in-body", item.if_installed)

    def test_relevant_news_matches_pending_packages(self):
        item = advise.parse_news_item(self.ITEM)
        got = advise.relevant_news([item], {"sys-devel/gcc", "app-editors/vim"})
        self.assertEqual(got, [("GCC 14 now defaults to a new C standard",
                                ["sys-devel/gcc"])])

    def test_relevant_news_empty_when_nothing_matches(self):
        item = advise.parse_news_item(self.ITEM)
        self.assertEqual(advise.relevant_news([item], {"www-client/firefox"}), [])


class SnapshotParsing(unittest.TestCase):
    SNAPPER = """\
 # | Date                | Description
---+---------------------+---------------------------
 0 |                     | current
 9 | 2026-08-10 10:00:00 | timeline hourly
10 | 2026-08-14 09:00:00 | pre-update (gentoo-updater)
12 | 2026-08-15 09:00:00 | pre-update (gentoo-updater)
"""

    def test_snapper_keeps_only_tagged(self):
        snaps = parse_snapper_list(self.SNAPPER, tag="gentoo-updater")
        self.assertEqual([s.ident for s in snaps], ["snapper#10", "snapper#12"])
        self.assertEqual(snaps[0].when, "2026-08-14 09:00:00")

    def test_btrfs_sorts_by_timestamp(self):
        find_out = (
            "/.snapshots/gentoo-updater-20260815-090000\n"
            "/.snapshots/gentoo-updater-20260814-090000\n"
        )
        snaps = parse_btrfs_snapshots(find_out)
        self.assertEqual(
            [s.when for s in snaps],
            ["20260814-090000", "20260815-090000"],  # sorted ascending
        )
        self.assertTrue(all(s.backend == "btrfs" for s in snaps))


class Lock(unittest.TestCase):
    def test_second_acquire_raises_already_running(self):
        import tempfile
        # Force the temp-dir fallback path by making both opens hit the same file.
        with mock.patch.object(lockfile, "_PREFERRED",
                               os.path.join(tempfile.gettempdir(), "gup-test.lock")):
            with lockfile.single_instance():
                with self.assertRaises(lockfile.AlreadyRunning):
                    with lockfile.single_instance():
                        pass


if __name__ == "__main__":
    unittest.main(verbosity=2)

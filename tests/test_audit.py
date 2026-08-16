"""Tests for the audit-record builder, JSONL renderer, and writer fallback.

Run with:  python -m pytest   (or standalone: python tests/test_audit.py)
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gentoo_updater import audit
from gentoo_updater.updater import UpdateReport, PhaseResult
from gentoo_updater.parse import PretendPlan, PkgChange


def _change(name):
    return PkgChange(atom=f"{name}-1.0", name=name, kind="ebuild", is_new=False,
                     is_upgrade=True, is_rebuild=False, needs_keyword=False,
                     from_binary=False)


def _report():
    r = UpdateReport()
    r.add(PhaseResult("preflight", ok=True, detail="ok"))
    r.add(PhaseResult("sync", ok=True, skipped=True, detail="skipped by flag"))
    r.add(PhaseResult("apply", ok=False, detail="emerge failed"))
    r.snapshot_id = "snapper#12"
    r.reboot_pkgs = ["sys-libs/glibc"]
    r.plan = PretendPlan(changes=[_change("a/b"), _change("c/d"), _change("e/f")])
    return r


class BuildRecord(unittest.TestCase):
    def test_captures_outcome_and_phases(self):
        rec = audit.build_record(_report(), started=100.0, finished=142.5,
                                 version="0.1.0", command="update")
        self.assertEqual(rec["command"], "update")
        self.assertEqual(rec["version"], "0.1.0")
        self.assertFalse(rec["ok"])            # an apply phase failed
        self.assertEqual(rec["duration_s"], 42.5)
        self.assertEqual(rec["snapshot_id"], "snapper#12")
        self.assertEqual(rec["reboot_pkgs"], ["sys-libs/glibc"])
        self.assertEqual(rec["packages_total"], 3)
        statuses = {p["name"]: p["status"] for p in rec["phases"]}
        self.assertEqual(statuses, {"preflight": "ok", "sync": "skipped",
                                    "apply": "fail"})

    def test_render_line_is_valid_jsonl(self):
        rec = audit.build_record(_report(), started=0.0, finished=1.0,
                                 version="0.1.0")
        line = audit.render_line(rec)
        self.assertTrue(line.endswith("\n"))
        self.assertEqual(json.loads(line)["version"], "0.1.0")


class WriterFallback(unittest.TestCase):
    def test_writes_to_first_writable_path(self):
        with tempfile.TemporaryDirectory() as d:
            good = os.path.join(d, "history.jsonl")
            log = audit.AuditLog(good)
            path = log.record({"a": 1})
            self.assertEqual(path, good)
            with open(good) as fh:
                self.assertEqual(json.loads(fh.read())["a"], 1)

    def test_falls_back_when_primary_unwritable(self):
        with tempfile.TemporaryDirectory() as d:
            fallback = os.path.join(d, "state", "history.jsonl")
            log = audit.AuditLog("/proc/cannot/write/here.jsonl")
            log._fallback = fallback
            path = log.record({"a": 2})
            self.assertEqual(path, fallback)
            self.assertTrue(os.path.exists(fallback))

    def test_returns_none_when_nothing_writable(self):
        log = audit.AuditLog("/proc/nope/a.jsonl")
        log._fallback = "/proc/nope/b.jsonl"
        self.assertIsNone(log.record({"a": 3}))


if __name__ == "__main__":
    unittest.main(verbosity=2)

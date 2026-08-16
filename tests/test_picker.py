"""Tests for the package-picker selection state and the plain fallback.

The curses front end needs a real terminal and isn't unit-tested; the logic it
drives (Selection) and the numbered fallback are covered here.

Run with:  python -m pytest   (or standalone: python tests/test_picker.py)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gentoo_updater import picker
from gentoo_updater.picker import Selection

ITEMS = [
    ("app-editors/vim", "U  app-editors/vim  9.0.1 → 9.1.0"),
    ("sys-libs/glibc", "U  sys-libs/glibc  2.39 → 2.40"),
    ("dev-lang/python", "U  dev-lang/python  3.13 → 3.14"),
]


class SelectionState(unittest.TestCase):
    def test_starts_all_checked(self):
        sel = Selection.from_items(ITEMS)
        self.assertEqual(sel.checked, [True, True, True])
        self.assertEqual(sel.n_checked, 3)
        self.assertEqual(sel.checked_names(), [n for n, _ in ITEMS])
        self.assertEqual(sel.excluded_names(), [])

    def test_toggle_and_exclude(self):
        sel = Selection.from_items(ITEMS)
        sel.toggle(1)  # uncheck glibc
        self.assertEqual(sel.excluded_names(), ["sys-libs/glibc"])
        self.assertEqual(sel.checked_names(),
                         ["app-editors/vim", "dev-lang/python"])

    def test_cursor_moves_and_wraps(self):
        sel = Selection.from_items(ITEMS)
        sel.move(-1)
        self.assertEqual(sel.cursor, 2)  # wrapped to end
        sel.move(1)
        self.assertEqual(sel.cursor, 0)
        sel.toggle()  # toggles at cursor (0)
        self.assertFalse(sel.checked[0])

    def test_set_all(self):
        sel = Selection.from_items(ITEMS)
        sel.set_all(False)
        self.assertEqual(sel.n_checked, 0)
        sel.set_all(True)
        self.assertEqual(sel.n_checked, 3)


class PlainFallback(unittest.TestCase):
    def _run(self, typed):
        sel = Selection.from_items(ITEMS)
        return picker._plain_pick(sel, input_fn=lambda _="": typed,
                                  out=lambda *a, **k: None)

    def test_unselect_by_number(self):
        self.assertEqual(self._run("2"),
                         ["app-editors/vim", "dev-lang/python"])

    def test_unselect_multiple_mixed_separators(self):
        self.assertEqual(self._run("1, 3"), ["sys-libs/glibc"])

    def test_blank_keeps_all(self):
        self.assertEqual(self._run(""), [n for n, _ in ITEMS])

    def test_q_cancels(self):
        self.assertIsNone(self._run("q"))

    def test_out_of_range_and_garbage_ignored(self):
        self.assertEqual(self._run("9 foo 2"),
                         ["app-editors/vim", "dev-lang/python"])


class PickDispatch(unittest.TestCase):
    def test_empty_items_returns_empty(self):
        self.assertEqual(picker.pick([]), [])

    def test_backend_routes_to_the_right_front_end(self):
        from unittest import mock
        with mock.patch.object(picker, "_plain_pick",
                               return_value=["kept"]) as plain, \
             mock.patch.object(picker, "_curses_pick",
                               return_value=["kept"]) as curses:
            self.assertEqual(picker.pick(ITEMS, backend="plain"), ["kept"])
            plain.assert_called_once()
            curses.assert_not_called()
            picker.pick(ITEMS, backend="curses")
            curses.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)

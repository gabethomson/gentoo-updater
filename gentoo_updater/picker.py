"""The `--select` checklist. Selection holds the state (testable on its own);
pick() drives it with either a curses checkbox or, when there's no tty, a plain
numbered prompt."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field


@dataclass
class Selection:
    # Named items, all checked to start.
    names: list[str]
    labels: list[str]
    checked: list[bool] = field(default_factory=list)
    cursor: int = 0

    def __post_init__(self):
        if not self.checked:
            self.checked = [True] * len(self.names)

    @classmethod
    def from_items(cls, items: list[tuple[str, str]]) -> "Selection":
        return cls(names=[n for n, _ in items], labels=[l for _, l in items])

    def move(self, delta: int) -> None:
        if self.names:
            self.cursor = (self.cursor + delta) % len(self.names)

    def toggle(self, index: int | None = None) -> None:
        i = self.cursor if index is None else index
        if 0 <= i < len(self.checked):
            self.checked[i] = not self.checked[i]

    def set_all(self, value: bool) -> None:
        self.checked = [value] * len(self.checked)

    def checked_names(self) -> list[str]:
        return [n for n, c in zip(self.names, self.checked) if c]

    def excluded_names(self) -> list[str]:
        return [n for n, c in zip(self.names, self.checked) if not c]

    @property
    def n_checked(self) -> int:
        return sum(self.checked)


def pick(items: list[tuple[str, str]], *, backend: str | None = None):
    # items are (name, label). Returns the checked names, or None if cancelled.
    # backend forces "curses"/"plain" (tests use it).
    if not items:
        return []
    sel = Selection.from_items(items)
    if backend is None:
        backend = "curses" if _curses_usable() else "plain"
    if backend == "curses":
        return _curses_pick(sel)
    return _plain_pick(sel)


def _curses_usable() -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    try:
        import curses  # noqa: F401
    except Exception:  # noqa: BLE001 - no curses on this platform
        return False
    return True


# -- curses front end ----------------------------------------------------

def _curses_pick(sel: Selection):
    import curses

    result = {"value": None}

    def _loop(stdscr):
        curses.curs_set(0)
        stdscr.keypad(True)
        top = 0
        while True:
            _draw(stdscr, sel, top)
            top = _keep_cursor_visible(stdscr, sel, top)
            ch = stdscr.getch()
            if ch in (curses.KEY_UP, ord("k")):
                sel.move(-1)
            elif ch in (curses.KEY_DOWN, ord("j")):
                sel.move(1)
            elif ch == ord(" "):
                sel.toggle()
            elif ch in (ord("a"), ord("A")):
                sel.set_all(True)
            elif ch in (ord("n"), ord("N")):
                sel.set_all(False)
            elif ch in (curses.KEY_ENTER, 10, 13):
                result["value"] = sel.checked_names()
                return
            elif ch in (ord("q"), 27):  # q or ESC
                result["value"] = None
                return

    curses.wrapper(_loop)
    return result["value"]


def _header_lines(sel: Selection) -> list[str]:
    return [
        "Select packages to update  "
        f"({sel.n_checked}/{len(sel.names)} selected)",
        "↑/↓ move · space toggle · a all · n none · "
        "enter confirm · q cancel",
        "",
    ]


def _draw(stdscr, sel: Selection, top: int) -> None:
    import curses

    stdscr.erase()
    height, width = stdscr.getmaxyx()
    head = _header_lines(sel)
    for i, line in enumerate(head):
        _addstr(stdscr, i, 0, line[: width - 1])
    list_top = len(head)
    rows = height - list_top
    for row in range(rows):
        idx = top + row
        if idx >= len(sel.names):
            break
        box = "[x]" if sel.checked[idx] else "[ ]"
        pointer = ">" if idx == sel.cursor else " "
        text = f"{pointer} {box} {sel.labels[idx]}"[: width - 1]
        attr = curses.A_BOLD if idx == sel.cursor else curses.A_NORMAL
        _addstr(stdscr, list_top + row, 0, text, attr)
    stdscr.refresh()


def _keep_cursor_visible(stdscr, sel: Selection, top: int) -> int:
    height, _ = stdscr.getmaxyx()
    rows = height - len(_header_lines(sel))
    if sel.cursor < top:
        return sel.cursor
    if sel.cursor >= top + rows:
        return sel.cursor - rows + 1
    return top


def _addstr(stdscr, y, x, text, attr=0) -> None:
    try:
        stdscr.addstr(y, x, text, attr)
    except Exception:  # noqa: BLE001 - never let a draw glitch crash the picker
        pass


# -- plain numbered fallback --------------------------------------------

def _plain_pick(sel: Selection, *, input_fn=None, out=print):
    # One shot: print the list, ask which numbers to drop.
    input_fn = input_fn or input  # late bind so tests can patch builtins.input
    out("Pending update -- all selected. Enter numbers to UNSELECT "
        "(space/comma separated), 'q' to cancel, or blank to keep all:")
    for i, label in enumerate(sel.labels, 1):
        out(f"  [x] {i:>3}  {label}")
    try:
        raw = input_fn("> ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if raw.lower() == "q":
        return None
    if not raw:
        return sel.checked_names()
    for tok in raw.replace(",", " ").split():
        try:
            n = int(tok)
        except ValueError:
            continue
        if 1 <= n <= len(sel.names):
            sel.toggle(n - 1)
    return sel.checked_names()

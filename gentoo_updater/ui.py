"""All terminal output goes through here. Uses rich for colour/tables when it's
installed, otherwise plain print(). The live spinner is hand-rolled (one thread,
raw ANSI on a single line) rather than a rich widget -- that way it can animate
through a long blocking command without a rich refresh thread fighting the
subprocess, and suspend() can hand the terminal cleanly to a child or the picker."""

from __future__ import annotations

import contextlib
import itertools
import sys
import threading
import time

try:
    from rich.console import Console
    from rich.table import Table
    from rich.prompt import Confirm
    _console: "Console | None" = Console()
    _HAVE_RICH = True
except Exception:  # noqa: BLE001 - rich is optional
    _console = None
    _HAVE_RICH = False


# Live-spinner state. Module-level because both the updater and the runner talk
# to ui, and the runner needs suspend() to pause the spinner while a subprocess
# owns the terminal.
_PLAIN = False              # --plain
_active = False             # is the spinner in use this run?
_paused = False             # temporarily handed the terminal to someone else
_current: "str | None" = None  # name of the phase currently running
_run_start: float = 0.0
_lock = threading.Lock()    # serialises every write to the terminal
_anim_thread: "threading.Thread | None" = None
_anim_stop: "threading.Event | None" = None

_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def set_plain(plain: bool) -> None:
    global _PLAIN
    _PLAIN = plain


def _dashboard_wanted() -> bool:
    return _HAVE_RICH and not _PLAIN and sys.stdout.isatty()


def _emit(render) -> None:
    """Run a print. When the spinner is up, clear its line first (under the lock)
    so output never lands on top of it; the animator redraws below on its next
    tick."""
    if _active:
        with _lock:
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()
            render()
    else:
        render()


# -- basic messages ------------------------------------------------------

def _plain(msg: str) -> None:
    print(msg)


def info(msg: str) -> None:
    _emit(lambda: _console.print(msg) if _HAVE_RICH else _plain(msg))


def dim(msg: str) -> None:
    _emit(lambda: _console.print(f"[dim]{msg}[/dim]") if _HAVE_RICH else _plain(msg))


def warn(msg: str) -> None:
    _emit(lambda: _console.print(f"[yellow]! {msg}[/yellow]") if _HAVE_RICH
          else _plain(f"! {msg}"))


def error(msg: str) -> None:
    _emit(lambda: _console.print(f"[bold red]x {msg}[/bold red]") if _HAVE_RICH
          else _plain(f"x {msg}"))


def hint(msg: str) -> None:
    _emit(lambda: _console.print(f"[cyan]  -> {msg}[/cyan]") if _HAVE_RICH
          else _plain(f"  -> {msg}"))


def phase_header(name: str) -> None:
    # The spinner already names the current phase, so skip the rule when it's up.
    if _active:
        return
    label = f" {name.upper()} "
    if _HAVE_RICH:
        _console.rule(f"[bold]{label}[/bold]")
    else:
        _plain("\n" + "=" * 8 + label + "=" * 8)


# -- prompts -------------------------------------------------------------

def confirm(prompt: str, *, default: bool = False) -> bool:
    # A prompt needs the terminal to itself, so pause the spinner while we ask.
    with suspend():
        if _HAVE_RICH:
            try:
                return Confirm.ask(prompt, default=default)
            except (EOFError, KeyboardInterrupt):
                return False
        suffix = " [Y/n] " if default else " [y/N] "
        try:
            ans = input(prompt + suffix).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if not ans:
            return default
        return ans in ("y", "yes")


# -- structured views ----------------------------------------------------

def show_plan(plan) -> None:
    def render():
        if plan.total == 0:
            (_console.print("[green]System is up to date -- nothing to do.[/green]")
             if _HAVE_RICH else _plain("System is up to date -- nothing to do."))
            return
        if _HAVE_RICH:
            table = Table(title="Pending update", show_edge=False, header_style="bold")
            table.add_column("Category")
            table.add_column("Count", justify="right")
            table.add_row("New", str(len(plan.new)))
            table.add_row("Upgrades", str(len(plan.upgrades)))
            table.add_row("Rebuilds", str(len(plan.rebuilds)))
            table.add_row("From binhost", str(len(plan.from_binary)))
            table.add_row("Need keyword (~)", str(len(plan.needs_keywords)))
            table.add_row("[bold]Total[/bold]", f"[bold]{plan.total}[/bold]")
            _console.print(table)
            if plan.download_size:
                _console.print(f"[dim]Download size: {plan.download_size}[/dim]")
            if plan.needs_keywords:
                _console.print("[yellow]Packages needing a testing keyword:[/yellow]")
                for c in plan.needs_keywords:
                    _console.print(f"  [yellow]~[/yellow] {c.atom}")
        else:
            _plain(f"Pending update: {plan.total} package(s)")
            _plain(f"  new={len(plan.new)} upgrades={len(plan.upgrades)} "
                   f"rebuilds={len(plan.rebuilds)} binhost={len(plan.from_binary)} "
                   f"need-keyword={len(plan.needs_keywords)}")
            if plan.download_size:
                _plain(f"  download size: {plan.download_size}")
    _emit(render)


def show_autounmask(suggestion) -> None:
    def render():
        if _HAVE_RICH:
            _console.print("[yellow]! The update needs configuration changes "
                           "before it can proceed.[/yellow]")
            _console.print("[bold]Add the following to /etc/portage[/bold] "
                           "(review each line before applying):")
        else:
            _plain("! The update needs configuration changes before it can proceed.")
            _plain("Add the following to /etc/portage (review each line before applying):")
        for change in suggestion.changes:
            header = f"# {change.target_file}"
            if _HAVE_RICH:
                _console.print(f"[cyan]{header}[/cyan]")
                for ln in change.lines:
                    _console.print(f"  {ln}")
            else:
                _plain(header)
                for ln in change.lines:
                    _plain(f"  {ln}")
        msg = "Once written, run 'dispatch-conf' if needed, then re-run gup."
        _console.print(f"[cyan]  -> {msg}[/cyan]") if _HAVE_RICH else _plain(f"  -> {msg}")
    _emit(render)


def show_snapshots(snaps) -> None:
    if _HAVE_RICH:
        table = Table(title="Pre-update snapshots", header_style="bold")
        table.add_column("#", justify="right")
        table.add_column("Id")
        table.add_column("When")
        table.add_column("Description", overflow="fold")
        for idx, s in enumerate(snaps, 1):
            table.add_row(str(idx), s.ident, s.when, s.description)
        _console.print(table)
    else:
        _plain("Pre-update snapshots:")
        for idx, s in enumerate(snaps, 1):
            _plain(f"  {idx}) {s.ident}  {s.when}  {s.description}")


def select_snapshot(snaps):
    # Pick by number; blank/invalid cancels.
    prompt = f"Snapshot to roll back to [1-{len(snaps)}, or blank to cancel]: "
    try:
        raw = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not raw:
        return None
    try:
        idx = int(raw)
    except ValueError:
        error("Not a number.")
        return None
    if not 1 <= idx <= len(snaps):
        error("Out of range.")
        return None
    return snaps[idx - 1]


def show_summary(report) -> None:
    if _HAVE_RICH:
        table = Table(title="Run summary", header_style="bold")
        table.add_column("Phase")
        table.add_column("Result")
        table.add_column("Detail", overflow="fold")
        for p in report.phases:
            if p.skipped:
                status = "[dim]skipped[/dim]"
            elif p.ok:
                status = "[green]ok[/green]"
            else:
                status = "[red]FAIL[/red]"
            table.add_row(p.name, status, p.detail)
        _console.print(table)
        if report.snapshot_id:
            _console.print(f"[dim]Snapshot: {report.snapshot_id}[/dim]")
        if report.reboot_pkgs:
            _console.print("[bold yellow]Reboot recommended — updated: "
                           + ", ".join(report.reboot_pkgs) + "[/bold yellow]")
        if report.failed:
            _console.print("[bold red]Run completed with failures.[/bold red]")
        else:
            _console.print("[bold green]Run completed successfully.[/bold green]")
    else:
        _plain("\n--- Run summary ---")
        for p in report.phases:
            status = "skipped" if p.skipped else ("ok" if p.ok else "FAIL")
            _plain(f"  {p.name:<14} {status:<8} {p.detail}")
        if report.snapshot_id:
            _plain(f"Snapshot: {report.snapshot_id}")
        if report.reboot_pkgs:
            _plain("Reboot recommended -- updated: " + ", ".join(report.reboot_pkgs))
        _plain("Run completed with failures." if report.failed
               else "Run completed successfully.")


# -- live spinner --------------------------------------------------------
#
# run_all calls begin_run -> phase_start / phase_done* -> end_run; the runner
# and picker wrap terminal-owning work in suspend(). The animator thread draws
# one line for the running phase; each finished phase prints a permanent line.

_MARK = {"ok": "[green]OK[/green]", "fail": "[red]XX[/red]",
         "skip": "[dim]--[/dim]"}


def _fmt(seconds: float) -> str:
    s = int(max(0, seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def _animate() -> None:
    frames = itertools.cycle(_FRAMES)
    while _anim_stop is not None and not _anim_stop.is_set():
        if _active and _current is not None and not _paused:
            frame = next(frames)
            elapsed = _fmt(time.time() - _run_start)
            with _lock:
                sys.stdout.write(f"\r\033[K\033[36m{frame}\033[0m "
                                 f"\033[1m{_current}\033[0m  \033[2m{elapsed}\033[0m")
                sys.stdout.flush()
        _anim_stop.wait(0.1)


def begin_run(phase_names: list[str]) -> None:
    # phase_names is accepted for symmetry; the linear view doesn't preview them.
    global _active, _paused, _current, _run_start, _anim_thread, _anim_stop
    _run_start = time.time()
    _current = None
    _paused = False
    _active = _dashboard_wanted()
    if _active:
        _anim_stop = threading.Event()
        _anim_thread = threading.Thread(target=_animate, daemon=True)
        _anim_thread.start()


def phase_start(name: str) -> None:
    global _current
    _current = name  # the animator picks it up on its next tick


def phase_done(result) -> None:
    global _current
    _current = None  # stop animating this phase before we print its result
    if not _active:
        return
    mark = (_MARK["skip"] if result.skipped
            else _MARK["ok"] if result.ok else _MARK["fail"])
    _emit(lambda: _console.print(
        f"{mark} [bold]{result.name}[/bold]  [dim]{result.detail}[/dim]"))


@contextlib.contextmanager
def suspend():
    # Hand the terminal to a subprocess / prompt / picker: pause the spinner and
    # wipe its line, then resume for the still-running phase. No-op when idle.
    global _paused
    if not _active:
        yield
        return
    _paused = True
    with _lock:
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
    try:
        yield
    finally:
        _paused = False


def end_run(report) -> None:
    global _active, _current, _anim_thread, _anim_stop
    if _anim_stop is not None:
        _anim_stop.set()
        if _anim_thread is not None:
            _anim_thread.join(timeout=1)
    if _active:
        with _lock:
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()
    elapsed = _fmt(time.time() - _run_start) if _run_start else ""
    _active = False
    _current = None
    _anim_thread = None
    _anim_stop = None
    show_summary(report)
    if elapsed:
        _console.print(f"[dim]elapsed {elapsed}[/dim]") if _HAVE_RICH \
            else _plain(f"elapsed {elapsed}")

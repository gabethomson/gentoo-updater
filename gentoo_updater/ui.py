"""All terminal output goes through here. Uses rich for colour/tables/the live
panel when it's installed, otherwise plain print(). Keeping it in one place means
the fallback is consistent and there's one thing to test."""

from __future__ import annotations

import contextlib
import sys
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


# Live-status state. Module-level because both the updater and the runner talk to
# ui, and the runner needs suspend() to drop the spinner while a subprocess owns
# the terminal. We keep this to a single spinner line (plus a permanent line per
# finished phase) rather than a pinned multi-line panel -- a big panel can't be
# reliably erased once a subprocess has scrolled the screen, which leaves stale
# copies behind. One transient line can.
_PLAIN = False              # --plain
_active = False             # is the live spinner in use this run?
_status = None              # the rich Status (one spinner line), or None
_current: "str | None" = None  # name of the phase currently running
_run_start: float = 0.0


def set_plain(plain: bool) -> None:
    global _PLAIN
    _PLAIN = plain


def _dashboard_wanted() -> bool:
    return _HAVE_RICH and not _PLAIN and sys.stdout.isatty()


# -- basic messages ------------------------------------------------------

def _plain(msg: str) -> None:
    print(msg)


def info(msg: str) -> None:
    if _HAVE_RICH:
        _console.print(msg)
    else:
        _plain(msg)


def dim(msg: str) -> None:
    if _HAVE_RICH:
        _console.print(f"[dim]{msg}[/dim]")
    else:
        _plain(msg)


def warn(msg: str) -> None:
    if _HAVE_RICH:
        _console.print(f"[yellow]! {msg}[/yellow]")
    else:
        _plain(f"! {msg}")


def error(msg: str) -> None:
    if _HAVE_RICH:
        _console.print(f"[bold red]x {msg}[/bold red]")
    else:
        _plain(f"x {msg}")


def hint(msg: str) -> None:
    if _HAVE_RICH:
        _console.print(f"[cyan]  -> {msg}[/cyan]")
    else:
        _plain(f"  -> {msg}")


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
    # A prompt needs the terminal to itself, so drop the live panel while we ask.
    with suspend():
        if _HAVE_RICH:
            try:
                return Confirm.ask(prompt, default=default)
            except (EOFError, KeyboardInterrupt):
                return False
        # plain fallback
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
    if plan.total == 0:
        info("[green]System is up to date -- nothing to do.[/green]"
             if _HAVE_RICH else "System is up to date -- nothing to do.")
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


def show_autounmask(suggestion) -> None:
    warn("The update needs configuration changes before it can proceed.")
    info("Add the following to /etc/portage (review each line before applying):"
         if not _HAVE_RICH else
         "[bold]Add the following to /etc/portage[/bold] "
         "(review each line before applying):")
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
    hint("Once written, run 'dispatch-conf' if needed, then re-run gup.")


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


# -- live status ---------------------------------------------------------
#
# A single spinner line for the running phase; each finished phase prints one
# permanent line above it. `run_all` calls begin_run -> phase_start/phase_done*
# -> end_run, and the runner wraps streamed subprocesses in suspend().

_MARK = {"ok": "[green]OK[/green]", "fail": "[red]XX[/red]",
         "skip": "[dim]--[/dim]"}


def _fmt(seconds: float) -> str:
    s = int(max(0, seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def _spinner_label(name: str) -> str:
    return f"[bold cyan]{name}[/bold cyan] …"


def begin_run(phase_names: list[str]) -> None:
    # phase_names is accepted for symmetry; the linear view doesn't preview them.
    global _active, _status, _current, _run_start
    _run_start = time.time()
    _current = None
    _status = None
    _active = _dashboard_wanted()


def phase_start(name: str) -> None:
    global _status, _current
    _current = name
    if not _active:
        return
    if _status is None:
        _status = _console.status(_spinner_label(name), spinner="dots")
        _status.start()
    else:
        _status.update(_spinner_label(name))


def phase_done(result) -> None:
    global _current
    _current = None
    if not _active:
        return
    mark = (_MARK["skip"] if result.skipped
            else _MARK["ok"] if result.ok else _MARK["fail"])
    # Printed through the console while the status is live -> lands above the
    # spinner as a permanent line.
    _console.print(f"{mark} [bold]{result.name}[/bold]  [dim]{result.detail}[/dim]")


@contextlib.contextmanager
def suspend():
    # Stop the spinner so a subprocess/prompt owns the terminal, then bring it
    # back for the still-running phase. No-op when the spinner isn't up.
    global _status
    if not _active or _status is None:
        yield
        return
    _status.stop()
    _status = None
    try:
        yield
    finally:
        if _current is not None:  # a phase is still running
            _status = _console.status(_spinner_label(_current), spinner="dots")
            _status.start()


def end_run(report) -> None:
    global _active, _status, _current
    if _status is not None:
        _status.stop()
        _status = None
    elapsed = _fmt(time.time() - _run_start) if _run_start else ""
    _active = False
    _current = None
    show_summary(report)
    if elapsed:
        _console.print(f"[dim]elapsed {elapsed}[/dim]") if _HAVE_RICH \
            else _plain(f"elapsed {elapsed}")

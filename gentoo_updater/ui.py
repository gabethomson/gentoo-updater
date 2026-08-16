"""Terminal output helpers.

Uses `rich` when available for colour and tables, but degrades gracefully to
plain print() so the tool works on a minimal box with no extra deps. All
user-facing output goes through here so the styling is consistent and testable.
"""

from __future__ import annotations

import contextlib
import sys
import time

try:
    from rich.console import Console, Group
    from rich.table import Table
    from rich.prompt import Confirm
    from rich.panel import Panel
    from rich.spinner import Spinner
    from rich.text import Text
    from rich.live import Live
    _console: "Console | None" = Console()
    _HAVE_RICH = True
except Exception:  # noqa: BLE001 - rich is optional
    _console = None
    _HAVE_RICH = False


# -- live dashboard state ------------------------------------------------
#
# When rich is present and we're on a real terminal, run_all drives a pinned
# "live" panel showing the phase checklist. It's module-level state (rather than
# an object) because both the updater and the runner talk to ui, and the runner
# needs `suspend()` to step the panel aside while a subprocess streams.
_PLAIN = False              # user forced plain output (--plain)
_live: "Live | None" = None
_phases: list[dict] = []    # [{"name", "status", "detail"}]
_run_start: float = 0.0


def set_plain(plain: bool) -> None:
    """Force plain (non-dashboard) output regardless of terminal/rich."""
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
    # The live dashboard already shows which phase is running, so the rule would
    # just be noise -- skip it while the panel is up.
    if _live is not None:
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
    """Render a parsed PretendPlan."""
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
    """Show emerge's suggested keyword/USE/mask/license changes and where they go."""
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
    """List pre-update snapshots available to roll back to."""
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
    """Prompt for a snapshot by index; return the chosen one or None to cancel."""
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
    """End-of-run summary of every phase."""
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


# -- live dashboard ------------------------------------------------------

_GLYPH = {  # non-running states; the running phase gets an animated spinner
    "ok": ("[green]OK[/green]", ""),
    "fail": ("[red]XX[/red]", "bold red"),
    "skip": ("[dim]--[/dim]", "dim"),
    "pending": ("[dim]..[/dim]", "dim"),
}


def _fmt(seconds: float) -> str:
    s = int(max(0, seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def _render_dashboard():
    """Build the pinned panel from current phase state (rich only)."""
    grid = Table.grid(padding=(0, 1))
    grid.add_column(width=2, justify="center")
    grid.add_column(no_wrap=True)
    grid.add_column(overflow="fold")
    for row in _phases:
        status = row["status"]
        if status == "running":
            marker = Spinner("dots", style="cyan")
            name = Text(row["name"], style="bold cyan")
        else:
            glyph, name_style = _GLYPH.get(status, _GLYPH["pending"])
            marker = Text.from_markup(glyph)
            name = Text(row["name"], style=name_style)
        grid.add_row(marker, name, Text(row.get("detail", ""), style="dim"))
    footer = Text(f"elapsed {_fmt(time.time() - _run_start)}", style="dim")
    return Panel(Group(grid, footer), title="gentoo-updater",
                 title_align="left", border_style="cyan", padding=(0, 1))


def _new_live() -> "Live":
    # transient so that stepping aside (suspend) cleanly erases the panel, and so
    # the final summary -- not a frozen checklist -- is what stays on screen.
    return Live(get_renderable=_render_dashboard, console=_console,
                refresh_per_second=12, transient=True,
                vertical_overflow="visible")


def begin_run(phase_names: list[str]) -> None:
    """Start the live dashboard for a pipeline run (no-op in plain mode)."""
    global _live, _phases, _run_start
    _phases = [{"name": n, "status": "pending", "detail": ""} for n in phase_names]
    _run_start = time.time()
    if not _dashboard_wanted():
        _live = None
        return
    _live = _new_live()
    _live.start()


def _row(name: str) -> "dict | None":
    return next((r for r in _phases if r["name"] == name), None)


def phase_start(name: str) -> None:
    row = _row(name)
    if row is not None:
        row["status"] = "running"
    if _live is not None:
        _live.refresh()


def phase_done(result) -> None:
    row = _row(result.name)
    if row is not None:
        row["status"] = ("skip" if result.skipped
                         else "ok" if result.ok else "fail")
        row["detail"] = result.detail
    if _live is not None:
        _live.refresh()


@contextlib.contextmanager
def suspend():
    """Temporarily tear down the live panel so a subprocess (or prompt) can own
    the terminal, then bring it back. A no-op when the dashboard isn't active."""
    global _live
    if _live is None:
        yield
        return
    _live.stop()
    _live = None
    try:
        yield
    finally:
        # Only resume if the run hasn't been finalized in the meantime.
        if _phases and any(r["status"] in ("pending", "running") for r in _phases):
            _live = _new_live()
            _live.start()


def end_run(report) -> None:
    """Stop the dashboard and print the persistent end-of-run summary."""
    global _live, _phases
    if _live is not None:
        _live.stop()
        _live = None
    _phases = []
    show_summary(report)

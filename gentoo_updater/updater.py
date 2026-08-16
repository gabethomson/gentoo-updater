"""Core update orchestration for a Gentoo system.

Each step is a discrete, named phase so it can be run, skipped, or reported on
individually. The updater shells out to portage tools -- it never tries to do
their work itself, only to wrap them with safety, parsing, and verification.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from enum import Enum

from . import __version__
from .runner import CommandRunner, CommandResult
from .parse import parse_pretend, PretendPlan
from .snapshot import SnapshotManager
from . import advise
from . import audit as _audit
from . import picker
from . import ui

# Where portage writes per-package elog messages (elog:save module). We surface
# new ones after a run so post-merge instructions don't get lost in the scroll.
ELOG_DIR = "/var/log/portage/elog"

# Free space below this (in GiB) in the portage build dir gets a warning before
# a source build -- big toolchain compiles can need several GiB of scratch.
LOW_SPACE_GIB = 5.0


class Risk(Enum):
    """How dangerous a pending change is judged to be."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Packages whose upgrade warrants extra caution -- toolchain, init, kernel,
# core libraries. An update touching these is more likely to need a reboot or
# to break things subtly, so we surface them prominently.
HIGH_RISK_ATOMS = (
    "sys-devel/gcc",
    "sys-libs/glibc",
    "sys-apps/systemd",
    "sys-kernel/",           # any kernel source/bin
    "sys-devel/binutils",
    "sys-devel/clang",
    "llvm-core/llvm",
    "dev-lang/rust",
    "dev-lang/python",
)


def _pkg_label(change) -> str:
    """One-line checklist label for a pending package: 'U cat/pkg  old → new'."""
    tag = ("N" if change.is_new else "U" if change.is_upgrade
           else "R" if change.is_rebuild else " ")
    core = change.atom.split("::", 1)[0]
    prefix = change.name + "-"
    ver = core[len(prefix):] if core.startswith(prefix) else core
    if change.old_version:
        return f"{tag}  {change.name}  {change.old_version} → {ver}"
    return f"{tag}  {change.name}  {ver}"


@dataclass
class PhaseResult:
    name: str
    ok: bool
    detail: str = ""
    skipped: bool = False


@dataclass
class UpdateReport:
    """Accumulated outcome of a full run, for the end-of-run summary."""

    phases: list[PhaseResult] = field(default_factory=list)
    plan: PretendPlan | None = None
    snapshot_id: str | None = None
    reboot_pkgs: list[str] = field(default_factory=list)

    def add(self, result: PhaseResult) -> None:
        self.phases.append(result)

    @property
    def failed(self) -> bool:
        return any(not p.ok and not p.skipped for p in self.phases)


class Updater:
    def __init__(
        self,
        runner: CommandRunner,
        snapshots: SnapshotManager,
        *,
        interactive: bool = True,
        assume_yes: bool = False,
        low_space_gib: float = LOW_SPACE_GIB,
        include_depclean: bool = False,
        select: bool = False,
        selector=None,
        audit_log=None,
        notifier=None,
    ):
        self.run = runner
        self.snapshots = snapshots
        self.interactive = interactive
        self.assume_yes = assume_yes
        self.low_space_gib = low_space_gib
        self.include_depclean = include_depclean
        # Interactive package selection (`--select`): pick which pending packages
        # to update; the rest become `emerge --exclude` atoms. `selector` is
        # injectable for tests; it takes [(name, label)] and returns the checked
        # names (or None to cancel).
        self.select = select
        self.selector = selector or picker.pick
        self._excludes: list[str] = []
        # Optional side-channels: an audit-log writer and a notifier. Injected so
        # tests can supply fakes (or None to disable). See audit.py / notify.py.
        self.audit_log = audit_log
        self.notifier = notifier
        self.report = UpdateReport()
        # Wall-clock start, used to find elog files written during this run.
        self._started = time.time()

    # -- decision helper -------------------------------------------------

    def _confirm(self, prompt: str, *, default: bool = False) -> bool:
        """Ask the user, unless we're running unattended."""
        if self.assume_yes:
            return True
        if not self.interactive:
            return default
        return ui.confirm(prompt, default=default)

    # -- phases ----------------------------------------------------------

    def phase_preflight(self) -> PhaseResult:
        """Sanity checks before touching anything."""
        missing = [t for t in ("emerge", "emaint") if shutil.which(t) is None]
        if missing:
            return PhaseResult(
                "preflight", ok=False,
                detail=f"missing required tools: {', '.join(missing)}",
            )
        # eix / gentoolkit are optional but nice; note their absence.
        optional = {t: shutil.which(t) is not None
                    for t in ("eix", "revdep-rebuild", "eselect", "glsa-check")}
        note = ", ".join(f"{k}={'yes' if v else 'no'}" for k, v in optional.items())

        free = self._portage_free_gib()
        space = ""
        if free is not None:
            space = f"; build space: {free:.1f} GiB free"
            if free < self.low_space_gib:
                ui.warn(f"Only {free:.1f} GiB free in the portage build dir "
                        f"(< {self.low_space_gib:.0f} GiB). A large source build may fail.")
        return PhaseResult("preflight", ok=True,
                           detail=f"optional tools: {note}{space}")

    @staticmethod
    def _portage_build_dir() -> str:
        """Where portage unpacks/builds. Honour PORTAGE_TMPDIR, else the default."""
        base = os.environ.get("PORTAGE_TMPDIR", "/var/tmp")
        candidate = os.path.join(base, "portage")
        return candidate if os.path.isdir(candidate) else base

    def _portage_free_gib(self) -> float | None:
        path = self._portage_build_dir()
        try:
            usage = shutil.disk_usage(path)
        except OSError:
            return None
        return usage.free / (1024 ** 3)

    def phase_news(self) -> PhaseResult:
        """Check for unread Gentoo news. Unread news can carry
        'do X before updating' warnings, so we surface it and, in
        interactive mode, let the user bail to read it first."""
        if shutil.which("eselect") is None:
            return PhaseResult("news", ok=True, skipped=True,
                               detail="eselect not present")
        res = self.run.capture(["eselect", "news", "count", "new"])
        count = res.stdout.strip()
        try:
            n = int(count)
        except ValueError:
            n = 0
        if n == 0:
            return PhaseResult("news", ok=True, detail="no unread news")

        ui.warn(f"{n} unread news item(s). These may contain pre-update warnings.")
        if self.interactive and not self.assume_yes:
            if ui.confirm("Read news now (opens 'eselect news read')?", default=True):
                self.run.interactive(["eselect", "news", "read", "new"])
                if not ui.confirm("Continue with the update?", default=True):
                    return PhaseResult("news", ok=False,
                                       detail="user stopped to act on news")
        return PhaseResult("news", ok=True, detail=f"{n} unread (acknowledged)")

    def phase_sync(self) -> PhaseResult:
        """Sync all repos. A failure of the *main* tree is fatal; a failed
        overlay is a warning (you can usually still update)."""
        res = self.run.stream(["emaint", "sync", "-a"])
        if res.returncode != 0:
            # emaint returns nonzero if ANY repo failed. We can't easily tell
            # which from the exit code alone, so we warn and let the user judge.
            ui.warn("At least one repository failed to sync.")
            if not self._confirm("Continue despite sync failure?", default=False):
                return PhaseResult("sync", ok=False, detail="sync failed, user aborted")
            return PhaseResult("sync", ok=True,
                               detail="sync had failures (continued by choice)")
        # refresh eix cache if present
        if shutil.which("eix-update"):
            self.run.stream(["eix-update"])
        return PhaseResult("sync", ok=True, detail="all repos synced")

    @staticmethod
    def _pretend_cmd(excludes: list[str] | None = None) -> list[str]:
        cmd = ["emerge", "--pretend", "--verbose", "--update",
               "--deep", "--newuse", "--with-bdeps=y", "@world"]
        if excludes:
            # --exclude takes one space-separated list of cat/pkg atoms.
            cmd += ["--exclude", " ".join(excludes)]
        return cmd

    def phase_plan(self) -> PhaseResult:
        """Dry-run the world update and parse what it intends to do."""
        res = self.run.capture(self._pretend_cmd())
        # emerge --pretend returns 0 normally; nonzero can mean blockers or
        # unsatisfied deps that need config changes first.
        plan = parse_pretend(res.stdout, high_risk_atoms=HIGH_RISK_ATOMS)
        self.report.plan = plan

        if res.returncode != 0:
            ui.error("emerge could not resolve the update cleanly.")
            # emerge prints its autounmask suggestions to stderr; combine both.
            suggestion = advise.parse_autounmask(res.stdout + "\n" + res.stderr)
            if suggestion.any:
                ui.show_autounmask(suggestion)
                return PhaseResult(
                    "plan", ok=False,
                    detail="needs config changes (see keyword/USE suggestions)",
                )
            ui.hint("Common causes: blockers, slot conflicts, or needed "
                    "keyword/USE changes. Re-run 'emerge -pv @world' to see detail.")
            ui.show_plan(plan)
            return PhaseResult("plan", ok=False,
                               detail="dependency resolution failed")

        ui.show_plan(plan)
        if plan.total == 0:
            return PhaseResult("plan", ok=True, detail="nothing to update")

        # Optional interactive cherry-pick: uncheck packages to skip this run.
        if self.select and self.interactive:
            selected = self._apply_selection(plan)
            if selected is not None:
                return selected  # selection re-planned; use its result

        if plan.high_risk:
            ui.warn("This update touches high-risk packages: "
                    + ", ".join(sorted(plan.high_risk)))
        return PhaseResult("plan", ok=True,
                           detail=f"{plan.total} package(s) pending")

    def _selectable_items(self, plan: PretendPlan) -> list[tuple[str, str]]:
        """(name, label) rows for the picker, one per distinct package."""
        items: list[tuple[str, str]] = []
        seen: set[str] = set()
        for c in plan.changes:
            if c.name in seen:
                continue
            seen.add(c.name)
            items.append((c.name, _pkg_label(c)))
        return items

    def _apply_selection(self, plan: PretendPlan) -> PhaseResult | None:
        """Run the picker; if anything was unchecked, re-pretend with --exclude
        and return the new plan result. Returns None when the selection is a
        no-op (kept all, or cancelled) so the caller proceeds normally."""
        items = self._selectable_items(plan)
        checked = self.selector(items)
        if checked is None:
            ui.info("Selection cancelled -- keeping all packages.")
            return None
        checked_set = set(checked)
        excludes = [name for name, _ in items if name not in checked_set]
        if not excludes:
            return None  # nothing unchecked

        self._excludes = excludes
        ui.info(f"Excluding {len(excludes)} package(s): " + ", ".join(excludes))
        res = self.run.capture(self._pretend_cmd(excludes))
        plan2 = parse_pretend(res.stdout, high_risk_atoms=HIGH_RISK_ATOMS)
        self.report.plan = plan2

        if res.returncode != 0:
            ui.error("Excluding those packages breaks dependency resolution.")
            ui.hint("Something you kept needs one you skipped. Re-run and keep "
                    "it, or resolve the conflict by hand.")
            ui.show_plan(plan2)
            return PhaseResult("plan", ok=False,
                               detail="exclusion caused a dependency conflict")

        ui.show_plan(plan2)
        if plan2.total == 0:
            return PhaseResult("plan", ok=True,
                               detail="all pending packages were excluded")
        if plan2.high_risk:
            ui.warn("Still touches high-risk packages: "
                    + ", ".join(sorted(plan2.high_risk)))
        return PhaseResult("plan", ok=True,
                           detail=f"{plan2.total} pending after exclusions")

    def phase_glsa(self) -> PhaseResult:
        """Check for known security advisories (GLSAs) affecting the system.
        Informational: a vulnerable package is worth knowing about but never
        blocks the update itself."""
        if shutil.which("glsa-check") is None:
            return PhaseResult("glsa", ok=True, skipped=True,
                               detail="glsa-check not present (install gentoolkit)")
        res = self.run.capture(["glsa-check", "--test", "all"])
        ids = advise.parse_glsa_ids(res.stdout + "\n" + res.stderr)
        if ids:
            ui.warn(f"{len(ids)} security advisory(ies) affect this system: "
                    + ", ".join(ids))
            ui.hint("Review with: glsa-check --list affected  "
                    "(fix with: glsa-check --fix <id>)")
            return PhaseResult("glsa", ok=True,
                               detail=f"{len(ids)} affected: {', '.join(ids)}")
        return PhaseResult("glsa", ok=True, detail="no known vulnerabilities")

    def phase_elog(self) -> PhaseResult:
        """Surface post-merge elog messages emerge wrote during this run.
        These carry manual follow-up steps that scroll past during a big build."""
        pkgs = self._new_elog_packages()
        if not pkgs:
            return PhaseResult("elog", ok=True, detail="no new elog messages")
        ui.warn(f"{len(pkgs)} package(s) left post-install messages "
                f"(in {ELOG_DIR}):")
        for p in pkgs:
            ui.hint(p)
        return PhaseResult("elog", ok=True,
                           detail=f"{len(pkgs)} elog message(s): {', '.join(pkgs)}")

    def _new_elog_packages(self) -> list[str]:
        """Package names whose elog files were written during this run."""
        try:
            entries = os.scandir(ELOG_DIR)
        except OSError:
            return []
        found: list[str] = []
        with entries:
            for e in entries:
                try:
                    if not e.is_file() or e.stat().st_mtime < self._started:
                        continue
                except OSError:
                    continue
                pkg = advise.elog_package_from_filename(e.name)
                if pkg:
                    found.append(pkg)
        return sorted(set(found))

    def phase_snapshot(self) -> PhaseResult:
        """Take a btrfs snapshot before applying, if supported."""
        if not self.snapshots.available:
            return PhaseResult("snapshot", ok=True, skipped=True,
                               detail="btrfs/snapper not available")
        if not self._confirm("Take a pre-update snapshot?", default=True):
            return PhaseResult("snapshot", ok=True, skipped=True,
                               detail="declined by user")
        try:
            snap_id = self.snapshots.create("pre-update (gentoo-updater)")
            self.report.snapshot_id = snap_id
            return PhaseResult("snapshot", ok=True, detail=f"created {snap_id}")
        except Exception as exc:  # noqa: BLE001 - report any snapshot failure
            ui.warn(f"Snapshot failed: {exc}")
            if not self._confirm("Continue without a snapshot?", default=False):
                return PhaseResult("snapshot", ok=False, detail=str(exc))
            return PhaseResult("snapshot", ok=True, skipped=True,
                               detail=f"failed, continued: {exc}")

    def phase_apply(self) -> PhaseResult:
        """The real update. Streamed so the user sees compile progress live."""
        if self.report.plan and self.report.plan.total == 0:
            return PhaseResult("apply", ok=True, skipped=True,
                               detail="nothing to do")
        if not self._confirm("Apply the update now?", default=False):
            return PhaseResult("apply", ok=False, detail="user declined apply")

        cmd = ["emerge", "--update", "--deep", "--newuse",
               "--with-bdeps=y", "--keep-going", "@world"]
        if self._excludes:
            # Apply the same exclusions the user picked at the plan step.
            cmd += ["--exclude", " ".join(self._excludes)]
        res = self.run.stream(cmd)
        if res.returncode != 0:
            ui.error("emerge @world exited non-zero. Some packages may have failed.")
            ui.hint("Inspect with: emerge --resume  (or --resume --skipfirst)")
            return PhaseResult("apply", ok=False, detail="emerge failed")
        return PhaseResult("apply", ok=True, detail="world updated")

    def phase_postupdate(self) -> PhaseResult:
        """Rebuild consumers of replaced libs and out-of-tree modules."""
        overall_ok = True
        details = []

        pres = self.run.stream(["emerge", "@preserved-rebuild"])
        details.append(f"preserved-rebuild rc={pres.returncode}")
        overall_ok &= pres.returncode == 0

        # @module-rebuild only matters if there are external modules; it's
        # harmless (fast no-op) if there aren't.
        mres = self.run.stream(["emerge", "@module-rebuild"])
        details.append(f"module-rebuild rc={mres.returncode}")
        overall_ok &= mres.returncode == 0

        return PhaseResult("post-update", ok=overall_ok, detail=", ".join(details))

    def phase_config(self) -> PhaseResult:
        """Handle pending config-file updates (._cfg files)."""
        # dispatch-conf is interactive by nature. In unattended mode we don't
        # auto-merge configs -- that's too risky -- we just report they exist.
        pending = self._count_pending_configs()
        if pending == 0:
            return PhaseResult("config", ok=True, detail="no config updates")
        if self.interactive and not self.assume_yes:
            if ui.confirm(f"{pending} config update(s) pending. Run dispatch-conf?",
                          default=True):
                self.run.interactive(["dispatch-conf"])
                return PhaseResult("config", ok=True, detail="dispatch-conf run")
        ui.warn(f"{pending} config file update(s) pending -- run 'dispatch-conf'.")
        return PhaseResult("config", ok=True, detail=f"{pending} pending (deferred)")

    def phase_depclean(self) -> PhaseResult:
        """Remove orphaned packages (not required by @world), opt-in and careful.

        Always pretends first and shows the count. Removal is gated on an
        explicit confirmation because depclean can drop packages you actually
        want but never added to @world. `emerge --depclean` itself refuses
        removals that would break reverse dependencies."""
        res = self.run.capture(["emerge", "--depclean", "--pretend"])
        removable = advise.parse_depclean_count(res.stdout)
        if removable == 0:
            return PhaseResult("depclean", ok=True, detail="no orphaned packages")

        ui.warn(f"{removable} orphaned package(s) could be removed by depclean.")
        ui.hint("Review carefully: depclean removes anything not pulled in by "
                "@world. List it with 'emerge --depclean --pretend'.")
        if not self._confirm("Run 'emerge --depclean' now?", default=False):
            return PhaseResult("depclean", ok=True,
                               detail=f"{removable} removable (deferred)")

        r = self.run.stream(["emerge", "--depclean"])
        if r.returncode != 0:
            return PhaseResult("depclean", ok=False, detail="depclean failed")
        return PhaseResult("depclean", ok=True,
                           detail=f"removed up to {removable} package(s)")

    def phase_verify(self) -> PhaseResult:
        """Post-update health checks that don't change anything."""
        details = []
        ok = True

        # broken library linkage
        if shutil.which("revdep-rebuild"):
            r = self.run.capture(["revdep-rebuild", "-p", "-i"])
            broken = "broken" in r.stdout.lower() and "no broken" not in r.stdout.lower()
            details.append("linkage:BROKEN" if broken else "linkage:ok")
            ok &= not broken
            if broken:
                ui.warn("Broken library linkage detected. Run: sudo revdep-rebuild")
        else:
            details.append("linkage:skipped")

        # anything still needing a preserved rebuild?
        r2 = self.run.capture(["emerge", "-p", "@preserved-rebuild"])
        needs = "Total: 0 packages" not in r2.stdout
        details.append("preserved:pending" if needs else "preserved:clean")
        ok &= not needs

        return PhaseResult("verify", ok=ok, detail=", ".join(details))

    def _count_pending_configs(self) -> int:
        """Count ._cfg* files portage has staged for review."""
        # These live scattered under /etc (and elsewhere in CONFIG_PROTECT dirs).
        # We use find rather than parsing portage internals for robustness.
        res = self.run.capture(
            ["find", "/etc", "-name", "._cfg????_*", "-type", "f"]
        )
        return len([ln for ln in res.stdout.splitlines() if ln.strip()])

    # -- orchestration ---------------------------------------------------

    def run_all(self, *, skip_snapshot: bool = False,
                skip_sync: bool = False) -> UpdateReport:
        """Execute the full pipeline in order, stopping on fatal failures."""
        sequence = [
            ("preflight", self.phase_preflight, True),
            ("news", self.phase_news, True),
            ("sync", self.phase_sync, not skip_sync),
            ("plan", self.phase_plan, True),
            ("glsa", self.phase_glsa, True),
            ("snapshot", self.phase_snapshot, not skip_snapshot),
            ("apply", self.phase_apply, True),
            ("config", self.phase_config, True),
            ("post-update", self.phase_postupdate, True),
        ]
        # depclean is opt-in; only add it to the pipeline when enabled, so the
        # common run doesn't carry a noisy "skipped" row for a feature that's off.
        if self.include_depclean:
            sequence.append(("depclean", self.phase_depclean, True))
        sequence += [
            ("elog", self.phase_elog, True),
            ("verify", self.phase_verify, True),
        ]

        ui.begin_run([name for name, _, _ in sequence])
        try:
            for name, fn, enabled in sequence:
                ui.phase_start(name)
                if not enabled:
                    skipped = PhaseResult(name, ok=True, skipped=True,
                                          detail="skipped by flag")
                    self.report.add(skipped)
                    ui.phase_done(skipped)
                    continue

                ui.phase_header(name)
                result = fn()
                self.report.add(result)
                ui.phase_done(result)

                if not result.ok and not result.skipped:
                    # Fatal phases stop the run. 'plan' failing means we never
                    # apply; 'apply' failing means we still run verify to report.
                    if name in ("preflight", "news", "sync", "plan", "snapshot"):
                        ui.error(f"Phase '{name}' failed: {result.detail}. Stopping.")
                        break
        finally:
            self._compute_reboot_advice()
            ui.end_run(self.report)

        self._finalize(command="update")
        return self.report

    def _finalize(self, *, command: str) -> None:
        """Best-effort side-channels after a run: audit trail + notification.
        Neither is allowed to raise into the caller -- the update already
        happened; recording it is secondary."""
        if self.audit_log is not None:
            try:
                record = _audit.build_record(
                    self.report, started=self._started, finished=time.time(),
                    version=__version__, command=command,
                )
                self.audit_log.record(record)
            except Exception as exc:  # noqa: BLE001 - never fail a run over logging
                ui.dim(f"(audit log skipped: {exc})")
        if self.notifier is not None:
            try:
                self.notifier.maybe_notify(self.report, command=command)
            except Exception as exc:  # noqa: BLE001 - notifications are best-effort
                ui.dim(f"(notification skipped: {exc})")

    def _compute_reboot_advice(self) -> None:
        """If the applied update touched kernel/glibc/systemd/dbus, record that a
        reboot is advisable so the summary can flag it."""
        applied = next((p for p in self.report.phases if p.name == "apply"), None)
        if not applied or applied.skipped or not applied.ok:
            return
        if not self.report.plan:
            return
        names = [c.name for c in self.report.plan.changes]
        self.report.reboot_pkgs = advise.packages_needing_reboot(names)

    def run_depclean(self) -> int:
        """Standalone `gup depclean`: preflight, then the depclean phase only."""
        pre = self.phase_preflight()
        self.report.add(pre)
        if not pre.ok:
            ui.error(f"Preflight failed: {pre.detail}")
            ui.show_summary(self.report)
            return 1
        ui.phase_header("depclean")
        result = self.phase_depclean()
        self.report.add(result)
        ui.show_summary(self.report)
        self._finalize(command="depclean")
        return 0 if result.ok else 1

    # -- rollback --------------------------------------------------------

    def run_rollback(self) -> int:
        """Interactive-ish rollback: list our snapshots, pick one, restore it."""
        if not self.snapshots.available:
            ui.error("No snapshot backend (snapper/btrfs) available -- nothing to "
                     "roll back to.")
            return 1
        snaps = self.snapshots.list_snapshots()
        if not snaps:
            ui.info("No pre-update snapshots created by gentoo-updater were found.")
            return 0

        ui.show_snapshots(snaps)
        choice = self._choose_snapshot(snaps)
        if choice is None:
            ui.info("Rollback cancelled.")
            return 0

        if not self._confirm(
                f"Roll back to {choice.ident} ({choice.when})? "
                "This changes what your system boots into.", default=False):
            ui.info("Rollback cancelled.")
            return 0
        try:
            msg = self.snapshots.rollback(choice.ident)
        except Exception as exc:  # noqa: BLE001 - surface any rollback failure
            ui.error(f"Rollback failed: {exc}")
            return 1
        ui.info(msg)
        return 0

    def _choose_snapshot(self, snaps):
        """Pick a snapshot: newest under -y/non-interactive, else prompt."""
        if self.assume_yes or not self.interactive:
            return snaps[-1]  # newest
        return ui.select_snapshot(snaps)

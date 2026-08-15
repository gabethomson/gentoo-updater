"""Pre-update snapshot handling.

Prefers snapper (if a 'root' config exists) because it integrates with the
rest of the snapper tooling the user may already rely on. Falls back to raw
`btrfs subvolume snapshot` if snapper isn't configured but the root is btrfs.
If neither applies, snapshots are simply unavailable and the updater skips them.
"""

from __future__ import annotations

import datetime as _dt
import os
import shutil
from dataclasses import dataclass

from .runner import CommandRunner

# Description we stamp on snapshots we create, so `gup rollback` can tell ours
# apart from snapshots made by other tooling.
SNAPSHOT_TAG = "gentoo-updater"


@dataclass
class SnapshotInfo:
    ident: str           # backend-native id: snapper number or btrfs path
    backend: str         # "snapper" | "btrfs"
    when: str            # human-readable timestamp
    description: str      # snapper description, or the path for btrfs


class SnapshotManager:
    def __init__(self, runner: CommandRunner):
        self.run = runner
        self._backend = self._detect_backend()

    @property
    def available(self) -> bool:
        return self._backend is not None

    @property
    def backend(self) -> str | None:
        return self._backend

    def _detect_backend(self) -> str | None:
        # snapper with a configured 'root' config is the preferred path
        if shutil.which("snapper"):
            res = self.run.capture(["snapper", "list-configs"])
            if res.returncode == 0 and _has_root_config(res.stdout):
                return "snapper"
        # fall back to raw btrfs if / is btrfs
        if shutil.which("btrfs") and _root_is_btrfs(self.run):
            return "btrfs"
        return None

    def create(self, description: str) -> str:
        """Create a pre-update snapshot, returning an identifier string.

        Raises on failure so the caller can decide whether to proceed.
        """
        if self._backend == "snapper":
            return self._create_snapper(description)
        if self._backend == "btrfs":
            return self._create_btrfs(description)
        raise RuntimeError("no snapshot backend available")

    # -- listing / rollback ---------------------------------------------

    def list_snapshots(self) -> list[SnapshotInfo]:
        """Pre-update snapshots this tool created, newest last."""
        if self._backend == "snapper":
            res = self.run.capture([
                "snapper", "-c", "root", "list",
                "--columns", "number,date,description",
            ])
            return parse_snapper_list(res.stdout, tag=SNAPSHOT_TAG)
        if self._backend == "btrfs":
            res = self.run.capture([
                "find", "/.snapshots", "-maxdepth", "1",
                "-name", f"{SNAPSHOT_TAG}-*",
            ])
            return parse_btrfs_snapshots(res.stdout)
        return []

    def rollback(self, ident: str) -> str:
        """Roll the system back to a snapshot.

        snapper: performs a real `snapper rollback` (takes effect on reboot).
        btrfs: raises with manual instructions -- automating a raw subvolume
        swap can leave an unbootable system, so we refuse to guess.
        """
        if self._backend == "snapper":
            num = ident.split("#", 1)[-1]
            res = self.run.capture(["snapper", "-c", "root", "rollback", num])
            if res.returncode != 0:
                raise RuntimeError(res.stderr.strip() or "snapper rollback failed")
            return (f"snapper rollback to {ident} staged. "
                    "Reboot to boot into the restored state.")
        if self._backend == "btrfs":
            raise RuntimeError(
                "Automatic rollback of a raw btrfs snapshot is not performed "
                "(it risks an unbootable system). Restore manually, e.g.:\n"
                f"  mount the top-level subvolume, move the live root aside, and\n"
                f"  `btrfs subvolume snapshot {ident} <new-root>`, then update your\n"
                "  bootloader/fstab to point at it."
            )
        raise RuntimeError("no snapshot backend available")

    def _create_snapper(self, description: str) -> str:
        # --print-number gives us the snapshot id back for the report/rollback
        res = self.run.capture([
            "snapper", "-c", "root", "create",
            "--description", description,
            "--cleanup-algorithm", "number",
            "--print-number",
        ])
        if res.returncode != 0:
            raise RuntimeError(res.stderr.strip() or "snapper create failed")
        num = res.stdout.strip()
        return f"snapper#{num}"

    def _create_btrfs(self, description: str) -> str:
        # A conservative, self-contained scheme: read-only snapshot of the root
        # subvolume into /.snapshots/<timestamp>. This assumes a subvolume
        # layout where / is a btrfs subvolume and /.snapshots exists or can be
        # created. We keep it read-only so it's a pure restore point.
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = f"/.snapshots/gentoo-updater-{stamp}"
        # ensure /.snapshots exists
        self.run.capture(["mkdir", "-p", "/.snapshots"])
        res = self.run.capture(["btrfs", "subvolume", "snapshot", "-r", "/", dest])
        if res.returncode != 0:
            raise RuntimeError(res.stderr.strip() or "btrfs snapshot failed")
        return dest


def _has_root_config(list_configs_output: str) -> bool:
    for line in list_configs_output.splitlines():
        # snapper list-configs prints a table; the config name is the 1st col
        first = line.split("|", 1)[0].strip()
        if first == "root":
            return True
    return False


def _root_is_btrfs(runner: CommandRunner) -> bool:
    res = runner.capture(["findmnt", "-n", "-o", "FSTYPE", "/"])
    return res.stdout.strip() == "btrfs"


def parse_snapper_list(text: str, *, tag: str) -> list[SnapshotInfo]:
    """Parse `snapper list --columns number,date,description` output, keeping
    only rows whose description carries our tag."""
    out: list[SnapshotInfo] = []
    for line in text.splitlines():
        if "|" not in line:
            continue
        cols = [c.strip() for c in line.split("|")]
        if len(cols) < 3:
            continue
        number, date, description = cols[0], cols[1], cols[2]
        if not number.isdigit():
            continue  # header/separator rows
        if tag not in description:
            continue
        out.append(SnapshotInfo(
            ident=f"snapper#{number}", backend="snapper",
            when=date, description=description,
        ))
    return out


def parse_btrfs_snapshots(find_output: str) -> list[SnapshotInfo]:
    """Turn `find /.snapshots -name gentoo-updater-*` output into snapshot info,
    reading the timestamp out of the directory name (gentoo-updater-YYYYmmdd-HHMMSS)."""
    out: list[SnapshotInfo] = []
    for path in find_output.splitlines():
        path = path.strip()
        if not path:
            continue
        name = os.path.basename(path)
        stamp = name.replace("gentoo-updater-", "", 1)
        out.append(SnapshotInfo(
            ident=path, backend="btrfs", when=stamp, description=path,
        ))
    out.sort(key=lambda s: s.when)
    return out

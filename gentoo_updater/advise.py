"""Pure parsing/advisory helpers, kept free of side effects so they're testable.

Everything here takes text (or plain data) in and returns structured data out --
no subprocess, no filesystem. The updater wires these to real command output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- reboot advisory ----------------------------------------------------

# Updating any of these means the running system no longer matches what's on
# disk in a way a reboot (or at least a re-exec of init) resolves. We advise,
# never force.
REBOOT_PREFIXES = (
    "sys-kernel/",        # kernel image / sources / linux-firmware
    "sys-libs/glibc",
    "sys-apps/systemd",
    "sys-apps/dbus",
)


def packages_needing_reboot(names) -> list[str]:
    """Subset of package names (category/pkg) whose update warrants a reboot."""
    hits = {
        n for n in names
        if any(n == p.rstrip("/") or n.startswith(p) for p in REBOOT_PREFIXES)
    }
    return sorted(hits)


# --- autounmask suggestions --------------------------------------------

# emerge prints one block per change kind when resolution needs config edits.
# We map the human header to the portage file the user would edit.
_AUTOUNMASK_SECTIONS = (
    ("keyword", "keyword changes are necessary", "package.accept_keywords"),
    ("use", "USE changes are necessary", "package.use"),
    ("mask", "mask changes are necessary", "package.unmask / package.mask"),
    ("license", "license changes are necessary", "package.license"),
)


@dataclass
class AutounmaskChange:
    kind: str            # keyword | use | mask | license
    target_file: str     # which /etc/portage file this goes in
    lines: list[str]     # the atom lines emerge suggests


@dataclass
class AutounmaskSuggestion:
    changes: list[AutounmaskChange] = field(default_factory=list)

    @property
    def any(self) -> bool:
        return bool(self.changes)


def _is_suggestion_line(line: str) -> bool:
    """A real atom line, not a note/comment/blank from emerge's block."""
    s = line.strip()
    if not s:
        return False
    if s.startswith("(") or s.startswith("!!!"):
        return False  # "(see ... man page)" notes
    # atom lines start with a version operator (>=, <=, ~, =, ...) or a
    # category/name token directly
    return bool(re.match(r"^[<>=~]*[a-z0-9]", s))


def parse_autounmask(text: str) -> AutounmaskSuggestion:
    """Extract emerge's suggested keyword/USE/mask/license changes from a failed
    `emerge --pretend` (its stdout+stderr). Tolerant: unrecognized output yields
    an empty suggestion rather than an error."""
    lines = text.splitlines()
    suggestion = AutounmaskSuggestion()

    i = 0
    while i < len(lines):
        line = lines[i]
        section = next((s for s in _AUTOUNMASK_SECTIONS if s[1] in line), None)
        if section is None:
            i += 1
            continue
        kind, _needle, target = section
        collected: list[str] = []
        j = i + 1
        # collect until a blank line ends the block, skipping note lines
        while j < len(lines):
            nxt = lines[j]
            if nxt.strip() == "" and collected:
                break
            if _is_suggestion_line(nxt):
                collected.append(nxt.strip())
            elif nxt.strip() == "" and not collected:
                # tolerate a blank right after the header before atoms appear
                pass
            elif collected and not nxt.strip().startswith(("#", "(")):
                # a non-atom, non-comment line after atoms -> block ended
                break
            j += 1
        if collected:
            suggestion.changes.append(
                AutounmaskChange(kind=kind, target_file=target, lines=collected)
            )
        i = j + 1
    return suggestion


# --- GLSA (security advisories) -----------------------------------------

_GLSA_ID = re.compile(r"^\s*(\d{6}-\d+)\b")


def parse_glsa_ids(text: str) -> list[str]:
    """Pull affected GLSA ids (e.g. 202501-07) from `glsa-check -t all` output."""
    ids = []
    for line in text.splitlines():
        m = _GLSA_ID.match(line)
        if m:
            ids.append(m.group(1))
    return ids


# --- depclean (orphan removal) ------------------------------------------

# An unmerge-candidate line in `emerge --depclean -p` is a bare cat/pkg atom at
# column 0-1; the follow-up "selected:"/"protected:" detail lines are indented
# and slash-free, so they don't match.
_DEPCLEAN_ATOM = re.compile(r"^ ?([a-z0-9]+(?:-[a-z0-9]+)*)/[a-z0-9][a-z0-9+_.-]*\s*$")
_DEPCLEAN_COUNT = re.compile(r"Number (?:to remove|removed):\s*(\d+)")


def parse_depclean_count(text: str) -> int:
    """How many packages `emerge --depclean --pretend` would remove.

    Prefers portage's own "Number to remove: N" line when present; otherwise
    counts atom lines under the "would be unmerged" header. Returns 0 when
    nothing is removable (or the output is unrecognised)."""
    for line in text.splitlines():
        m = _DEPCLEAN_COUNT.search(line)
        if m:
            return int(m.group(1))
    in_block = False
    count = 0
    for line in text.splitlines():
        if "would be unmerged" in line:
            in_block = True
            continue
        if in_block and _DEPCLEAN_ATOM.match(line):
            count += 1
    return count


# --- elog (post-merge messages) -----------------------------------------

# elog filenames look like: cat:pkg-version:YYYYMMDD-HHMMSS.log
_ELOG_NAME = re.compile(r"^([a-z0-9+_.-]+):([a-z0-9+_.-]+):\d{8}-\d{6}\.log$")


def elog_package_from_filename(fname: str) -> str | None:
    """Turn an elog filename into 'category/package-version', or None if it
    doesn't look like a portage elog file."""
    m = _ELOG_NAME.match(fname.strip())
    if not m:
        return None
    return f"{m.group(1)}/{m.group(2)}"

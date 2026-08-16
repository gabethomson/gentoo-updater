"""Text parsers: command output in, structured data out. No I/O, so they're
easy to test. The updater feeds them real emerge/glsa/news output."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- reboot advisory ----------------------------------------------------

# Updating these means the running system no longer matches disk in a way only
# a reboot fixes. We advise, we don't force.
REBOOT_PREFIXES = (
    "sys-kernel/",        # kernel image / sources / linux-firmware
    "sys-libs/glibc",
    "sys-apps/systemd",
    "sys-apps/dbus",
)


def packages_needing_reboot(names) -> list[str]:
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
    # A real atom line, not a "(see ... man page)" note or a blank.
    s = line.strip()
    if not s:
        return False
    if s.startswith("(") or s.startswith("!!!"):
        return False
    # atoms start with a version operator (>=, ~, =, ...) or the category
    return bool(re.match(r"^[<>=~]*[a-z0-9]", s))


def parse_autounmask(text: str) -> AutounmaskSuggestion:
    # Pull emerge's suggested keyword/USE/mask/license edits out of a failed
    # pretend. Unrecognised output -> empty, never an error.
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
    # Affected GLSA ids (e.g. 202501-07) from `glsa-check -t all`.
    ids = []
    for line in text.splitlines():
        m = _GLSA_ID.match(line)
        if m:
            ids.append(m.group(1))
    return ids


# --- news correlation ---------------------------------------------------
#
# GLEP 42 news items have a Display-If-Installed header naming a package the
# item is about. Match those against the pending update so news about a package
# you're upgrading shows up at the plan step, not buried in `eselect news`.

_NEWS_TITLE = re.compile(r"^Title:\s*(.+?)\s*$")
_NEWS_IF_INSTALLED = re.compile(r"^Display-If-Installed:\s*(.+?)\s*$")


@dataclass
class NewsItem:
    title: str
    if_installed: list[str] = field(default_factory=list)  # cat/pkg names


def news_atom_name(atom: str) -> str:
    # '>=sys-apps/portage-3.0' -> 'sys-apps/portage'.
    a = atom.strip()
    a = re.sub(r"^[<>=~!]+", "", a)     # version operators
    a = a.split("::", 1)[0]             # ::repo
    a = a.split(":", 1)[0]              # :slot
    a = re.sub(r"-[0-9][^/]*$", "", a)  # -version on the last path segment
    return a


def parse_news_item(text: str) -> NewsItem:
    # Title + Display-If-Installed names. Headers are above the first blank line.
    title = ""
    installed: list[str] = []
    for line in text.splitlines():
        if line.strip() == "":
            break  # end of headers; the body follows
        m = _NEWS_TITLE.match(line)
        if m and not title:
            title = m.group(1)
            continue
        m = _NEWS_IF_INSTALLED.match(line)
        if m:
            installed.append(news_atom_name(m.group(1)))
    return NewsItem(title=title, if_installed=installed)


def relevant_news(items, package_names) -> list[tuple[str, list[str]]]:
    # [(title, [matched cat/pkg])] for items that touch a pending package.
    names = set(package_names)
    out: list[tuple[str, list[str]]] = []
    for it in items:
        hits = sorted({p for p in it.if_installed if p in names})
        if hits:
            out.append((it.title, hits))
    return out


# --- depclean (orphan removal) ------------------------------------------

# An unmerge-candidate line in `emerge --depclean -p` is a bare cat/pkg atom at
# column 0-1; the follow-up "selected:"/"protected:" detail lines are indented
# and slash-free, so they don't match.
_DEPCLEAN_ATOM = re.compile(r"^ ?([a-z0-9]+(?:-[a-z0-9]+)*)/[a-z0-9][a-z0-9+_.-]*\s*$")
_DEPCLEAN_COUNT = re.compile(r"Number (?:to remove|removed):\s*(\d+)")


def parse_depclean_count(text: str) -> int:
    # How many `emerge --depclean -p` would remove. Trust portage's own
    # "Number to remove: N" if it's there, else count the atom lines.
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
    # 'cat:pkg-ver:stamp.log' -> 'cat/pkg-ver', or None if it's not one.
    m = _ELOG_NAME.match(fname.strip())
    if not m:
        return None
    return f"{m.group(1)}/{m.group(2)}"

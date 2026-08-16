"""Optional configuration file support.

A config file lets a user pin their preferred defaults (unattended mode, snapshot
policy, notification and audit settings) so they don't have to pass the same
flags every run -- handy for a systemd timer that runs `gup` with no arguments.

Precedence, lowest to highest:
  1. built-in defaults (this dataclass)
  2. /etc/gentoo-updater.toml           (system-wide)
  3. ~/.config/gentoo-updater/config.toml   (per-user)
  4. command-line flags

Parsing is TOML via the stdlib `tomllib` (Python 3.11+). On older interpreters,
or when no file exists, we silently fall back to the built-in defaults -- the
config file is a convenience, never a requirement.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields

try:  # tomllib is stdlib from 3.11; degrade gracefully below it.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - depends on interpreter version
    tomllib = None  # type: ignore[assignment]

SYSTEM_PATH = "/etc/gentoo-updater.toml"


def _user_path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "gentoo-updater", "config.toml")


# Recognised notification triggers.
NOTIFY_CHOICES = ("never", "failure", "reboot", "always")


@dataclass
class Config:
    """Effective settings for a run. Field names match CLI concepts so the two
    can be merged mechanically."""

    # behaviour
    yes: bool = False
    non_interactive: bool = False
    no_snapshot: bool = False
    no_sync: bool = False
    no_sudo: bool = False
    depclean: bool = False           # include a depclean step in the pipeline
    select: bool = False             # interactively cherry-pick packages to update

    # thresholds
    low_space_gib: float = 5.0

    # notifications
    notify: str = "never"            # one of NOTIFY_CHOICES
    notify_desktop: bool = True      # use notify-send if notifying
    notify_email: str = ""           # address to mail; empty disables email

    # audit trail
    audit: bool = True               # append a JSONL record per run
    audit_path: str = ""             # empty -> AuditLog picks a default

    def merged_with_cli(self, cli: dict) -> "Config":
        """Return a copy overridden by any CLI values that were explicitly set.

        `cli` maps field names to values, where None means 'not given on the
        command line' and should leave the config value untouched.
        """
        values = {f.name: getattr(self, f.name) for f in fields(self)}
        for key, val in cli.items():
            if val is not None and key in values:
                values[key] = val
        return Config(**values)


def _coerce(raw: dict) -> dict:
    """Keep only recognised keys and coerce them to the field types. Unknown
    keys are ignored rather than fatal, so a newer config on an older tool (or a
    typo) degrades instead of crashing."""
    known = {f.name: f.type for f in fields(Config)}
    out: dict = {}
    for key, val in raw.items():
        norm = key.replace("-", "_")
        if norm not in known:
            continue
        if norm == "low_space_gib":
            try:
                out[norm] = float(val)
            except (TypeError, ValueError):
                continue
        elif norm in ("notify", "notify_email", "audit_path"):
            out[norm] = str(val)
        else:  # the booleans
            out[norm] = bool(val)
    if "notify" in out and out["notify"] not in NOTIFY_CHOICES:
        del out["notify"]  # ignore an invalid trigger, keep the default
    return out


def parse_config_text(text: str) -> dict:
    """Parse TOML config text into a coerced dict of known settings. Tolerant:
    a syntax error or missing tomllib yields an empty dict (defaults win)."""
    if tomllib is None:
        return {}
    try:
        raw = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return {}
    # accept either top-level keys or a [gentoo-updater] table
    section = raw.get("gentoo-updater")
    if isinstance(section, dict):
        raw = {**raw, **section}
    return _coerce(raw)


def load_config(paths: list[str] | None = None) -> Config:
    """Load and layer config files (later paths override earlier). Missing or
    unreadable files are skipped."""
    if paths is None:
        paths = [SYSTEM_PATH, _user_path()]
    merged: dict = {}
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        merged.update(parse_config_text(text))
    return Config(**merged) if merged else Config()

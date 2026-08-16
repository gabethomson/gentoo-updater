# gentoo-updater (`gup`)

A wrapper around Gentoo world updates. It runs the same commands you'd type by
hand, in the right order, and adds the checks a bare update sequence skips: a
snapshot first, risk flagging, news, and health checks after.

Every command it runs is printed to the terminal, prefixed with `$`. Nothing is
hidden.

```
── gentoo-updater ───────────────────────────────
  OK preflight   optional tools ok; 42.1 GiB free
  OK news        no unread news
  OK sync        all repos synced
  OK plan        17 package(s) pending
  OK glsa        no known vulnerabilities
  OK snapshot    created snapper#42
  ⠋  apply
     config
     verify
  elapsed 04:18
```

## What it does

A normal update is a sequence with decision points:

```bash
emaint sync -a
eselect news read
emerge -avuDN --with-bdeps=y @world
dispatch-conf
emerge @preserved-rebuild
emerge @module-rebuild
```

`gup` runs that, and also:

- Takes a btrfs/snapper snapshot before applying (`gup rollback` restores it).
- Flags high-risk packages in the plan (gcc, glibc, systemd, kernel, llvm, rust, python).
- Lets you cherry-pick packages to update with `--select`.
- Runs `glsa-check` for known vulnerabilities.
- Warns on unread news, and flags any unread item that's about a package you're updating.
- Shows the exact `/etc/portage` lines to add when the resolver needs keyword/USE changes.
- Checks for broken linkage, pending preserved rebuilds, and post-merge `elog` messages afterward.
- Advises a reboot when the update touched the kernel, glibc, systemd, or dbus.
- Optional: JSONL audit log, desktop/email notifications, scheduled runs, and depclean.

## Install

It's pure stdlib, so it doesn't need `pip` — which is good, since Gentoo blocks
`pip install` into the system Python (PEP 668). Pick whichever fits.

**Run it from the clone** (no install):

```bash
git clone https://github.com/gabethomson/gentoo-updater
cd gentoo-updater
python -m gentoo_updater --help
```

**pipx** — gives you a `gup` command in its own venv:

```bash
emerge -av dev-python/pipx
pipx install git+https://github.com/gabethomson/gentoo-updater
pipx install 'gentoo-updater[pretty] @ git+https://github.com/gabethomson/gentoo-updater'  # with rich
```

**ebuild** — the Gentoo-native way, from the bundled overlay. A live `-9999`
ebuild builds straight from `main` (no distfile, no Manifest), and there's a
pinned release ebuild too:

```bash
# after adding the overlay (see contrib/overlay/README.md):
echo "app-admin/gentoo-updater **" | sudo tee /etc/portage/package.accept_keywords/gentoo-updater
sudo emerge -av =app-admin/gentoo-updater-9999
```

See [`contrib/overlay/`](contrib/overlay/) for full setup.

The only optional dependency is `rich` (`dev-python/rich`) for colour, tables,
and the live dashboard; without it the output is plain text.

## Usage

```bash
gup                  # full update, interactive
gup -y                # unattended: assume yes, apply automatically
gup --dry-run         # print what would run, change nothing
gup --select          # pick which packages to update (checklist)

gup plan              # show the pending update only (no sync/apply)
gup verify            # post-update health checks only
gup news              # show/read pending news
gup rollback          # restore a pre-update snapshot
gup depclean          # remove orphaned packages (asks first)
gup install-schedule  # set up unattended runs (auto-detects your init)
gup install-timer     # = install-schedule --init systemd
```

### Flags

| Flag | Effect |
|---|---|
| `-y`, `--yes` | Assume yes to all prompts; apply automatically |
| `--non-interactive` | Never prompt; report where prompts would be (won't auto-apply unless `-y`) |
| `--dry-run` | Never run a mutating command; print what would run |
| `--plain` | No live dashboard; plain linear output |
| `--select` | Pick which pending packages to update |
| `--no-snapshot` | Skip the pre-update snapshot |
| `--no-sync` | Skip the repo sync |
| `--no-sudo` | Don't prepend `sudo` (already root) |
| `--depclean` | Also run a depclean step (pretends first, asks before removing) |
| `--notify WHEN` | Notify on completion: `never` / `failure` / `reboot` / `always` |
| `--no-audit` | Don't append a run record to the audit log |
| `--config PATH` | Use only this config file |
| `--init WHICH` | Target init for `install-schedule`: `auto`, `systemd`, `openrc`, `runit`, `cron` |
| `--schedule PERIOD` | Cadence for `install-schedule`: `daily`/`weekly`/`monthly` |

Interactive (default) prompts before applying, before the snapshot, and stops to
let you read news. `-y` runs the whole thing with no input — that's what the
scheduler uses.

## While it runs

With `rich` on a real terminal, `gup` shows a live checklist pinned to the bottom
of the screen: a spinner on the current phase and a running clock. Finished
phases show `OK` (or a red one on failure).

During the streaming phases (sync, the real `emerge`, `dispatch-conf`) the panel
drops so Portage streams its own output normally, then comes back. Piped output,
cron, or no `rich` falls back to plain output. Force that anywhere with `--plain`.

## Phases

1. **preflight** — check `emerge`/`emaint` exist, note optional tools, warn on low build-dir space
2. **news** — check for unread news, offer to read it
3. **sync** — `emaint sync -a`
4. **plan** — `emerge -pvuDN @world`, parsed and risk-flagged, cross-referenced against unread news; shows the `/etc/portage` fix on a masked resolution
5. **glsa** — `glsa-check` for known advisories (informational)
6. **snapshot** — btrfs/snapper restore point
7. **apply** — the real `@world` update
8. **config** — pending `._cfg` files via `dispatch-conf`
9. **post-update** — `@preserved-rebuild` + `@module-rebuild`
10. **elog** — post-merge messages from the run
11. **verify** — broken-linkage and preserved-rebuild checks

Preflight, news, sync, plan, and snapshot are fatal — they stop the run if they
fail. `glsa` and `elog` are informational. An optional **depclean** phase runs
before `elog` when enabled.

## Snapshots & rollback

Uses **snapper** if a `root` config exists, otherwise a raw `btrfs subvolume
snapshot` into `/.snapshots/` if `/` is btrfs. If neither, snapshots are skipped.

`gup rollback` lists the snapshots gup made and restores the one you pick. Under
snapper that's a real `snapper rollback` (takes effect on reboot). For raw btrfs
it won't swap subvolumes for you (that can leave you unbootable) — it prints the
manual steps instead.

## Cherry-picking with `--select`

`--select` (interactive only) turns the plan step into a checklist. Everything
starts checked; uncheck what to skip this run.

```
Select packages to update  (2/4 selected)
↑/↓ move · space toggle · a all · n none · enter confirm · q cancel

 > [x] app-editors/vim      9.0.1 → 9.1.0
   [ ] sys-libs/glibc       2.39  → 2.40
   [x] dev-lang/python      3.13  → 3.14
   [ ] www-client/firefox   128   → 130
```

Unchecked packages become `emerge --exclude` atoms. gup re-runs the pretend with
those exclusions and shows the resulting plan before applying, so a conflict
(something you kept needs something you skipped) shows up at the plan step. The
same exclusions go to the actual `emerge`.

Needs a real terminal for the arrow-key list; over a pipe it falls back to a
numbered prompt. Enable per-run with `--select` or `select = true` in the config.

## depclean

`gup depclean` (or the `--depclean` step) removes packages nothing in `@world`
needs. It pretends first, shows the count, and removes nothing until you confirm.
`emerge` still refuses anything that would break a reverse dependency. Read the
list — depclean removes things you use but never added to `@world`.

## Configuration

Set defaults in a config file instead of repeating flags. Read in order, each
overriding the last; command-line flags win:

1. `/etc/gentoo-updater.toml`
2. `~/.config/gentoo-updater/config.toml`
3. command-line flags

Keys match the flag names. Starter file: [`contrib/gentoo-updater.toml.example`](contrib/gentoo-updater.toml.example).

```toml
yes           = false
no_snapshot   = false
select        = false
depclean      = false
low_space_gib = 5.0
notify        = "reboot"   # never | failure | reboot | always
notify_email  = ""
audit         = true
```

Needs Python 3.11+ (`tomllib`); older interpreters ignore the file and use
defaults.

## Audit log & notifications

Each `update`/`depclean` run appends a JSON-Lines record to
`/var/log/gentoo-updater/history.jsonl` (or `~/.local/state/…` if that isn't
writable): timestamp, per-phase results, snapshot id, reboot advice, package
count. Disable with `--no-audit`.

Notifications fire on completion when configured (`--notify`): `failure` on a
failed run, `reboot` also when a reboot is advised, `always` every time. Sent via
`notify-send` and/or email (`sendmail`/`mail` to `notify_email`).

Every run also writes a **debug log** (each command, its exit code and timing,
per-phase results) to `/var/log/gentoo-updater/debug.log`, or `~/.local/state/…`
when not root. It's overwritten each run and its path is printed at the end — the
first place to look if a run hangs or misbehaves.

## Unattended updates (systemd / OpenRC / runit)

Only the scheduling differs per init. `gup install-schedule` detects your init
and installs the right thing (as root; `--dry-run` prints it). Override with
`--init`, set cadence with `--schedule`.

| Init | Installs | Activate |
|---|---|---|
| systemd | `.service` + `.timer` in `/etc/systemd/system` | `systemctl enable --now gentoo-updater.timer` |
| OpenRC | `/etc/cron.daily/gentoo-updater` | run a cron daemon (e.g. cronie) |
| runit | `/etc/sv/gentoo-updater/run` (run-then-sleep loop) | `ln -s /etc/sv/gentoo-updater /var/service/` |

```bash
gup install-schedule                 # auto-detect
gup install-schedule --init runit    # or force one
gup install-timer                    # = --init systemd
```

Each just runs `gup -y --no-sudo`. Reference copies in [`contrib/`](contrib/).
OpenRC and runit have no timer of their own, which is why they use cron / a
supervised loop. Pair it with `notify = "failure"` so unattended failures reach
you.

## Safety notes

- Never auto-merges config files, even unattended — it reports pending ones.
- Never edits `/etc/portage`; it shows the lines, you apply them.
- A single-instance lock prevents two runs colliding.
- `--dry-run` runs no mutating command (snapshot creation included) and skips the
  audit write and notifications. Read-only checks still run, so it reflects real
  state. (It still writes the debug log — that's a diagnostic, not a system change.)
- depclean is opt-in and always asks before removing.

## Status

v0.2 — single-machine Gentoo. Live dashboard, package picker, config file, audit
log, notifications, multi-init scheduling, opt-in depclean. Multi-distro/fleet is
out of scope for now.

## License

MIT.

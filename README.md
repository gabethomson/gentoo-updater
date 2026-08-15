# gentoo-updater (`gup`)

A safer, structured wrapper around Gentoo world updates. It doesn't replace
`emerge` — it orchestrates it, adding the safety rails and verification that a
bare update sequence lacks: pre-update snapshots, risk flagging, news gating,
and post-update health checks.

It's a terminal tool. It shells out to the same `emerge`/`emaint` commands you'd
run by hand, so there's no magic and nothing hidden — you can see every command
it runs.

## Why

The normal update ritual is a fixed sequence of commands with decision points:

```bash
emaint sync -a
eselect news read
emerge -avuDN --with-bdeps=y @world
dispatch-conf
emerge @preserved-rebuild
emerge @module-rebuild
```

`gup` runs that sequence, but also:

- **Snapshots first** — takes a btrfs/snapper snapshot before applying, so a bad
  update is one rollback away.
- **Flags risk** — parses the pending update and highlights high-risk packages
  (gcc, glibc, systemd, kernel, llvm, rust, python) before you commit.
- **Gates on news** — warns (and in interactive mode, pauses) when there are
  unread news items, which often carry "do X before updating" instructions.
- **Verifies after** — checks for broken library linkage and pending preserved
  rebuilds, so you know the system is consistent when it's done.
- **Reports** — a clean end-of-run summary of every phase.

## Install

```bash
# clone and install
git clone https://github.com/kenny/gentoo-updater
cd gentoo-updater
pip install --user .

# optional: nicer output (tables, colour)
pip install --user 'rich>=13'
```

The tool works without `rich`, falling back to plain text.

## Usage

```bash
gup                      # full update, interactive (prompts at decisions)
gup -y                   # unattended: assume yes, apply automatically
gup --dry-run            # show what would run, change nothing
gup plan                 # dry-run: just show the pending update, no sync/apply
gup verify               # only run post-update health checks
gup news                 # show/read pending news
```

### Flags

| Flag | Effect |
|---|---|
| `-y`, `--yes` | Assume yes to all prompts; apply automatically (unattended) |
| `--non-interactive` | Never prompt; report where prompts would be (does not auto-apply unless `-y`) |
| `--dry-run` | Never run mutating commands; print what would run |
| `--no-snapshot` | Skip the pre-update snapshot |
| `--no-sync` | Skip repository sync (use current tree) |
| `--no-sudo` | Don't prepend `sudo` (use when already root) |

### Interactive vs unattended

- **Interactive** (default): prompts before applying, before snapshotting, and
  stops to let you read news. Good for a normal desktop update.
- **Unattended** (`-y`): runs the whole pipeline start to finish with no input.
  Good for a cron/timer job — though note it will apply updates automatically,
  so pair it with `--no-snapshot` only if you trust the setup.

## Phases

1. **preflight** — verify `emerge`/`emaint` exist, note optional tools
2. **news** — check for unread news, offer to read it
3. **sync** — `emaint sync -a` (all repos + overlays)
4. **plan** — `emerge -pvuDN @world`, parse and categorize, flag risk
5. **snapshot** — btrfs/snapper pre-update restore point
6. **apply** — the real `emerge` world update
7. **config** — handle pending `._cfg` files (dispatch-conf)
8. **post-update** — `@preserved-rebuild` + `@module-rebuild`
9. **verify** — broken-linkage and preserved-rebuild checks

Fatal phases (preflight, news, sync, plan, snapshot) stop the run if they fail,
so you never apply against a broken plan or without a snapshot you asked for.

## Snapshots

Prefers **snapper** if a `root` config exists (integrates with your existing
snapper tooling). Falls back to raw `btrfs subvolume snapshot` into
`/.snapshots/` if snapper isn't configured but `/` is btrfs. If neither applies,
snapshots are skipped and the tool tells you.

## Safety notes

- The tool never auto-merges config files in unattended mode — `dispatch-conf`
  is too consequential to automate. It reports pending configs instead.
- `--dry-run` is genuinely side-effect-free: no mutating command runs.
- Every command it runs is printed, prefixed with `$`.

## Status

v0.1 — single-machine Gentoo. Fleet/multi-distro support is a possible future
direction but explicitly out of scope for now.

## License

MIT

# gentoo-updater (`gup`)

**Updating Gentoo is a ritual. `gup` is the checklist that makes sure you don't skip a step.**

It's not a replacement for `emerge` — it's the careful sysadmin sitting next to you, running the exact same commands you'd type by hand, but never forgetting the snapshot, never missing the news item that says *"read this before you update"*, and always checking that nothing broke on the way out.

No magic. No hidden state. Every command it runs is printed to your terminal, prefixed with `$`, so you can watch it work and learn the sequence yourself.

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

---

## The problem

A "real" Gentoo update isn't one command — it's a sequence of them, with judgement calls in between:

```bash
emaint sync -a                          # pull the tree
eselect news read                       # ...and actually read it
emerge -avuDN --with-bdeps=y @world     # the main event
dispatch-conf                           # merge the config churn
emerge @preserved-rebuild               # fix the libs you just replaced
emerge @module-rebuild                  # rebuild out-of-tree modules
```

Miss a step and you find out later — a broken linkage here, an unread warning there, a kernel you can't boot into and no snapshot to fall back to. `gup` runs the whole dance for you and adds the guardrails a bare sequence doesn't have.

## What it actually does

- **Snapshots before it touches anything** — btrfs/snapper restore point up front, so a bad update is one `gup rollback` away.
- **Tells you what's risky** — parses the pending update and flags the scary stuff (gcc, glibc, systemd, the kernel, llvm, rust, python) *before* you commit.
- **Lets you cherry-pick** — `--select` turns the plan into a checklist; uncheck anything you want to sit out this round.
- **Watches your back on security** — runs `glsa-check` and names any package with a known advisory against it.
- **Won't let you skip the news** — unread news items often say "do X first". It surfaces them, pauses so you can read them, and — the good part — at the plan step it calls out any unread item that's *specifically about a package you're updating* (via its `Display-If-Installed` header).
- **Decodes the wall of red** — when the resolver wants keyword/USE changes, it hands you the exact `/etc/portage` lines instead of a cryptic failure.
- **Checks its own work** — broken-linkage and preserved-rebuild scans afterward, plus the `elog` messages that usually scroll into oblivion during a big build.
- **Tells you when to reboot** — flags updates that touched the kernel, glibc, systemd, or dbus.
- **Keeps a diary** — an optional JSONL audit log of every run, and desktop/email notifications when you want them.
- **Runs itself** — `gup install-schedule` sets up hands-off updates on systemd, OpenRC, *or* runit.
- **Tidies up** — an opt-in `depclean` step that prunes orphans without doing anything reckless.

## Install

```bash
git clone https://github.com/kenny/gentoo-updater
cd gentoo-updater
pip install --user .

# optional, for colour + tables + the live dashboard:
pip install --user 'rich>=13'
```

Works fine without `rich` — it just falls back to plain text. Nothing else is required; it leans entirely on the Portage tooling you already have.

## Quick start

```bash
gup                  # the full ride, interactive — prompts at the decision points
gup -y                # unattended: assume yes, apply automatically
gup --dry-run         # narrate every step, change nothing
gup --select          # pick exactly which packages to update (arrow-key checklist)
```

And the focused sub-commands, for when you don't want the whole pipeline:

```bash
gup plan              # just show the pending update (no sync, no apply)
gup verify            # only the post-update health checks
gup news              # show / read pending news
gup rollback          # restore a pre-update snapshot
gup depclean          # prune orphaned packages (asks first)
gup install-schedule  # set up unattended runs (auto-detects your init)
gup install-timer     # shortcut for 'install-schedule --init systemd'
```

### Flags

| Flag | What it does |
|---|---|
| `-y`, `--yes` | Assume yes to every prompt and apply automatically (unattended) |
| `--non-interactive` | Never prompt; report where a prompt *would* be (won't auto-apply unless `-y`) |
| `--dry-run` | Never run a mutating command; just print what would run |
| `--plain` | Turn off the live dashboard; plain linear output |
| `--select` | Interactively pick which pending packages to update |
| `--no-snapshot` | Skip the pre-update snapshot |
| `--no-sync` | Skip the repo sync (use the tree as-is) |
| `--no-sudo` | Don't prepend `sudo` (you're already root) |
| `--depclean` | Also run a depclean step (pretends first, asks before removing) |
| `--notify WHEN` | Completion notification: `never` · `failure` · `reboot` · `always` |
| `--no-audit` | Don't append a record to the audit log |
| `--config PATH` | Use only this config file (skip the default locations) |
| `--init WHICH` | Target init for `install-schedule`: `auto` · `systemd` · `openrc` · `runit` · `cron` |
| `--schedule PERIOD` | Cadence for `install-schedule`: `daily`/`weekly`/`monthly` (systemd also takes any `OnCalendar=`) |

**Interactive vs unattended.** Interactive (the default) stops to let you read news, confirm the snapshot, and approve the apply — the way you'd want to run a desktop update. `-y` runs start to finish with zero input, which is what the scheduler uses. It *will* apply automatically, so only pair it with `--no-snapshot` if you trust the setup.

## While it runs

With `rich` on a real terminal, `gup` draws a live dashboard: a phase checklist pinned to the bottom of the screen, a spinner on whatever's running, and a running clock. Finished phases collapse to a green `OK` (or a red one if something went sideways).

When it's time for the heavy lifting — the sync, the actual `emerge`, `dispatch-conf` — the panel politely **steps aside** and lets Portage stream its own output, in its own colours, exactly as if you'd run it yourself. Then the panel snaps back and ticks the phase off. No captured logs, no lost progress bars.

Piping to a file, running under cron, or no `rich`? It quietly drops to plain linear output. Force that anywhere with `--plain`.

## The phases

1. **preflight** — confirm `emerge`/`emaint` exist, note the optional tools, warn if the build dir is low on space
2. **news** — check for unread news and offer to read it
3. **sync** — `emaint sync -a` across every repo and overlay
4. **plan** — `emerge -pvuDN @world`, parsed and categorised, risk flagged, and cross-referenced against unread news (flags items whose `Display-If-Installed` package is in this update); on a masked resolution it hands you the `/etc/portage` fix
5. **glsa** — `glsa-check` for known security advisories (informational)
6. **snapshot** — the btrfs/snapper restore point
7. **apply** — the real `@world` update
8. **config** — pending `._cfg` files via `dispatch-conf`
9. **post-update** — `@preserved-rebuild` + `@module-rebuild`
10. **elog** — the post-merge messages Portage wrote during the run
11. **verify** — broken-linkage and preserved-rebuild checks

The **fatal** phases — preflight, news, sync, plan, snapshot — halt the run if they fail, so you never merge against a broken plan or skip a snapshot you asked for. `glsa` and `elog` are informational and never stop anything. An optional **depclean** phase slots in before `elog` when you ask for it.

## Snapshots & rollback

`gup` prefers **snapper** when a `root` config exists — it plays nice with the tooling you already have. No snapper but `/` is btrfs? It falls back to a plain read-only `btrfs subvolume snapshot` into `/.snapshots/`. Neither? It says so and moves on.

`gup rollback` lists the restore points *it* created and rolls back to the one you pick. Under snapper that's a real `snapper rollback` (effective next boot). For raw btrfs it deliberately **won't** swap subvolumes for you — that's a great way to end up unbootable — and instead prints the manual steps so you stay in control.

## Cherry-picking with `--select`

By default `gup` updates all of `@world`. Sometimes you don't want that — maybe you're not ready for the new kernel today. `--select` (interactive runs only) turns the plan step into a checklist: everything starts checked, you uncheck what to hold back.

```
Select packages to update  (2/4 selected)
↑/↓ move · space toggle · a all · n none · enter confirm · q cancel

 > [x] app-editors/vim      9.0.1 → 9.1.0
   [ ] sys-libs/glibc       2.39  → 2.40
   [x] dev-lang/python      3.13  → 3.14
   [ ] www-client/firefox   128   → 130
```

Whatever you uncheck becomes an `emerge --exclude` atom. Here's the important part: `gup` then **re-runs the pretend with those exclusions** and shows you the *real* resulting plan before applying. So if something you kept still needs the thing you skipped, you find out right there — with a clear message — instead of halfway through a compile. The same exclusions ride along to the actual merge.

Needs a real terminal for the arrow-key list; over a pipe it falls back to a numbered "type the ones to drop" prompt. Turn it on per-run with `--select`, or make it the default with `select = true` in your config.

## Cleaning orphans (`depclean`)

`gup depclean` (or the `--depclean` step) removes packages nothing in `@world` needs anymore. This one can actually bite — so `gup` treats it with respect: it runs `emerge --depclean --pretend` first, tells you how many packages are on the block, and **removes nothing until you say so** (`emerge` itself still refuses anything that'd break a reverse dep). Read the list. Depclean will happily remove that tool you use daily but never added to `@world`.

## Configuration

Tired of typing the same flags? Pin them in a config file — especially handy for a scheduler that runs `gup` with no arguments. It reads these in order, each overriding the last, and command-line flags beat all of them:

1. `/etc/gentoo-updater.toml` — system-wide
2. `~/.config/gentoo-updater/config.toml` — per-user
3. flags on the command line — always win

Keys mirror the flag names. There's a starter at [`contrib/gentoo-updater.toml.example`](contrib/gentoo-updater.toml.example):

```toml
yes           = false     # unattended: assume yes, apply automatically
no_snapshot   = false
select        = false     # always cherry-pick interactively
depclean      = false     # include the depclean step (still asks first)
low_space_gib = 5.0       # warn below this much free build space
notify        = "reboot"  # never | failure | reboot | always
notify_email  = ""        # e.g. "root@localhost"; empty = no email
audit         = true      # append a JSONL record per run
```

Config parsing wants Python 3.11+ (`tomllib`); on anything older the file is simply ignored and the built-in defaults stand.

## Audit log & notifications

Every `update` and `depclean` run drops one JSON-Lines record into an **audit log** — `/var/log/gentoo-updater/history.jsonl`, or `~/.local/state/…` if that's not writable. Timestamp, every phase's result, the snapshot id, reboot advice, package count. Grep it later to answer *"what did this box do last Tuesday?"* Kill it with `--no-audit` or `audit = false`.

**Notifications** fire when a run finishes, if you've asked them to:

- `failure` — only when something broke
- `reboot` — that, plus whenever a reboot is advised
- `always` — every single run

Delivery goes out over `notify-send` (desktop) and/or email via `sendmail`/`mail` to `notify_email` — whichever you've got.

## Unattended updates (systemd · OpenRC · runit)

The tool itself doesn't care what init you run — only the *scheduling* differs. `gup install-schedule` sniffs your init and sets up the right thing (as root; `--dry-run` just prints it). Force it with `--init`, set cadence with `--schedule`.

| Init | What gets installed | How to activate |
|---|---|---|
| **systemd** | `.service` + `.timer` in `/etc/systemd/system` | `systemctl enable --now gentoo-updater.timer` |
| **OpenRC** | `/etc/cron.daily/gentoo-updater` | make sure a cron daemon (e.g. cronie) is running |
| **runit** | `/etc/sv/gentoo-updater/run` (a supervised run-then-sleep loop) | `ln -s /etc/sv/gentoo-updater /var/service/` |

```bash
gup install-schedule                 # auto-detect this box
gup install-schedule --init runit    # or pin one
gup install-timer                    # = install-schedule --init systemd
```

Under the hood every one of them just runs `gup -y --no-sudo`. Reference copies for all three live in [`contrib/`](contrib/). Do yourself a favour and pair it with `notify = "failure"` so an unattended breakage actually reaches you.

> Why the split? OpenRC and runit don't *have* a periodic scheduler — they supervise daemons. So `gup` uses cron for OpenRC (the Gentoo-native path) and a supervised sleep-loop for runit. That's why those two need a cron daemon / a runsvdir symlink, and systemd doesn't.

## The fine print (a.k.a. safety)

- **It never auto-merges your config files.** `dispatch-conf` is too consequential to automate — even unattended, `gup` just reports what's pending.
- **It never edits `/etc/portage` for you.** When the resolver needs keyword/USE changes, it *shows* you the lines. You decide.
- **One at a time.** A single-instance lock stops two `update`/`rollback`/`depclean` runs — or a run racing itself — from colliding.
- **`--dry-run` means it.** No mutating command runs — snapshot *creation* included — and it skips the audit write and notifications too. Read-only introspection (`emerge --pretend`, snapshot *listing*) still runs, so the dry run reflects reality.
- **depclean is always opt-in**, and always asks before it removes anything.
- **Everything is visible.** Every command it runs is echoed, prefixed with `$`.

## Status

**v0.2** — single-machine Gentoo, now with a live dashboard, an interactive package picker, a config file, an audit log, notifications, multi-init scheduling, and opt-in depclean. Fleet / multi-distro support is a maybe-someday; explicitly not the goal right now.

## License

MIT — do what you like, no warranty. It runs `emerge` as root; read the code, keep your snapshots.

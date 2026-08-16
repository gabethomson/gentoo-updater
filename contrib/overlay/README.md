# gentoo-updater overlay

Ebuilds so you can `emerge` gentoo-updater instead of using pip (which Gentoo
blocks by PEP 668). There are two:

- **`-9999`** — a live ebuild that builds straight from `main` on GitHub. No
  distfile, no Manifest, no version bumps: `git pull` on your side, re-emerge,
  and you're on the latest. Recommended while things move fast.
- **`-0.2.2`** — a pinned release from the `v0.2.2` tag tarball. Reproducible,
  but needs a Manifest (see below).

First point portage at the overlay (run from inside your clone — `$PWD` fills in
the real path; note the unquoted `<<EOF`):

```bash
cd /wherever/you/cloned/gentoo-updater

sudo tee /etc/portage/repos.conf/gentoo-updater.conf >/dev/null <<EOF
[gentoo-updater]
location = $PWD/contrib/overlay
masters = gentoo
auto-sync = false
EOF
```

## Live ebuild (recommended)

```bash
# live ebuilds have no KEYWORDS, so accept ** for this package:
echo "app-admin/gentoo-updater **" | sudo tee /etc/portage/package.accept_keywords/gentoo-updater

sudo emerge -av =app-admin/gentoo-updater-9999
# with the animated dashboard (rich):
sudo USE="rich" emerge -av =app-admin/gentoo-updater-9999
```

To update later: `git pull` in your clone, then re-emerge (git-r3 refetches
`main` and rebuilds).

## Pinned release

The tarball ebuild needs a Manifest with the distfile's checksum. Generate it
once per release (it fetches the tag tarball from GitHub):

```bash
echo "app-admin/gentoo-updater ~amd64" | sudo tee /etc/portage/package.accept_keywords/gentoo-updater
sudo ebuild "$PWD/contrib/overlay/app-admin/gentoo-updater/gentoo-updater-0.2.2.ebuild" manifest
sudo emerge -av =app-admin/gentoo-updater-0.2.2
```

If you see `VERIFY FAILED … Insufficient data for checksum verification`, it
means that `ebuild … manifest` step hasn't been run for this version yet.

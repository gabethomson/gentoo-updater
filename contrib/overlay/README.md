# gentoo-updater overlay

Ebuilds so you can `emerge` gentoo-updater instead of using pip (which Gentoo
blocks by PEP 668).

Point portage at the overlay first. Run this from inside your clone — `$PWD`
fills in the real path, and the heredoc is unquoted so it expands:

```bash
cd /wherever/you/cloned/gentoo-updater

sudo tee /etc/portage/repos.conf/gentoo-updater.conf >/dev/null <<EOF
[gentoo-updater]
location = $PWD/contrib/overlay
masters = gentoo
auto-sync = false
EOF
```

## Install

```bash
# accept the testing keyword, then merge
echo "app-admin/gentoo-updater ~amd64" | sudo tee /etc/portage/package.accept_keywords/gentoo-updater
sudo emerge -av app-admin/gentoo-updater

# with colour, tables, and the animated status line:
sudo USE="rich" emerge -av app-admin/gentoo-updater
```

That's it — no Manifest step. The versioned ebuild fetches the `v${PV}` git tag
rather than a release tarball, so there's no distfile to checksum. (The old
tarball approach is what produced `VERIFY FAILED … Insufficient data for
checksum verification` when a Manifest hadn't been regenerated.)

## Updating

```bash
cd /wherever/you/cloned/gentoo-updater
git pull                       # picks up the new ebuild
sudo emerge -av app-admin/gentoo-updater
```

## Releasing a new version (maintainer)

1. Bump `version` in `pyproject.toml` and `__version__` in
   `gentoo_updater/__init__.py`.
2. Copy the ebuild to the new version:
   `git mv .../gentoo-updater-0.2.2.ebuild .../gentoo-updater-0.2.3.ebuild`
3. Commit, then tag and push: `git tag -a v0.2.3 -m v0.2.3 && git push --tags`

The ebuild pins `EGIT_COMMIT="v${PV}"`, so the tag must exist before anyone can
emerge that version. Nothing else to regenerate.

## Live ebuild

There's also a `-9999` ebuild that builds whatever is on `main`, for tracking
development between releases. It has no `KEYWORDS`, so it needs `**`:

```bash
echo "app-admin/gentoo-updater **" | sudo tee /etc/portage/package.accept_keywords/gentoo-updater
sudo emerge -av =app-admin/gentoo-updater-9999
```

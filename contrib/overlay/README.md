# gentoo-updater overlay

A minimal ebuild so you can `emerge` gentoo-updater instead of using pip (which
Gentoo blocks by PEP 668).

## Use it as a local overlay

Run these **from inside your clone** (the dir that contains `contrib/`). `$PWD`
fills in the real path for you — don't paste a literal `/path/to/...`. Note the
unquoted `<<EOF` so `$PWD` expands.

```bash
cd /wherever/you/cloned/gentoo-updater

# tell portage where the overlay lives
sudo tee /etc/portage/repos.conf/gentoo-updater.conf >/dev/null <<EOF
[gentoo-updater]
location = $PWD/contrib/overlay
masters = gentoo
auto-sync = false
EOF

# unmask the ~testing keyword, generate the Manifest, then merge
echo "app-admin/gentoo-updater ~amd64" | sudo tee /etc/portage/package.accept_keywords/gentoo-updater
sudo ebuild "$PWD/contrib/overlay/app-admin/gentoo-updater/gentoo-updater-0.2.1.ebuild" manifest
sudo emerge -av app-admin/gentoo-updater

# with the live dashboard:
sudo USE="rich" emerge -av app-admin/gentoo-updater
```

The `ebuild … manifest` step is required (thin manifests): it fetches the tag
tarball from GitHub and records its hash before portage will merge.

The `SRC_URI` pulls the `v${PV}` tag tarball from the public GitHub mirror, so
bump the ebuild filename to match each release tag.

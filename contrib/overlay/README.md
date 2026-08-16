# gentoo-updater overlay

A minimal ebuild so you can `emerge` gentoo-updater instead of using pip (which
Gentoo blocks by PEP 668).

## Use it as a local overlay

```bash
# tell portage where the overlay lives
sudo tee /etc/portage/repos.conf/gentoo-updater.conf >/dev/null <<'EOF'
[gentoo-updater]
location = /path/to/gentoo-updater/contrib/overlay
masters = gentoo
auto-sync = false
EOF

# unmask the ~testing keyword, then merge
echo "app-admin/gentoo-updater ~amd64" | sudo tee -a /etc/portage/package.accept_keywords/gentoo-updater
sudo ebuild /path/to/gentoo-updater/contrib/overlay/app-admin/gentoo-updater/gentoo-updater-0.2.0.ebuild manifest
sudo emerge -av app-admin/gentoo-updater

# with the live dashboard:
sudo USE="rich" emerge -av app-admin/gentoo-updater
```

The `SRC_URI` pulls the `v${PV}` tag tarball from the public GitHub mirror, so
bump the ebuild filename to match each release tag.

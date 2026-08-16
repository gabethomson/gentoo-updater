# Copyright 2026 Gentoo Authors
# Distributed under the terms of the MIT license

EAPI=8

DISTUTILS_USE_PEP517=setuptools
PYTHON_COMPAT=( python3_{11..14} )

inherit distutils-r1 git-r3

DESCRIPTION="Wraps emerge world updates with snapshots, checks, and rollback"
HOMEPAGE="https://github.com/gabethomson/gentoo-updater"
EGIT_REPO_URI="https://github.com/gabethomson/gentoo-updater.git"

LICENSE="MIT"
SLOT="0"
# Live ebuild: no KEYWORDS, so it needs ACCEPT_KEYWORDS="**". git-r3 fetches
# straight from the repo, so there's no distfile and no Manifest to maintain.
KEYWORDS=""
IUSE="rich"

RDEPEND="rich? ( dev-python/rich[${PYTHON_USEDEP}] )"

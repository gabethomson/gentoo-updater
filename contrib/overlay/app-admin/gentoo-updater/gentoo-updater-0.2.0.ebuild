# Copyright 2026 Gentoo Authors
# Distributed under the terms of the MIT license

EAPI=8

DISTUTILS_USE_PEP517=setuptools
PYTHON_COMPAT=( python3_{11..14} )

inherit distutils-r1

DESCRIPTION="Wraps emerge world updates with snapshots, checks, and rollback"
HOMEPAGE="https://github.com/gabethomson/gentoo-updater"
SRC_URI="https://github.com/gabethomson/${PN}/archive/refs/tags/v${PV}.tar.gz -> ${P}.tar.gz"

LICENSE="MIT"
SLOT="0"
KEYWORDS="~amd64"
IUSE="rich"

# Everything the tool needs at runtime is stdlib; rich is optional and only
# enables colour, tables, and the live dashboard.
RDEPEND="rich? ( dev-python/rich[${PYTHON_USEDEP}] )"

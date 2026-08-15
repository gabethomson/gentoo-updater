"""Make the repo root importable so `import gentoo_updater` works under pytest."""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

"""`dasik.__version__` must not be a second copy of the version.

It was hardcoded to "0.2.0" and stayed there through 0.3.0, 0.4.0, 0.5.0 and
0.6.0 — exactly the drift `_version` in __main__ already warns about, in
the one place nobody reads often enough to notice.

The fallback matters as much as the lookup: dasik runs from a bare source tree
in the VM harness (`cd /root/repo && python -m dasik`), where no distribution is
installed, and an unguarded metadata lookup would make `import dasik` itself
raise there.
"""
from unittest.mock import patch

import dasik
from dasik.__main__ import _version


def test_the_package_version_matches_the_distribution():
    assert dasik.__version__ == _version()


def test_it_is_not_the_stale_hardcoded_one():
    assert dasik.__version__ != "0.2.0"


def test_an_uninstalled_source_tree_still_imports():
    """The VM case: no distribution metadata to find."""
    with patch("importlib.metadata.version",
               side_effect=ModuleNotFoundError("no dist")):
        assert dasik._resolve_version() == "0.0.0+unknown"

"""srcinfo.py — pure .SRCINFO parsing helpers (no I/O, no subprocess)."""
from dasik.lib.actions import srcinfo


SRCINFO = """
pkgbase = config-saver
\tpkgdesc = saver
\tpkgver = 1.0
pkgname = config-saver
"""

SRCINFO_MULTI = """
pkgbase = foo
pkgname = foo
pkgname = foo-docs
"""


# --- parse_pkgnames (moved from PkgbuildGitInstaller) --------------------- #

def test_parse_pkgnames_single():
    assert srcinfo.parse_pkgnames(SRCINFO) == {"config-saver"}


def test_parse_pkgnames_split_package():
    assert srcinfo.parse_pkgnames(SRCINFO_MULTI) == {"foo", "foo-docs"}


def test_parse_pkgnames_ignores_pkgbase_and_deps():
    text = "pkgbase = x\npkgname = x\ndepends = git\nmakedepends = go\n"
    assert srcinfo.parse_pkgnames(text) == {"x"}


# --- parse_depends -------------------------------------------------------- #

def test_parse_depends_includes_make_and_check():
    text = (
        "pkgname = asunder\n"
        "\tdepends = gtk2\n"
        "\tmakedepends = intltool\n"
        "\tcheckdepends = python-pytest\n"
    )
    assert srcinfo.parse_depends(text) == {"gtk2", "intltool", "python-pytest"}


def test_parse_depends_arch_suffixed_and_ignores_optdepends():
    text = (
        "pkgname = foo\n"
        "\tdepends_x86_64 = libx\n"
        "\tmakedepends_aarch64 = cross-gcc\n"
        "\toptdepends = bar: optional thing\n"
    )
    deps = srcinfo.parse_depends(text)
    assert "libx" in deps and "cross-gcc" in deps
    assert "bar" not in deps          # optdepends excluded


def test_parse_depends_strips_optdepends_description():
    # even if some odd .SRCINFO listed a dep with a colon, optdepends is excluded
    text = "pkgname = z\n\tdepends = a\n\toptdepends = b: because\n"
    assert srcinfo.parse_depends(text) == {"a"}


def test_parse_depends_empty_when_none():
    assert srcinfo.parse_depends("pkgname = z\n\tpkgver = 1\n") == set()


# --- strip_version_constraint -------------------------------------------- #

def test_strip_version_constraint_variants():
    assert srcinfo.strip_version_constraint("gtk2>=2.24") == "gtk2"
    assert srcinfo.strip_version_constraint("foo<=1") == "foo"
    assert srcinfo.strip_version_constraint("foo=3") == "foo"
    assert srcinfo.strip_version_constraint("foo>1") == "foo"
    assert srcinfo.strip_version_constraint("foo<2") == "foo"
    assert srcinfo.strip_version_constraint("foo") == "foo"


def test_strip_version_constraint_trims_whitespace():
    assert srcinfo.strip_version_constraint("  gtk2 >= 2.24 ") == "gtk2"

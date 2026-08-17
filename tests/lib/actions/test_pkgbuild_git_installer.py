"""PkgbuildGitInstaller — build+install a PKGBUILD pinned to a Git commit (PLAN v3 §8).

Never runs makepkg for real: subprocess/Command are mocked. Tests cover the
DECISION logic — SHA pinning, pkgname identity, safe argv, cleanup on success and
failure — not the destructive shell-out itself.
"""
from unittest.mock import MagicMock, patch, call

import pytest

from dasik.lib.actions.pkgbuild_git_installer import (
    PkgbuildGitInstaller,
    _su_argv,
)
from dasik.lib.actions.package_resolver import ResolvedGitPackage
from dasik.lib.exceptions.exceptions import CommandExecutionError
from dasik.lib.target.target import Target


_SHA = "a520605367e13ec25db4c3c7e1c4bf46175ba8cd"


def _pkg(name="config-saver", ref=_SHA, subdir="."):
    return ResolvedGitPackage(name=name, source={
        "type": "pkgbuild-git",
        "url": "https://github.com/amt911/config-saver-aur.git",
        "ref": ref,
        "subdir": subdir,
    })


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


# --- pure: parse pkgnames -------------------------------------------------

def test_parse_pkgnames_single():
    assert PkgbuildGitInstaller._parse_pkgnames(SRCINFO) == {"config-saver"}


def test_parse_pkgnames_split_package():
    assert PkgbuildGitInstaller._parse_pkgnames(SRCINFO_MULTI) == {"foo", "foo-docs"}


def test_parse_pkgnames_ignores_pkgbase_and_deps():
    text = "pkgbase = x\npkgname = x\ndepends = git\nmakedepends = go\n"
    assert PkgbuildGitInstaller._parse_pkgnames(text) == {"x"}


# --- orchestration (fully mocked) ----------------------------------------

class _Harness:
    """Drives install() with ALL shell-outs mocked through a single patch target
    (``Command.execute``; the installer no longer uses raw subprocess). ``head_sha``
    is what ``git rev-parse HEAD`` returns; ``srcinfo`` what printsrcinfo returns."""

    def __init__(self, head_sha=_SHA, srcinfo=SRCINFO, installed=True, user_exists=False):
        self.head_sha = head_sha
        self.srcinfo = srcinfo
        self.installed = installed
        self.user_exists = user_exists
        self.runs = []

    def command_execute(self, cmd, args=None, *a, **kw):
        args = list(args or [])
        self.runs.append([cmd, *args])
        rc = 0
        out = b""
        if cmd == "id":
            rc = 0 if self.user_exists else 1
        elif cmd == "pacman" and args and args[0] == "-Q":
            rc = 0 if self.installed else 1
        elif cmd == "su":
            script = args[3] if len(args) > 3 else ""
            if "rev-parse" in script:
                out = (self.head_sha + "\n").encode()
            elif "--printsrcinfo" in script:
                out = self.srcinfo.encode()
        return MagicMock(returncode=rc, stdout=out, stderr=b"")


def _install(harness, pkg=None, srcinfo_on_disk=False, build_deps=None):
    inst = PkgbuildGitInstaller(Target(root="/"), build_deps=build_deps)
    with patch("dasik.lib.actions.pkgbuild_git_installer.Command.execute",
               side_effect=harness.command_execute), \
         patch("dasik.lib.actions.pkgbuild_git_installer.os.path.exists",
               return_value=srcinfo_on_disk), \
         patch("dasik.lib.actions.pkgbuild_git_installer.os.remove"), \
         patch("builtins.open", MagicMock()):
        inst.install([pkg or _pkg()])
    return inst


def test_happy_path_builds_and_verifies():
    h = _Harness()
    _install(h)
    joined = [" ".join(r) for r in h.runs]
    assert any("makepkg -sri" in j for j in joined)         # built + installed
    assert any(r[:2] == ["pacman", "-Q"] for r in h.runs)   # verified installed


def test_clone_url_passed_as_positional_arg_not_interpolated():
    h = _Harness()
    _install(h)
    # the clone command runs `git clone "$1" "$2"` with url/dir as sh positional args
    clone = [r for r in h.runs if "clone" in " ".join(r)]
    assert clone, h.runs
    argv = clone[0]
    assert 'git clone "$1" "$2"' in argv
    assert "https://github.com/amt911/config-saver-aur.git" in argv
    # the url appears only as a positional arg, never spliced into the script token
    assert not any("clone https://github.com" in tok for tok in argv)


def test_sha_mismatch_aborts():
    h = _Harness(head_sha="0" * 40)
    with pytest.raises(CommandExecutionError, match="commit"):
        _install(h)


def test_identity_mismatch_aborts_before_install():
    h = _Harness(srcinfo="pkgbase = other\npkgname = other-package\n")
    with pytest.raises(CommandExecutionError, match="refusing"):
        _install(h)
    # never reached the build+install step (printsrcinfo may run for identity)
    assert not any("makepkg -sri" in " ".join(r) for r in h.runs)


def test_post_install_verify_failure_raises():
    h = _Harness(installed=False)   # pacman -Q says not installed after build
    with pytest.raises(CommandExecutionError):
        _install(h)


# --- build dependencies the repositories do not have ----------------------
#
# `makepkg -s` syncs dependencies with pacman, which only knows the configured
# repositories. A makedepends that lives in the AUR therefore aborts the build
# with "target not found", and no ordering of the package list can fix it: the
# dependency has to be installed BEFORE makepkg runs. The installer hands its
# declared build dependencies to a hook; resolving which of them are AUR-only
# belongs to the caller, which owns the resolver.

SRCINFO_AUR_MAKEDEP = """
pkgbase = ttf-atkinson-hyperlegible-next-nerd-git
\tmakedepends = font-patcher
pkgname = ttf-atkinson-hyperlegible-next-nerd-git
"""


def _font_pkg():
    return _pkg(name="ttf-atkinson-hyperlegible-next-nerd-git")


def test_declared_build_deps_reach_the_hook_before_makepkg_runs():
    h = _Harness(srcinfo=SRCINFO_AUR_MAKEDEP)
    seen = []

    def build_deps(deps):
        seen.append(sorted(deps))
        h.runs.append(["<deps-hook>"])

    _install(h, pkg=_font_pkg(), build_deps=build_deps)

    assert seen == [["font-patcher"]]
    joined = [" ".join(r) for r in h.runs]
    assert joined.index("<deps-hook>") < next(
        i for i, j in enumerate(joined) if "makepkg -sri" in j)


def test_a_dep_the_pkgbuild_itself_produces_is_not_offered():
    """A split package depending on its own sibling is not a missing dep."""
    h = _Harness(srcinfo=(
        "pkgbase = foo\n\tdepends = foo-common\n"
        "pkgname = foo\npkgname = foo-common\n"
    ))
    seen = []
    _install(h, pkg=_pkg(name="foo"), build_deps=seen.append)
    assert seen == [[]] or seen == []


def test_version_constraints_are_stripped_from_deps():
    h = _Harness(srcinfo=(
        "pkgbase = x\n\tmakedepends = font-patcher>=3.1.0\n"
        "\tdepends = python-fontforge=20230101\npkgname = x\n"
    ))
    seen = []
    _install(h, pkg=_pkg(name="x"), build_deps=lambda d: seen.append(sorted(d)))
    assert seen == [["font-patcher", "python-fontforge"]]


def test_the_sudoers_fragment_survives_the_deps_hook():
    """Installing an AUR build dependency runs the AUR installer, which shares
    the _aurbuilder user AND its /etc/sudoers.d fragment — and removes the
    fragment when it finishes. That happens in the middle of THIS build, so the
    `sudo pacman -U` behind `makepkg -i` then has no passwordless sudo left:

        sudo: a terminal is required to read the password
        ==> WARNING: Failed to install built package(s).

    measured in a guest, exit 14. The fragment has to be re-asserted after the
    hook and before makepkg — the install()-level cleanup still removes it, so
    nothing is left behind.
    """
    h = _Harness(srcinfo=SRCINFO_AUR_MAKEDEP)

    def build_deps(deps):
        h.runs.append(["<deps-hook>"])

    def record_open(path, *a, **kw):
        h.runs.append([f"<open {path}>"])
        return MagicMock()

    inst = PkgbuildGitInstaller(Target(root="/"), build_deps=build_deps)
    with patch("dasik.lib.actions.pkgbuild_git_installer.Command.execute",
               side_effect=h.command_execute), \
         patch("dasik.lib.actions.pkgbuild_git_installer.os.path.exists",
               return_value=False), \
         patch("dasik.lib.actions.pkgbuild_git_installer.os.remove"), \
         patch("builtins.open", side_effect=record_open):
        inst.install([_font_pkg()])

    joined = [" ".join(r) for r in h.runs]
    hook = joined.index("<deps-hook>")
    build = next(i for i, j in enumerate(joined) if "makepkg -sri" in j)
    sudoers = [i for i, j in enumerate(joined) if "sudoers.d/_aurbuilder" in j]
    assert any(hook < i < build for i in sudoers), (
        f"sudoers written at {sudoers}, hook at {hook}, build at {build}")


def test_without_a_hook_the_build_still_runs():
    """The hook is optional: nothing else changes when no caller supplies one."""
    h = _Harness(srcinfo=SRCINFO_AUR_MAKEDEP)
    _install(h, pkg=_font_pkg())
    assert any("makepkg -sri" in " ".join(r) for r in h.runs)


def test_uses_committed_srcinfo_when_present():
    h = _Harness(srcinfo="SHOULD NOT BE USED")
    # committed .SRCINFO on disk is read instead of printsrcinfo
    inst = PkgbuildGitInstaller(Target(root="/"))
    with patch("dasik.lib.actions.pkgbuild_git_installer.Command.execute",
               side_effect=h.command_execute), \
         patch("dasik.lib.actions.pkgbuild_git_installer.os.path.exists", return_value=True), \
         patch("dasik.lib.actions.pkgbuild_git_installer.os.remove"), \
         patch("builtins.open", MagicMock()), \
         patch("pathlib.Path.read_text", return_value=SRCINFO):
        inst.install([_pkg()])
    # printsrcinfo was NOT invoked (committed file used)
    assert not any("--printsrcinfo" in " ".join(r) for r in h.runs)


def test_cleanup_runs_on_build_failure():
    h = _Harness()

    def boom(cmd, args=None, *a, **kw):
        if cmd == "useradd":
            return MagicMock(returncode=0, stdout=b"", stderr=b"")
        return h.command_execute(cmd, args, *a, **kw)

    inst = PkgbuildGitInstaller(Target(root="/"))
    removed = MagicMock()
    with patch("dasik.lib.actions.pkgbuild_git_installer.Command.execute",
               side_effect=boom), \
         patch("dasik.lib.actions.pkgbuild_git_installer.os.path.exists", return_value=False), \
         patch("dasik.lib.actions.pkgbuild_git_installer.os.remove", removed), \
         patch("builtins.open", MagicMock()), \
         patch.object(PkgbuildGitInstaller, "_build_one", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            inst.install([_pkg()])
    # user was created this run (id returned 1) → cleanup removed it
    assert any(r[:1] == ["userdel"] or (r and r[0] == "userdel") for r in h.runs)


def test_existing_user_not_removed():
    h = _Harness(user_exists=True)
    _install(h)
    assert not any(r and r[0] == "userdel" for r in h.runs)
    assert not any(r and r[0] == "useradd" for r in h.runs)


def test_su_argv_terminates_options_before_dash_prefixed_payload():
    assert _su_argv(
        "_aurbuilder", 'exec "$@"', "yay", "-S", "asunder"
    ) == [
        "su",
        "-",
        "_aurbuilder",
        "-c",
        'exec "$@"',
        "--",
        "sh",
        "yay",
        "-S",
        "asunder",
    ]

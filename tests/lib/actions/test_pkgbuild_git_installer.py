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


def _install(harness, pkg=None, srcinfo_on_disk=False):
    inst = PkgbuildGitInstaller(Target(root="/"))
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

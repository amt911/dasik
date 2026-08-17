"""PackagesAction.apply routes package_sources (git) installs to the installer."""
from unittest.mock import MagicMock, patch

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.packages_action import PackagesAction
from dasik.lib.actions.package_resolver import PackageResolution, ResolvedGitPackage
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target


_SRC = {
    "type": "pkgbuild-git",
    "url": "https://github.com/amt911/config-saver-aur.git",
    "ref": "a520605367e13ec25db4c3c7e1c4bf46175ba8cd",
    "subdir": ".",
}


def _ctx():
    return ActionContext(target=Target(root="/"))


def _ok():
    return MagicMock(returncode=0, stdout=b"", stderr=b"")


def test_apply_routes_git_to_installer():
    a = PackagesAction(
        config={"packages": ["config-saver"], "package_sources": {"config-saver": _SRC}},
        context=_ctx(),
    )
    git = [ResolvedGitPackage("config-saver", _SRC)]
    res = PackageResolution(git=git)
    with patch.object(a, "_resolve_sources", return_value=res), \
         patch("dasik.lib.actions.packages_action.Command.execute", MagicMock(return_value=_ok())), \
         patch("dasik.lib.actions.pkgbuild_git_installer.PkgbuildGitInstaller.install") as install:
        a.apply([Change("packages", Op.INSTALL, "config-saver")])
    install.assert_called_once_with(git)


class _FakeInstaller:
    """Captures what PackagesAction hands the real installer."""
    seen: dict = {}

    def __init__(self, target, build_deps=None):
        _FakeInstaller.seen = {"target": target, "hook": build_deps}

    def install(self, pkgs):
        _FakeInstaller.seen["installed"] = list(pkgs)


def _drive_hook(action, deps, resolution):
    """Run _apply_git_install, then feed *deps* to the hook it registered."""
    with patch("dasik.lib.actions.pkgbuild_git_installer.PkgbuildGitInstaller",
               _FakeInstaller), \
         patch.object(action, "_resolve_sources", return_value=resolution), \
         patch.object(action, "_apply_aur_install") as aur:
        action._apply_git_install([ResolvedGitPackage("x", _SRC)])
        hook = _FakeInstaller.seen["hook"]
        assert hook is not None, "no build-dep hook was registered"
        hook(deps)
    return aur


def test_git_build_deps_only_the_aur_has_are_installed_before_the_build():
    """font-patcher is a makedepends of a package_sources PKGBUILD and lives in
    the AUR: `makepkg -s` cannot sync it, so dasik must install it itself."""
    a = PackagesAction(config={"package_sources": {"x": _SRC}}, context=_ctx())
    res = PackageResolution(repo=["python-fontforge"], aur=["font-patcher"])
    aur = _drive_hook(a, ["font-patcher", "python-fontforge"], res)
    aur.assert_called_once()
    assert aur.call_args[0][0] == ["font-patcher"]


def test_repo_build_deps_are_left_to_makepkg():
    """makepkg -s syncs repo dependencies perfectly well; doing it twice would
    only make the build slower and the log confusing."""
    a = PackagesAction(config={"package_sources": {"x": _SRC}}, context=_ctx())
    res = PackageResolution(repo=["python-fontforge"])
    aur = _drive_hook(a, ["python-fontforge"], res)
    aur.assert_not_called()


def test_apply_no_git_does_not_touch_installer():
    a = PackagesAction(config={"packages": ["git"]}, context=_ctx())
    res = PackageResolution(repo=["git"])
    with patch.object(a, "_resolve_sources", return_value=res), \
         patch("dasik.lib.actions.packages_action.Command.execute", MagicMock(return_value=_ok())), \
         patch("dasik.lib.actions.pkgbuild_git_installer.PkgbuildGitInstaller.install") as install:
        a.apply([Change("packages", Op.INSTALL, "git")])
    install.assert_not_called()

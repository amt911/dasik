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


def test_apply_no_git_does_not_touch_installer():
    a = PackagesAction(config={"packages": ["git"]}, context=_ctx())
    res = PackageResolution(repo=["git"])
    with patch.object(a, "_resolve_sources", return_value=res), \
         patch("dasik.lib.actions.packages_action.Command.execute", MagicMock(return_value=_ok())), \
         patch("dasik.lib.actions.pkgbuild_git_installer.PkgbuildGitInstaller.install") as install:
        a.apply([Change("packages", Op.INSTALL, "git")])
    install.assert_not_called()

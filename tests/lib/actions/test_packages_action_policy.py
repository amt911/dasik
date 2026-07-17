"""Unknown-package policy in PackagesAction.apply (PLAN v3 §7).

warn-and-skip (default): a name confirmed to exist nowhere is skipped with a
visible warning, the rest install, apply exits normally. error: abort as before.
unavailable (AUR unreachable) is ALWAYS a blocking abort, whatever the policy.
"""
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.packages_action import PackagesAction
from dasik.lib.actions.package_resolver import PackageResolution
from dasik.lib.exceptions.exceptions import CommandExecutionError
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target


def _ctx(root="/"):
    return ActionContext(target=Target(root=root))


def _ok():
    return MagicMock(returncode=0, stdout=b"", stderr=b"")


def _install(*names):
    return [Change("packages", Op.INSTALL, n) for n in names]


def _action(desired, policy="warn-and-skip"):
    return PackagesAction(
        config={"packages": list(desired), "package_policy": {"unknown": policy}},
        context=_ctx(),
    )


def test_unavailable_always_aborts_before_mutation():
    a = _action(["someaurpkg"])
    res = PackageResolution(unavailable=["someaurpkg"])
    fake = MagicMock(side_effect=lambda *x, **k: _ok())
    with patch.object(a, "_resolve_sources", return_value=res), \
         patch("dasik.lib.actions.packages_action.Command.execute", fake):
        with pytest.raises(CommandExecutionError):
            a.apply(_install("someaurpkg"))
    # nothing was installed
    assert not any(c.args[0] == "pacman" and "-S" in (c.args[1] or [])
                   for c in fake.call_args_list)


def test_unavailable_aborts_even_in_error_policy():
    a = _action(["x"], policy="error")
    res = PackageResolution(unavailable=["x"])
    with patch.object(a, "_resolve_sources", return_value=res), \
         patch("dasik.lib.actions.packages_action.Command.execute", MagicMock(return_value=_ok())):
        with pytest.raises(CommandExecutionError):
            a.apply(_install("x"))


def test_unknown_with_error_policy_aborts():
    a = _action(["nope"], policy="error")
    res = PackageResolution(unknown=["nope"])
    with patch.object(a, "_resolve_sources", return_value=res), \
         patch("dasik.lib.actions.packages_action.Command.execute", MagicMock(return_value=_ok())):
        with pytest.raises(CommandExecutionError):
            a.apply(_install("nope"))


def test_unknown_with_warn_and_skip_installs_rest_and_warns():
    a = _action(["git", "nope"])
    res = PackageResolution(repo=["git"], unknown=["nope"])
    fake = MagicMock(return_value=_ok())
    warn = MagicMock()
    with patch.object(a, "_resolve_sources", return_value=res), \
         patch("dasik.lib.actions.packages_action.Command.execute", fake), \
         patch("dasik.lib.actions.packages_action.run_logger.get",
               return_value=MagicMock(warning=warn)):
        a.apply(_install("git", "nope"))   # must NOT raise
    # git installed via pacman -S
    pac = [c for c in fake.call_args_list if c.args[0] == "pacman" and c.args[1][:4] == ["--noconfirm", "--needed", "-S", "git"]]
    assert pac, fake.call_args_list
    # skipped recorded + warned once
    assert a._skipped_unknown == ["nope"]
    warn.assert_called_once()


def test_only_unknown_warn_and_skip_runs_no_install_and_exits_ok():
    a = _action(["nope1", "nope2"])
    res = PackageResolution(unknown=["nope2", "nope1"])
    fake = MagicMock(return_value=_ok())
    with patch.object(a, "_resolve_sources", return_value=res), \
         patch("dasik.lib.actions.packages_action.Command.execute", fake), \
         patch("dasik.lib.actions.packages_action.run_logger.get", return_value=MagicMock()):
        a.apply(_install("nope1", "nope2"))
    # no pacman -S transaction ran
    assert not any(c.args[0] == "pacman" and "-S" in c.args[1] for c in fake.call_args_list)
    assert a._skipped_unknown == ["nope1", "nope2"]  # sorted, stable


def test_managed_keys_excludes_skipped_after_apply():
    a = _action(["git", "nope"])
    res = PackageResolution(repo=["git"], unknown=["nope"])
    with patch.object(a, "_resolve_sources", return_value=res), \
         patch("dasik.lib.actions.packages_action.Command.execute", MagicMock(return_value=_ok())), \
         patch("dasik.lib.actions.packages_action.run_logger.get", return_value=MagicMock()):
        a.apply(_install("git", "nope"))
    assert a.managed_keys() == {"packages": ["git"]}


def test_managed_keys_full_before_apply():
    a = _action(["git", "nope"])
    assert a.managed_keys() == {"packages": ["git", "nope"]}

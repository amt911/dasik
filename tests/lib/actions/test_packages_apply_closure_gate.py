"""The transitive AUR closure gate in PackagesAction.apply().

Regression suite for the 2026-08-18 incident: a declared AUR package whose
transitive dependency chain ends in a name nothing satisfies must abort the
apply BEFORE the first mutation (no pacman -S, no build user, no sudoers) —
not 25 minutes in, mid-yay-transaction. Optional roots degrade to a warning.
"""
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.package_resolver import (
    AurUnavailableError,
    PackageResolution,
)
from dasik.lib.actions.packages_action import PackagesAction
from dasik.lib.exceptions.exceptions import CommandExecutionError
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target
from dasik.lib.validation.aur_closure import BrokenDep


_INCIDENT = BrokenDep(
    chain=("lib32-gst-libav", "lib32-ffmpeg", "lib32-libdav1d"),
    spec="lib32-libdav1d",
    detail="not in the configured repos, not in the AUR, and no repo or AUR "
           "package provides it",
)


@pytest.fixture(autouse=True)
def _quiet_run_logger():
    with patch("dasik.lib.actions.packages_action.run_logger.get",
               return_value=MagicMock()) as fake:
        yield fake


def _action(packages, installed=()):
    ctx = ActionContext(target=Target(root="/mnt"))
    a = PackagesAction({"packages": packages}, ctx)
    a._installed_all = lambda: set(installed)          # type: ignore[assignment]
    return a


def _resolution(repo=(), aur=()):
    r = PackageResolution()
    r.repo = list(repo)
    r.aur = list(aur)
    return r


def _apply(a, changes, resolution, broken, aur_calls=None):
    """Drive apply() with the resolver and the closure validator stubbed."""
    execute = MagicMock()
    validator = MagicMock(
        side_effect=broken if isinstance(broken, Exception) else None,
        return_value=broken if not isinstance(broken, Exception) else None,
    )
    recorded = aur_calls if aur_calls is not None else []

    def fake_aur(pkgs, helper=None, **kw):
        recorded.append((list(pkgs), helper))

    with patch.object(PackagesAction, "_resolve_sources",
                      return_value=resolution), \
         patch("dasik.lib.actions.packages_action.Command.execute", execute), \
         patch("dasik.lib.validation.aur_closure.validate_aur_closure",
               validator), \
         patch.object(PackagesAction, "_apply_aur_install",
                      side_effect=fake_aur):
        a.apply(changes)
    return execute, validator, recorded


def test_broken_required_chain_aborts_before_any_mutation():
    a = _action(["base", "lib32-gst-libav"])
    changes = [Change("packages", Op.INSTALL, n)
               for n in ("base", "lib32-gst-libav")]
    execute = MagicMock()
    with patch.object(PackagesAction, "_resolve_sources",
                      return_value=_resolution(repo=["base"],
                                               aur=["lib32-gst-libav"])), \
         patch("dasik.lib.actions.packages_action.Command.execute", execute), \
         patch("dasik.lib.validation.aur_closure.validate_aur_closure",
               return_value=[_INCIDENT]), \
         patch.object(PackagesAction, "_apply_aur_install") as aur:
        with pytest.raises(CommandExecutionError) as exc:
            a.apply(changes)
    msg = str(exc.value)
    assert "lib32-gst-libav → lib32-ffmpeg → lib32-libdav1d" in msg
    # NOTHING mutated: no pacman -S/-D/-Rns, no AUR path entered.
    mutating = [c for c in execute.call_args_list
                if any(f in c.args[1] for f in ("-S", "-D", "-Rns"))]
    assert mutating == []
    aur.assert_not_called()


def test_every_broken_required_chain_is_listed_in_the_abort():
    other = BrokenDep(chain=("foo", "gone"), spec="gone", detail="nowhere")
    a = _action(["lib32-gst-libav", "foo"])
    changes = [Change("packages", Op.INSTALL, n)
               for n in ("lib32-gst-libav", "foo")]
    with patch.object(PackagesAction, "_resolve_sources",
                      return_value=_resolution(aur=["lib32-gst-libav", "foo"])), \
         patch("dasik.lib.actions.packages_action.Command.execute"), \
         patch("dasik.lib.validation.aur_closure.validate_aur_closure",
               return_value=[_INCIDENT, other]):
        with pytest.raises(CommandExecutionError) as exc:
            a.apply(changes)
    msg = str(exc.value)
    assert "lib32-libdav1d" in msg and "foo → gone" in msg


def test_the_gate_walks_the_resolved_aur_roots_with_the_shared_resolver():
    a = _action(["yay"])
    resolution = _resolution(aur=["yay"])
    execute, validator, _ = _apply(
        a, [Change("packages", Op.INSTALL, "yay")], resolution, broken=[])
    roots, resolver, target = validator.call_args.args
    assert list(roots) == ["yay"]
    assert resolver is a._resolver
    assert target is a.context.target


def test_optional_broken_root_warns_and_is_excluded_not_fatal(_quiet_run_logger):
    broken = BrokenDep(chain=("sunshine", "gone-dep"), spec="gone-dep",
                       detail="nowhere")
    a = _action(["yay", {"name": "sunshine", "optional": True}])
    changes = [Change("packages", Op.INSTALL, n) for n in ("yay", "sunshine")]
    aur_calls = []
    _apply(a, changes, _resolution(aur=["yay", "sunshine"]), broken=[broken],
           aur_calls=aur_calls)
    # sunshine never reaches any AUR batch; the required batch still runs
    assert aur_calls == [(["yay"], "yay")]
    assert a.failed_packages == ["sunshine"]
    assert "sunshine" not in a.managed_keys()["packages"]
    warning = _quiet_run_logger.return_value.warning
    assert any("sunshine → gone-dep" in str(c) for c in warning.call_args_list)


def test_rpc_unreachable_mid_closure_aborts_with_retry_wording():
    a = _action(["yay"])
    with patch.object(PackagesAction, "_resolve_sources",
                      return_value=_resolution(aur=["yay"])), \
         patch("dasik.lib.actions.packages_action.Command.execute") as execute, \
         patch("dasik.lib.validation.aur_closure.validate_aur_closure",
               side_effect=AurUnavailableError("timeout")), \
         patch.object(PackagesAction, "_apply_aur_install") as aur:
        with pytest.raises(CommandExecutionError) as exc:
            a.apply([Change("packages", Op.INSTALL, "yay")])
    msg = str(exc.value).lower()
    assert "unavailable" in msg and "retry" in msg
    assert "missing" not in msg
    aur.assert_not_called()
    mutating = [c for c in execute.call_args_list
                if any(f in c.args[1] for f in ("-S", "-D", "-Rns"))]
    assert mutating == []


def test_healthy_closure_leaves_the_install_flow_unchanged():
    a = _action(["base", "yay", "claude-desktop-bin"])
    changes = [Change("packages", Op.INSTALL, n)
               for n in ("base", "yay", "claude-desktop-bin")]
    aur_calls = []
    execute, validator, _ = _apply(
        a, changes, _resolution(repo=["base"], aur=["yay", "claude-desktop-bin"]),
        broken=[], aur_calls=aur_calls)
    validator.assert_called_once()
    assert aur_calls == [(["yay", "claude-desktop-bin"], "yay")]
    repo_txns = [c for c in execute.call_args_list if "-S" in c.args[1]]
    assert any("base" in c.args[1] for c in repo_txns)


def test_no_aur_installs_means_no_closure_work():
    a = _action(["base"])
    execute, validator, _ = _apply(
        a, [Change("packages", Op.INSTALL, "base")],
        _resolution(repo=["base"]), broken=[])
    validator.assert_not_called()


def test_the_resolution_split_is_printed(capsys):
    a = _action(["base", "yay"])
    _apply(a, [Change("packages", Op.INSTALL, n) for n in ("base", "yay")],
           _resolution(repo=["base"], aur=["yay"]), broken=[])
    out = capsys.readouterr().out
    assert "AUR" in out and "yay" in out and "repo" in out

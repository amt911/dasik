"""Optional packages: a peripheral failure must not abort the install (F-04/F-17).

On 2026-07-19 three AUR packages out of 311 failed (sunshine's pkg_resources
transition, two Epson sources returning HTTP 403). yay installed everything else
and exited 1; `check=True` turned that into an exception that stopped the
reconciler before Users, Systemd, Firewall, Snapper, the initramfs and the
bootloader. The disk was already partitioned.

An `optional: true` package may fail without stopping convergence — but it must
NEVER be recorded as managed/installed, so the divergence stays visible and the
next plan retries it.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.packages_action import PackagesAction
from dasik.lib.exceptions.exceptions import CommandExecutionError
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target


def _action(packages, installed=()):
    ctx = ActionContext(target=Target(root="/mnt"))
    a = PackagesAction({"packages": packages}, ctx)
    a._installed_all = lambda: set(installed)          # type: ignore[assignment]
    a.actual = lambda: set(installed)                  # type: ignore[assignment]
    return a


def _resolution(repo=(), aur=()):
    from dasik.lib.actions.package_resolver import PackageResolution
    r = PackageResolution()
    r.repo = list(repo)
    r.aur = list(aur)
    return r


_CFG = [
    "base",
    {"name": "sunshine", "optional": True},
    {"name": "epsonscan2", "optional": True},
]


def test_optional_flag_is_parsed_from_the_package_spec():
    a = _action(_CFG)
    assert a.optional_packages == {"sunshine", "epsonscan2"}
    assert a.desired == ["base", "sunshine", "epsonscan2"]


def test_optional_aur_failure_does_not_abort_apply():
    a = _action(_CFG)
    changes = [Change("packages", Op.INSTALL, n) for n in ("base", "sunshine")]
    with patch.object(PackagesAction, "_resolve_sources",
                      return_value=_resolution(repo=["base"], aur=["sunshine"])), \
         patch("dasik.lib.actions.packages_action.Command.execute") as run, \
         patch.object(PackagesAction, "_apply_aur_install",
                      side_effect=CommandExecutionError("yay failed (exit 1)")):
        a.apply(changes)                        # must NOT raise
    assert run.call_args_list, "the repo transaction still ran"
    assert a.failed_optional == ["sunshine"]


def test_failed_optional_package_is_not_recorded_as_managed():
    a = _action(_CFG)
    changes = [Change("packages", Op.INSTALL, "sunshine")]
    with patch.object(PackagesAction, "_resolve_sources",
                      return_value=_resolution(aur=["sunshine"])), \
         patch("dasik.lib.actions.packages_action.Command.execute"), \
         patch.object(PackagesAction, "_apply_aur_install",
                      side_effect=CommandExecutionError("boom")):
        a.apply(changes)
    managed = a.managed_keys()["packages"]
    assert "sunshine" not in managed
    assert "base" in managed


def test_required_aur_failure_still_aborts():
    a = _action(["base", "yay", "claude-desktop-bin"])
    changes = [Change("packages", Op.INSTALL, "claude-desktop-bin")]
    with patch.object(PackagesAction, "_resolve_sources",
                      return_value=_resolution(aur=["claude-desktop-bin"])), \
         patch("dasik.lib.actions.packages_action.Command.execute"), \
         patch.object(PackagesAction, "_apply_aur_install",
                      side_effect=CommandExecutionError("boom")):
        with pytest.raises(CommandExecutionError):
            a.apply(changes)


def test_optional_and_required_aur_go_in_separate_batches():
    """A failing optional package must not take the required ones down with it."""
    a = _action(["yay", "claude-desktop-bin", {"name": "sunshine", "optional": True}])
    changes = [Change("packages", Op.INSTALL, n)
               for n in ("claude-desktop-bin", "sunshine")]
    batches = []

    def fake_aur(pkgs, helper=None):
        batches.append(list(pkgs))
        if "sunshine" in pkgs:
            raise CommandExecutionError("sunshine failed")

    with patch.object(PackagesAction, "_resolve_sources",
                      return_value=_resolution(aur=["claude-desktop-bin", "sunshine"])), \
         patch("dasik.lib.actions.packages_action.Command.execute"), \
         patch.object(PackagesAction, "_apply_aur_install", side_effect=fake_aur):
        a.apply(changes)
    assert batches == [["claude-desktop-bin"], ["sunshine"]]
    assert a.failed_optional == ["sunshine"]


def test_optional_repo_failure_does_not_abort_apply():
    a = _action(["base", {"name": "obscure-repo-pkg", "optional": True}])
    changes = [Change("packages", Op.INSTALL, n) for n in ("base", "obscure-repo-pkg")]
    calls = []

    def fake_exec(cmd, args, **kw):
        calls.append(args)
        if "obscure-repo-pkg" in args:
            raise CommandExecutionError("pacman failed (exit 1)")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    with patch.object(PackagesAction, "_resolve_sources",
                      return_value=_resolution(repo=["base", "obscure-repo-pkg"])), \
         patch("dasik.lib.actions.packages_action.Command.execute", side_effect=fake_exec):
        a.apply(changes)
    assert a.failed_optional == ["obscure-repo-pkg"]
    # required packages went in their own transaction, which succeeded
    assert any("base" in c and "obscure-repo-pkg" not in c for c in calls)


def test_unknown_optional_name_is_skipped_even_under_the_error_policy():
    """`config-saver` & friends exist nowhere; marked optional they must not
    abort a strict-policy apply, and must not be claimed as managed."""
    ctx = ActionContext(target=Target(root="/mnt"))
    a = PackagesAction({"packages": ["base", {"name": "config-saver", "optional": True}],
                        "package_policy": {"unknown": "error"}}, ctx)
    a._installed_all = lambda: set()                    # type: ignore[assignment]
    from dasik.lib.actions.package_resolver import PackageResolution
    res = PackageResolution()
    res.repo = ["base"]
    res.unknown = ["config-saver"]
    with patch.object(PackagesAction, "_resolve_sources", return_value=res), \
         patch("dasik.lib.actions.packages_action.Command.execute"):
        a.apply([Change("packages", Op.INSTALL, "base"),
                 Change("packages", Op.INSTALL, "config-saver")])
    assert "config-saver" not in a.managed_keys()["packages"]


def test_unknown_required_name_still_aborts_under_the_error_policy():
    ctx = ActionContext(target=Target(root="/mnt"))
    a = PackagesAction({"packages": ["base", "config-saver"],
                        "package_policy": {"unknown": "error"}}, ctx)
    a._installed_all = lambda: set()                    # type: ignore[assignment]
    from dasik.lib.actions.package_resolver import PackageResolution
    res = PackageResolution()
    res.repo = ["base"]
    res.unknown = ["config-saver"]
    with patch.object(PackagesAction, "_resolve_sources", return_value=res), \
         patch("dasik.lib.actions.packages_action.Command.execute"):
        with pytest.raises(CommandExecutionError):
            a.apply([Change("packages", Op.INSTALL, "config-saver")])


def test_import_state_keeps_the_optional_flag():
    a = _action(_CFG, installed=["base", "sunshine", "epsonscan2"])
    entries = a.import_state()["packages"]
    assert {"name": "sunshine", "optional": True} in entries

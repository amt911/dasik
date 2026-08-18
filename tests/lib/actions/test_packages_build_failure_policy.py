"""package_policy.build_failure — machine-wide continue-on-failure semantics.

`abort` (default) keeps today's behavior: a required package whose install or
build fails stops the apply. `warn-and-continue` gives every package the
semantics `optional: true` gives one: report, keep it out of the manifest
(plan re-shows it, next apply retries), and carry on with everything else.
"""
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.package_resolver import PackageResolution
from dasik.lib.actions.packages_action import PackagesAction
from dasik.lib.exceptions.exceptions import CommandExecutionError
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target
from dasik.lib.validation.aur_closure import BrokenDep


@pytest.fixture(autouse=True)
def _aur_closure_satisfiable():
    with patch("dasik.lib.validation.aur_closure.validate_aur_closure",
               return_value=[]):
        yield


@pytest.fixture(autouse=True)
def _quiet_run_logger():
    with patch("dasik.lib.actions.packages_action.run_logger.get",
               return_value=MagicMock()) as fake:
        yield fake


def _action(packages, policy=None, installed=()):
    cfg = {"packages": packages}
    if policy is not None:
        cfg["package_policy"] = policy
    a = PackagesAction(cfg, ActionContext(target=Target(root="/mnt")))
    a._installed_all = lambda: set(installed)          # type: ignore[assignment]
    return a


def _resolution(repo=(), aur=(), git=()):
    r = PackageResolution()
    r.repo = list(repo)
    r.aur = list(aur)
    r.git = list(git)
    return r


_CONTINUE = {"build_failure": "warn-and-continue"}


def test_default_policy_still_aborts_on_a_required_aur_failure():
    a = _action(["yay", "broken-pkg"])
    with patch.object(PackagesAction, "_resolve_sources",
                      return_value=_resolution(aur=["yay", "broken-pkg"])), \
         patch("dasik.lib.actions.packages_action.Command.execute"), \
         patch.object(PackagesAction, "_apply_aur_install",
                      side_effect=CommandExecutionError("build died")):
        with pytest.raises(CommandExecutionError):
            a.apply([Change("packages", Op.INSTALL, "broken-pkg")])


def test_continue_policy_swallows_a_required_aur_failure_and_records_it():
    a = _action(["yay", "broken-pkg"], policy=_CONTINUE, installed=["yay"])
    with patch.object(PackagesAction, "_resolve_sources",
                      return_value=_resolution(aur=["yay", "broken-pkg"])), \
         patch("dasik.lib.actions.packages_action.Command.execute"), \
         patch.object(PackagesAction, "_apply_aur_install",
                      side_effect=CommandExecutionError("build died")):
        a.apply([Change("packages", Op.INSTALL, n)
                 for n in ("yay", "broken-pkg")])   # must NOT raise
    assert a.failed_packages == ["broken-pkg"]      # yay was installed, salvage
    assert "broken-pkg" not in a.managed_keys()["packages"]
    assert "yay" in a.managed_keys()["packages"]


def test_continue_policy_falls_back_per_package_on_a_repo_batch_failure():
    a = _action(["git", "htop", "cursed"], policy=_CONTINUE)
    calls = []

    def fake_exec(cmd, args, **kw):
        calls.append(list(args))
        if "-S" in args and ("cursed" in args and len([x for x in args if not x.startswith("-")]) > 1):
            raise CommandExecutionError("batch failed")   # batch with all three
        if args[-1] == "cursed":
            raise CommandExecutionError("cursed failed")  # per-package retry
        return MagicMock(returncode=0, stdout=b"", stderr=b"")

    with patch.object(PackagesAction, "_resolve_sources",
                      return_value=_resolution(repo=["git", "htop", "cursed"])), \
         patch("dasik.lib.actions.packages_action.Command.execute",
               side_effect=fake_exec):
        a.apply([Change("packages", Op.INSTALL, n)
                 for n in ("git", "htop", "cursed")])     # must NOT raise
    singles = [c[-1] for c in calls if "-S" in c
               and len([x for x in c if not x.startswith("-")]) == 1]
    assert singles == ["git", "htop", "cursed"]           # per-package fallback
    assert a.failed_packages == ["cursed"]


def test_abort_policy_still_raises_on_a_repo_batch_failure():
    a = _action(["git"])
    with patch.object(PackagesAction, "_resolve_sources",
                      return_value=_resolution(repo=["git"])), \
         patch("dasik.lib.actions.packages_action.Command.execute",
               side_effect=CommandExecutionError("batch failed")):
        with pytest.raises(CommandExecutionError):
            a.apply([Change("packages", Op.INSTALL, "git")])


def test_continue_policy_downgrades_a_broken_required_closure_chain(_quiet_run_logger):
    broken = BrokenDep(chain=("lib32-gst-libav", "lib32-ffmpeg", "gone"),
                       spec="gone", detail="nowhere")
    a = _action(["yay", "lib32-gst-libav"], policy=_CONTINUE, installed=["yay"])
    batches = []

    def fake_aur(pkgs, helper=None, **kw):
        batches.append(list(pkgs))

    with patch.object(PackagesAction, "_resolve_sources",
                      return_value=_resolution(aur=["yay", "lib32-gst-libav"])), \
         patch("dasik.lib.actions.packages_action.Command.execute"), \
         patch("dasik.lib.validation.aur_closure.validate_aur_closure",
               return_value=[broken]), \
         patch.object(PackagesAction, "_apply_aur_install",
                      side_effect=fake_aur):
        a.apply([Change("packages", Op.INSTALL, n)
                 for n in ("yay", "lib32-gst-libav")])    # must NOT raise
    assert batches == [(["yay"])]                          # root dropped
    assert a.failed_packages == ["lib32-gst-libav"]
    warning = _quiet_run_logger.return_value.warning
    assert any("lib32-gst-libav" in str(c) for c in warning.call_args_list)


def test_continue_policy_records_a_git_build_failure_and_carries_on():
    from dasik.lib.actions.package_resolver import ResolvedGitPackage
    git_pkg = ResolvedGitPackage(name="mytool", source={"type": "git"})
    a = _action(["git", "mytool"], policy=_CONTINUE, installed=["git"])
    with patch.object(PackagesAction, "_resolve_sources",
                      return_value=_resolution(repo=["git"], git=[git_pkg])), \
         patch("dasik.lib.actions.packages_action.Command.execute"), \
         patch.object(PackagesAction, "_apply_git_install",
                      side_effect=CommandExecutionError("clone died")):
        a.apply([Change("packages", Op.INSTALL, n)
                 for n in ("git", "mytool")])              # must NOT raise
    assert a.failed_packages == ["mytool"]
    assert "mytool" not in a.managed_keys()["packages"]


def test_continue_policy_prints_an_end_of_domain_summary(capsys):
    a = _action(["yay", "broken-pkg"], policy=_CONTINUE, installed=["yay"])
    with patch.object(PackagesAction, "_resolve_sources",
                      return_value=_resolution(aur=["yay", "broken-pkg"])), \
         patch("dasik.lib.actions.packages_action.Command.execute"), \
         patch.object(PackagesAction, "_apply_aur_install",
                      side_effect=CommandExecutionError("build died")):
        a.apply([Change("packages", Op.INSTALL, "broken-pkg")])
    out = capsys.readouterr().out
    assert "broken-pkg" in out
    assert "not installed" in out


def test_optional_and_policy_failures_do_not_duplicate():
    a = _action(["yay", {"name": "sunshine", "optional": True}],
                policy=_CONTINUE, installed=["yay"])
    with patch.object(PackagesAction, "_resolve_sources",
                      return_value=_resolution(aur=["yay", "sunshine"])), \
         patch("dasik.lib.actions.packages_action.Command.execute"), \
         patch.object(PackagesAction, "_apply_aur_install",
                      side_effect=CommandExecutionError("boom")):
        a.apply([Change("packages", Op.INSTALL, n)
                 for n in ("yay", "sunshine")])
    assert a.failed_packages == ["sunshine"]

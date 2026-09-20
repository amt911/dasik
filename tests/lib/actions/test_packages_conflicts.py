"""A conflicting installed package must be named, not dumped.

2026-09-20, MSI GE63: swapping `jdupes-git` for the repo `jdupes` left the
AUR `libjodycode-git` behind, and every apply from then on ended in

    :: libjodycode-4.1.2-3 and libjodycode-git-… are in conflict. Remove libjodycode-git? [y/N]
    error: unresolvable package conflicts detected

repeated twice (the batch, then the per-package salvage), with no statement of
what to do about it. The answer — `pacman -Rns libjodycode-git` — was in
pacman's own text and dasik walked past it.

Two guarantees here:
  * `plan` sees the conflict BEFORE anything mutates, and says the exact
    removal command. A plan stays read-only: it warns, it plans nothing extra.
  * `apply` says the same thing, and under `package_policy.conflicts:
    "replace"` clears an UNDECLARED blocker itself so convergence continues.
"""
from unittest.mock import MagicMock, call, patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.package_resolver import PackageResolution
from dasik.lib.actions.packages_action import PackagesAction
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target

CONFLICT = (
    ":: libjodycode-4.1.2-3 and libjodycode-git-4.1.2.r12.g1f56137-1 are in "
    "conflict. Remove libjodycode-git? [y/N] error: unresolvable package "
    "conflicts detected\n"
)


@pytest.fixture(autouse=True)
def _aur_closure_satisfiable():
    with patch("dasik.lib.validation.aur_closure.validate_aur_closure",
               return_value=[]):
        yield


@pytest.fixture
def warnings():
    """Every warning/error text the action logged, as one list of strings."""
    logged: list = []

    def record(msg, detail=None, **kw):
        logged.append(f"{msg}\n{detail or ''}")

    logger = MagicMock()
    logger.warning.side_effect = record
    logger.error.side_effect = record
    with patch("dasik.lib.actions.packages_action.run_logger.get",
               return_value=logger):
        yield logged


def _action(packages, policy=None, installed=()):
    cfg = {"packages": list(packages)}
    if policy is not None:
        cfg["package_policy"] = policy
    a = PackagesAction(cfg, ActionContext(target=Target(root="/mnt")))
    a._installed_all = lambda: set(installed)          # type: ignore[assignment]
    a._explicit_raw = lambda: set(installed)           # type: ignore[assignment]
    a.actual = lambda: set(installed)                  # type: ignore[assignment]
    return a


def _probe(stderr="", returncode=0):
    return MagicMock(stdout=b"", stderr=stderr.encode(), returncode=returncode)


# --------------------------------------------------------------- plan


def test_plan_names_the_exact_removal_command(warnings):
    a = _action(["jdupes"], installed=["libjodycode-git"])
    with patch("dasik.lib.actions.packages_action.Command.execute",
               return_value=_probe(CONFLICT, returncode=1)):
        changes = a.plan(managed=[])
    assert [(c.op, c.item) for c in changes] == [(Op.INSTALL, "jdupes")]
    said = "\n".join(warnings)
    assert "libjodycode-git" in said
    assert "pacman -Rns libjodycode-git" in said


def test_plan_stays_quiet_when_nothing_conflicts(warnings):
    a = _action(["jdupes"], installed=[])
    with patch("dasik.lib.actions.packages_action.Command.execute",
               return_value=_probe()):
        a.plan(managed=[])
    assert not [w for w in warnings if "conflict" in w]


def test_a_converged_config_never_probes():
    """Nothing to install: a plan must not pay for a `pacman -Sp`."""
    a = _action(["jdupes"], installed=["jdupes"])
    with patch("dasik.lib.actions.packages_action.Command.execute") as ex:
        a.plan(managed=[])
    assert not [c for c in ex.call_args_list if "-Sp" in (c.args[1] if len(c.args) > 1 else [])]


def test_a_probe_that_cannot_run_plans_exactly_as_before(warnings):
    a = _action(["jdupes"], installed=[])
    with patch("dasik.lib.actions.packages_action.Command.execute",
               side_effect=OSError("no pacman here")):
        changes = a.plan(managed=[])
    assert [(c.op, c.item) for c in changes] == [(Op.INSTALL, "jdupes")]
    assert not [w for w in warnings if "conflict" in w]


# -------------------------------------------------------------- apply


def _apply(action, monkey_execute):
    with patch.object(PackagesAction, "_resolve_sources",
                      return_value=_resolution(repo=["jdupes"])), \
         patch.object(PackagesAction, "_gate_aur_closure", side_effect=lambda p, t: p), \
         patch.object(PackagesAction, "_enforce_reasons"), \
         patch("dasik.lib.actions.packages_action.Command.execute",
               side_effect=monkey_execute) as ex:
        action.apply([Change("packages", Op.INSTALL, "jdupes")])
    return ex


def _resolution(repo=(), aur=(), git=()):
    r = PackageResolution()
    r.repo = list(repo)
    r.aur = list(aur)
    r.git = list(git)
    return r


def _pacman_that_conflicts(removed=()):
    """A pacman where -Sp reports the conflict until `libjodycode-git` is gone."""
    state = {"blocker": True}

    def execute(cmd, args, **kw):
        if cmd == "pacman" and "-Sp" in args:
            return _probe(CONFLICT if state["blocker"] else "",
                          returncode=1 if state["blocker"] else 0)
        if cmd == "pacman" and "-Rns" in args:
            state["blocker"] = False
            return MagicMock(returncode=0, stdout=b"", stderr=b"")
        if cmd == "pacman" and "-Qi" in args:
            return MagicMock(returncode=0, stderr=b"", stdout=(
                b"Name            : libjodycode-git\n"
                b"Required By     : None\n"))
        return MagicMock(returncode=0, stdout=b"", stderr=b"")

    return execute


def test_apply_says_the_removal_command_before_pacman_dumps(warnings):
    a = _action(["jdupes"], installed=["libjodycode-git"])
    _apply(a, _pacman_that_conflicts())
    said = "\n".join(warnings)
    assert "pacman -Rns libjodycode-git" in said


def test_the_default_policy_removes_nothing():
    a = _action(["jdupes"], installed=["libjodycode-git"])
    ex = _apply(a, _pacman_that_conflicts())
    assert not [c for c in ex.call_args_list if "-Rns" in c.args[1]]


def test_replace_removes_an_undeclared_blocker_then_installs():
    a = _action(["jdupes"], policy={"conflicts": "replace"},
                installed=["libjodycode-git"])
    ex = _apply(a, _pacman_that_conflicts())
    calls = [c.args[1] for c in ex.call_args_list if c.args[0] == "pacman"]
    removal = next(i for i, args in enumerate(calls) if "-Rns" in args)
    install = next(i for i, args in enumerate(calls)
                   if "-S" in args and "-Sp" not in args)
    assert "libjodycode-git" in calls[removal]
    assert removal < install, "the blocker must go BEFORE the install"


def test_replace_refuses_to_remove_a_package_the_config_declares(warnings):
    """Both sides declared is a config the user has to fix; dasik must not
    silently delete half of what was asked for."""
    a = _action(["jdupes", "libjodycode-git"], policy={"conflicts": "replace"},
                installed=["libjodycode-git"])
    ex = _apply(a, _pacman_that_conflicts())
    assert not [c for c in ex.call_args_list if "-Rns" in c.args[1]]
    assert "declares both" in "\n".join(warnings)


def test_replace_refuses_a_blocker_another_installed_package_requires(warnings):
    a = _action(["jdupes"], policy={"conflicts": "replace"},
                installed=["libjodycode-git", "jdupes-git"])

    def execute(cmd, args, **kw):
        if cmd == "pacman" and "-Sp" in args:
            return _probe(CONFLICT, returncode=1)
        if cmd == "pacman" and "-Qi" in args:
            return MagicMock(returncode=0, stderr=b"", stdout=(
                b"Name            : libjodycode-git\n"
                b"Required By     : jdupes-git\n"))
        return MagicMock(returncode=0, stdout=b"", stderr=b"")

    ex = _apply(a, execute)
    assert not [c for c in ex.call_args_list if "-Rns" in c.args[1]]
    assert "jdupes-git" in "\n".join(warnings)

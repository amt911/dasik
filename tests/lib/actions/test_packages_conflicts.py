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
from dasik.lib.exceptions.exceptions import CommandExecutionError
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target

# Verbatim from the GE63 apply log.
CONFLICT = (
    "pacman failed (exit 1):\n"
    ":: libjodycode-4.1.2-3 and libjodycode-git-4.1.2.r12.g1f56137-1 are in "
    "conflict. Remove libjodycode-git? [y/N] error: unresolvable package "
    "conflicts detected\n"
    "error: failed to prepare transaction (conflicting dependencies)\n"
)
# `plan` runs no transaction, so it reads both sides' metadata instead
# (MEASURED: no pacman probe reports a conflict read-only — see
# tests/lib/actions/test_pacman_conflicts.py).
SI = ("Name            : jdupes\n"
      "Provides        : libjodycode\n"
      "Conflicts With  : None\n")
QI = ("Name            : libjodycode-git\n"
      "Provides        : libjodycode\n"
      "Conflicts With  : libjodycode\n")


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


def _metadata(si=SI, qi=QI):
    """A pacman that answers `-Si <candidates>` and `-Qi` (the whole machine)."""
    def execute(cmd, args, **kw):
        if cmd == "pacman" and args[0] == "-Si":
            return MagicMock(stdout=si.encode(), stderr=b"", returncode=0)
        if cmd == "pacman" and args[0] == "-Qi":
            return MagicMock(stdout=qi.encode(), stderr=b"", returncode=0)
        # Everything else is a plan probe (`-T`, `-Sg`): nothing satisfied,
        # no groups, so the declared name really is an install.
        return MagicMock(stdout=b"", stderr=b"", returncode=1)
    return execute


# --------------------------------------------------------------- plan


def test_plan_names_the_exact_removal_command(warnings):
    a = _action(["jdupes"], installed=["libjodycode-git"])
    with patch("dasik.lib.actions.packages_action.Command.execute",
               side_effect=_metadata()):
        changes = a.plan(managed=[])
    assert [(c.op, c.item) for c in changes] == [(Op.INSTALL, "jdupes")]
    said = "\n".join(warnings)
    assert "libjodycode-git" in said
    assert "pacman -Rns libjodycode-git" in said


def test_plan_stays_quiet_when_nothing_conflicts(warnings):
    a = _action(["jdupes"], installed=[])
    with patch("dasik.lib.actions.packages_action.Command.execute",
               side_effect=_metadata(qi="Name            : htop\n")):
        a.plan(managed=[])
    assert not [w for w in warnings if "conflict" in w]


def test_a_converged_config_never_probes():
    """Nothing to install: a plan must not pay for a `pacman -Sp`."""
    a = _action(["jdupes"], installed=["jdupes"])
    with patch("dasik.lib.actions.packages_action.Command.execute") as ex:
        a.plan(managed=[])
    assert not [c for c in ex.call_args_list
                if len(c.args) > 1 and c.args[1] and c.args[1][0] == "-Si"]


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


def _pacman_that_conflicts(required_by=b"None"):
    """The real mechanism: `pacman -S` FAILS with the conflict verdict until
    `libjodycode-git` is gone. Nothing else can report it (measured)."""
    state = {"blocker": True}

    def execute(cmd, args, **kw):
        if cmd == "pacman" and "-S" in args and state["blocker"]:
            raise CommandExecutionError(CONFLICT)
        if cmd == "pacman" and "-Rns" in args:
            state["blocker"] = False
            return MagicMock(returncode=0, stdout=b"", stderr=b"")
        if cmd == "pacman" and args and args[0] == "-Qi":
            return MagicMock(returncode=0, stderr=b"", stdout=(
                b"Name            : libjodycode-git\nRequired By     : "
                + required_by + b"\n"))
        return MagicMock(returncode=0, stdout=b"", stderr=b"")

    return execute


def test_apply_says_the_removal_command_when_pacman_refuses(warnings):
    a = _action(["jdupes"], policy={"build_failure": "warn-and-continue"},
                installed=["libjodycode-git"])
    _apply(a, _pacman_that_conflicts())
    said = "\n".join(warnings)
    assert "pacman -Rns libjodycode-git" in said


def test_the_default_policy_removes_nothing():
    a = _action(["jdupes"], policy={"build_failure": "warn-and-continue"},
                installed=["libjodycode-git"])
    ex = _apply(a, _pacman_that_conflicts())
    assert not [c for c in ex.call_args_list if "-Rns" in c.args[1]]


def test_replace_removes_an_undeclared_blocker_then_installs():
    a = _action(["jdupes"], policy={"conflicts": "replace"},
                installed=["libjodycode-git"])
    ex = _apply(a, _pacman_that_conflicts())
    calls = [c.args[1] for c in ex.call_args_list if c.args[0] == "pacman"]
    removal = next(i for i, args in enumerate(calls) if "-Rns" in args)
    retry = [i for i, args in enumerate(calls) if "-S" in args]
    assert "libjodycode-git" in calls[removal]
    assert any(i > removal for i in retry), \
        "the same transaction must be retried once the blocker is gone"
    assert not a.failed_packages, "the retry succeeded: nothing failed"


def test_replace_refuses_to_remove_a_package_the_config_declares(warnings):
    """Both sides declared is a config the user has to fix; dasik must not
    silently delete half of what was asked for."""
    a = _action(["jdupes", "libjodycode-git"],
                policy={"conflicts": "replace",
                        "build_failure": "warn-and-continue"},
                installed=["libjodycode-git"])
    ex = _apply(a, _pacman_that_conflicts())
    assert not [c for c in ex.call_args_list if "-Rns" in c.args[1]]
    assert "declares both" in "\n".join(warnings)


def test_replace_refuses_a_blocker_another_installed_package_requires(warnings):
    a = _action(["jdupes"], policy={"conflicts": "replace",
                                    "build_failure": "warn-and-continue"},
                installed=["libjodycode-git", "jdupes-git"])
    ex = _apply(a, _pacman_that_conflicts(required_by=b"jdupes-git"))
    assert not [c for c in ex.call_args_list if "-Rns" in c.args[1]]
    assert "jdupes-git" in "\n".join(warnings)


def test_a_removal_the_apply_itself_already_did_is_dropped():
    """Found in a guest (2026-09-20), with the unit suite green.

    The plan said `- remove openresolv (no longer declared)` AND
    `+ install systemd-resolvconf`. Installing hit the conflict, `replace`
    removed openresolv, the retry installed — and then the apply reached its
    own planned removal of a package that was no longer there:

        $ pacman --noconfirm -Rns openresolv
        error: target not found: openresolv
        error: apply failed

    A converged machine, and an aborted apply with a partial generation. What
    a plan decided about the machine can stop being true DURING the apply, so
    the removal transaction takes only what is still installed.
    """
    # A LIVE machine: the removal actually takes the blocker off it, which a
    # fixed `installed` set would hide.
    machine = {"libjodycode-git"}
    a = _action(["jdupes"], policy={"conflicts": "replace"}, installed=machine)
    a._installed_all = lambda: set(machine)        # type: ignore[assignment]
    execute = _pacman_that_conflicts()

    def with_removal(cmd, args, **kw):
        result = execute(cmd, args, **kw)
        if cmd == "pacman" and "-Rns" in args:
            machine.discard("libjodycode-git")
        return result

    with patch.object(PackagesAction, "_resolve_sources",
                      return_value=_resolution(repo=["jdupes"])), \
         patch.object(PackagesAction, "_gate_aur_closure", side_effect=lambda p, t: p), \
         patch.object(PackagesAction, "_enforce_reasons"), \
         patch("dasik.lib.actions.packages_action.Command.execute",
               side_effect=with_removal) as ex:
        a.apply([Change("packages", Op.INSTALL, "jdupes"),
                 Change("packages", Op.REMOVE, "libjodycode-git",
                        reason="no longer declared")])

    removals = [c.args[1] for c in ex.call_args_list
                if c.args[0] == "pacman" and "-Rns" in c.args[1]]
    assert len(removals) == 1, \
        f"the blocker must be removed once, not once per store: {removals}"


def test_a_removal_of_something_already_gone_is_not_attempted(warnings):
    """The same rule without any conflict: pacman fails the WHOLE `-Rns`
    transaction on one unknown name, so a stale removal would take the real
    ones with it."""
    a = _action([], installed=["still-here"])

    def execute(cmd, args, **kw):
        if cmd == "pacman" and args and args[0] == "-Qq":
            return MagicMock(returncode=0, stderr=b"", stdout=b"still-here\n")
        return MagicMock(returncode=0, stdout=b"", stderr=b"")

    with patch.object(PackagesAction, "_enforce_reasons"), \
         patch("dasik.lib.actions.packages_action.Command.execute",
               side_effect=execute) as ex:
        a.apply([Change("packages", Op.REMOVE, "long-gone",
                        reason="no longer declared"),
                 Change("packages", Op.REMOVE, "still-here",
                        reason="no longer declared")])

    removals = [c.args[1] for c in ex.call_args_list
                if c.args[0] == "pacman" and "-Rns" in c.args[1]]
    assert removals == [["--noconfirm", "-Rns", "still-here"]]

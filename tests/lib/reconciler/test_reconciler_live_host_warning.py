"""Spec §5 / issue #63: a prominent warning must be printed when destructive
changes are applied against the running host (--target /)."""
from unittest.mock import MagicMock

from dasik.lib.actions.abstract_action import AbstractAction
from dasik.lib.reconciler.reconciler import ActionPlanResult, Reconciler
from dasik.lib.state.change import Change, Op, Plan
from dasik.lib.target.target import Target


class _V3(AbstractAction):
    @property
    def name(self) -> str: return "rec"
    def is_needed(self) -> bool: return False
    def execute(self) -> None: pass
    def plan(self, managed): return []
    def apply(self, changes): pass
    def managed_keys(self): return {"packages": []}


def _reconciler(root):
    return Reconciler(
        config={"packages": []},
        target=Target(root=root),
        manifest={"managed": {}},
        action_metas=[],
        state_store=MagicMock(),
        generation_store=MagicMock(),
    )


def _destructive_plan():
    plan = Plan()
    plan.add(Change("packages", Op.REMOVE, "vim"))
    a = _V3(config=[], context=None)
    return plan, [ActionPlanResult(action=a, changes=list(plan.changes))]


def _additive_plan():
    plan = Plan()
    plan.add(Change("packages", Op.INSTALL, "git"))
    a = _V3(config=["git"], context=None)
    return plan, [ActionPlanResult(action=a, changes=list(plan.changes))]


def test_warns_on_destructive_against_running_host(capsys):
    r = _reconciler("/")
    plan, results = _destructive_plan()
    r.apply(plan, results, assume_yes=True)
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "running host" in err.lower()
    assert "--target /" in err


def test_no_warning_against_install_target(capsys):
    r = _reconciler("/mnt")
    plan, results = _destructive_plan()
    r.apply(plan, results, assume_yes=True)
    assert "WARNING" not in capsys.readouterr().err


def test_warning_shown_even_with_assume_yes(capsys):
    """rollback defaults to --target / and --yes; the heads-up still matters."""
    r = _reconciler("/")
    plan, results = _destructive_plan()
    r.apply(plan, results, assume_yes=True)
    assert "WARNING" in capsys.readouterr().err


def test_warning_precedes_confirmation_prompt(capsys):
    r = _reconciler("/")
    plan, results = _destructive_plan()
    r.apply(plan, results, assume_yes=False, input_fn=lambda _: "n")
    err = capsys.readouterr().err
    assert "WARNING" in err  # warning goes to stderr, prompt to stdin


def test_no_warning_for_additive_plan_on_host(capsys):
    r = _reconciler("/")
    plan, results = _additive_plan()
    r.apply(plan, results, assume_yes=True)
    assert "WARNING" not in capsys.readouterr().err

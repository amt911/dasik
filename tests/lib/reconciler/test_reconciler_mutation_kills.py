"""Targeted tests that pin real idempotency/safety logic in the Reconciler which
mutation testing (scripts/mutation.sh --reconciler) found covered-but-unverified:

- build_plan must process EVERY v3 action, not stop at the first skipped one
  (a `continue`->`break` mutant truncated the plan silently).
- apply must prompt before destructive changes by DEFAULT (an `assume_yes=False`
  ->`True` mutant skipped the confirmation).
- _domain_for must reject multi-domain actions (a `len>1`->`len>2` mutant let a
  2-domain action through).
- a `__root__` action must receive the whole config (mutants set it to None / a
  broken key and it went unnoticed).
"""
from unittest.mock import MagicMock

import pytest

from dasik.lib.actions.abstract_action import AbstractAction
from dasik.lib.reconciler.reconciler import ActionPlanResult, Reconciler
from dasik.lib.state.change import Change, Op, Plan
from dasik.lib.target.target import Target


def _meta(cls, config_key):
    return {"class": cls, "config_key": config_key,
            "is_optional": True, "required_fields": [], "depends_on": []}


class _Base(AbstractAction):
    @property
    def name(self) -> str: return "stub"
    def is_needed(self) -> bool: return False
    def execute(self) -> None: pass


class _Yields(_Base):
    """v3 action that always yields one change."""
    def plan(self, managed): return [Change("packages", Op.INSTALL, "git")]
    def managed_keys(self): return {"packages": []}


class _SkippedOptional(_Base):
    """v3 optional action whose config slice is absent and owns nothing → skipped."""
    def plan(self, managed): return [Change("skip", Op.INSTALL, "x")]
    def managed_keys(self): return {"skipdomain": []}


class _NonV3(_Base):
    """Legacy action (does not override plan) → skipped by build_plan."""
    def managed_keys(self): return {"legacy": []}


def _reconciler(config, metas):
    return Reconciler(config=config, target=Target(root="/mnt"),
                      manifest={"managed": {}}, action_metas=metas)


def test_build_plan_keeps_going_after_a_skipped_optional_action():
    # _SkippedOptional (config_key absent, owns nothing) is skipped; the LATER
    # _Yields must still be planned. A continue->break mutant truncates here.
    r = _reconciler({"packages": ["git"]},
                    [_meta(_SkippedOptional, "missing"), _meta(_Yields, "packages")])
    plan, results = r.build_plan()
    assert [c.item for c in plan.changes] == ["git"]
    assert len(results) == 1


def test_build_plan_keeps_going_after_a_non_v3_action():
    # _NonV3 hits `if not cls.is_v3(): continue`; _Yields after it must still run.
    r = _reconciler({"packages": ["git"]},
                    [_meta(_NonV3, "legacy"), _meta(_Yields, "packages")])
    plan, _ = r.build_plan()
    assert [c.item for c in plan.changes] == ["git"]


class _RootAware(_Base):
    """__root__ action that records the config object it was handed."""
    seen: list = []
    def __init__(self, config, context=None):
        super().__init__(config, context)
        type(self).seen.append(config)
    def plan(self, managed): return []
    def managed_keys(self): return {"root": []}


def test_root_action_receives_the_whole_config():
    _RootAware.seen = []
    cfg = {"packages": ["git"], "hostname": "h"}
    r = _reconciler(cfg, [_meta(_RootAware, "__root__")])
    r.build_plan()
    assert _RootAware.seen == [cfg]          # got the full config, not None / a slice


class _TwoDomain(_Base):
    def plan(self, managed): return []
    def managed_keys(self): return {"a": [], "b": []}


def test_domain_for_rejects_multi_domain_action():
    with pytest.raises(ValueError):
        Reconciler._domain_for(_TwoDomain(config=[], context=None))


class _RemovingAction(_Base):
    applied = None
    def plan(self, managed): return [Change("packages", Op.REMOVE, "git")]
    def managed_keys(self): return {"packages": []}
    def apply(self, changes): type(self).applied = list(changes)


def test_apply_requires_confirmation_by_default_for_destructive_changes():
    # Default (no assume_yes) must PROMPT; answering "n" aborts and applies nothing.
    _RemovingAction.applied = None
    r = _reconciler({"packages": []}, [])
    plan = Plan()
    plan.extend([Change("packages", Op.REMOVE, "git")])
    action = _RemovingAction(config=[], context=None)
    results = [ActionPlanResult(action=action, changes=list(plan.changes))]

    prompt = MagicMock(return_value="n")
    out = r.apply(plan, results, input_fn=prompt)

    assert prompt.called                      # it asked (default is NOT assume_yes)
    assert out is None                         # aborted
    assert _RemovingAction.applied is None     # nothing applied

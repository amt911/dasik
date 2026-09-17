import json
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from dasik.lib.actions.abstract_action import AbstractAction
from dasik.lib.reconciler.reconciler import ActionPlanResult, Reconciler
from dasik.lib.state.change import Change, Op, Plan
from dasik.lib.target.target import Target


class _RecordingV3(AbstractAction):
    """Stub v3 action that records apply() calls + owns one domain."""

    last_applied: list = []

    @property
    def name(self) -> str: return "rec"
    def is_needed(self) -> bool: return False
    def execute(self) -> None: pass

    def plan(self, managed):
        return []

    def apply(self, changes):
        type(self).last_applied = list(changes)

    def managed_keys(self):
        return {"packages": list(self.config) if isinstance(self.config, list) else []}


def _make_reconciler(*, config=None, manifest=None, store=None, gen_store=None):
    return Reconciler(
        config=config or {"packages": []},
        target=Target(root="/"),
        manifest=manifest or {"managed": {}},
        action_metas=[],
        state_store=store,
        generation_store=gen_store,
    )


def test_apply_noop_when_plan_is_empty():
    store = MagicMock()
    gen = MagicMock()
    r = _make_reconciler(store=store, gen_store=gen)
    new_manifest = r.apply(Plan(), [], assume_yes=True)
    store.save.assert_not_called()
    gen.new.assert_not_called()
    assert new_manifest is None


def test_apply_runs_each_action_apply_in_order():
    """Both actions' apply() called with their own change slice, in order."""
    store = MagicMock()
    gen = MagicMock()
    r = _make_reconciler(store=store, gen_store=gen)

    call_log: list = []

    class _Logging(AbstractAction):
        @property
        def name(self) -> str: return "log"
        def is_needed(self) -> bool: return False
        def execute(self) -> None: pass
        def plan(self, managed): return []
        def apply(self, changes):
            call_log.append((self, list(changes)))
        def managed_keys(self):
            return {"packages": list(self.config) if isinstance(self.config, list) else []}

    a1 = _Logging(config=["git"], context=None)
    a2 = _Logging(config=["htop"], context=None)
    c1 = Change("packages", Op.INSTALL, "git")
    c2 = Change("packages", Op.INSTALL, "htop")
    plan = Plan()
    plan.add(c1)
    plan.add(c2)
    results = [
        ActionPlanResult(action=a1, changes=[c1]),
        ActionPlanResult(action=a2, changes=[c2]),
    ]
    r.apply(plan, results, assume_yes=True)
    assert call_log == [(a1, [c1]), (a2, [c2])]


# --- SF-3: finalize_apply() runs once, after every action has applied ----- #
#
# FirewallAction runs BEFORE PackagesAction/SystemdAction (so a ufw REMOVE
# can still shell out to `ufw` while the binary is there), so an immediate
# daemon reload attempted from inside its own apply() can hit a transiently-
# missing `firewalld` package. finalize_apply() is where it retries once the
# whole apply has settled -- called by the reconciler, not a new plugin
# system: one optional method per action, the same shape as verify().

def test_apply_calls_finalize_apply_on_every_completed_action():
    store = MagicMock()
    gen = MagicMock()
    r = _make_reconciler(store=store, gen_store=gen)
    calls: list = []

    class _Finalizing(AbstractAction):
        @property
        def name(self) -> str: return "fin"
        def is_needed(self) -> bool: return False
        def execute(self) -> None: pass
        def plan(self, managed): return []
        def apply(self, changes): pass
        def managed_keys(self): return {}
        def finalize_apply(self): calls.append(self)

    a1 = _Finalizing(config=[], context=None)
    a2 = _Finalizing(config=[], context=None)
    plan = Plan()
    plan.add(Change("packages", Op.INSTALL, "git"))
    results = [
        ActionPlanResult(action=a1, changes=[]),
        ActionPlanResult(action=a2, changes=[]),
    ]
    r.apply(plan, results, assume_yes=True)
    assert calls == [a1, a2]


def test_apply_tolerates_an_action_double_with_no_finalize_apply():
    """Not every action double in the suite subclasses AbstractAction --
    finalize_apply() must be duck-typed (getattr + callable), never assumed,
    or every plain test double in the reconciler suite breaks."""
    store = MagicMock()
    gen = MagicMock()
    r = _make_reconciler(store=store, gen_store=gen)
    a = _RecordingV3(config=[], context=None)
    plan = Plan()
    plan.add(Change("packages", Op.INSTALL, "git"))
    results = [ActionPlanResult(action=a, changes=[])]
    r.apply(plan, results, assume_yes=True)   # must not raise


def test_apply_never_calls_finalize_apply_when_the_plan_is_empty():
    store = MagicMock()
    gen = MagicMock()
    r = _make_reconciler(store=store, gen_store=gen)
    finalize = MagicMock()

    class _Finalizing(AbstractAction):
        @property
        def name(self) -> str: return "fin"
        def is_needed(self) -> bool: return False
        def execute(self) -> None: pass
        def plan(self, managed): return []
        def apply(self, changes): pass
        def managed_keys(self): return {}
        def finalize_apply(self): finalize()

    a = _Finalizing(config=[], context=None)
    new_manifest = r.apply(Plan(), [ActionPlanResult(action=a, changes=[])],
                           assume_yes=True)
    assert new_manifest is None
    finalize.assert_not_called()


def test_apply_never_calls_finalize_apply_on_the_action_that_raised_or_after_it():
    """A failed apply persists a partial manifest and re-raises (see
    test_partial_apply.py) -- finalize_apply() is for a SUCCESSFUL apply's
    own end-of-run cleanup, not a recovery hook, so neither the action that
    raised nor anything after it in the plan gets finalized."""
    store = MagicMock()
    gen = MagicMock()
    r = _make_reconciler(store=store, gen_store=gen)
    calls: list = []

    class _Boom(AbstractAction):
        @property
        def name(self) -> str: return "boom"
        def is_needed(self) -> bool: return False
        def execute(self) -> None: pass
        def plan(self, managed): return []
        def apply(self, changes): raise RuntimeError("boom")
        def managed_keys(self): return {}
        def finalize_apply(self): calls.append(self)

    class _Never(_Boom):
        def apply(self, changes): pass

    a1 = _Boom(config=[], context=None)
    a2 = _Never(config=[], context=None)
    plan = Plan()
    plan.add(Change("packages", Op.INSTALL, "git"))
    results = [
        ActionPlanResult(action=a1, changes=[]),
        ActionPlanResult(action=a2, changes=[]),
    ]
    with pytest.raises(RuntimeError):
        r.apply(plan, results, assume_yes=True)
    assert calls == []


def test_apply_destructive_plan_prompts_user_and_aborts_on_no():
    store = MagicMock()
    gen = MagicMock()
    r = _make_reconciler(store=store, gen_store=gen)
    a = _RecordingV3(config=[], context=None)
    plan = Plan()
    plan.add(Change("packages", Op.REMOVE, "vim"))
    results = [ActionPlanResult(action=a, changes=list(plan.changes))]

    answers = iter(["n"])
    new_manifest = r.apply(
        plan, results,
        assume_yes=False,
        input_fn=lambda _: next(answers),
    )
    # No persistence on abort
    store.save.assert_not_called()
    gen.new.assert_not_called()
    assert new_manifest is None


def test_apply_destructive_plan_proceeds_when_user_confirms():
    store = MagicMock()
    gen = MagicMock()
    gen.new.return_value = 3
    r = _make_reconciler(store=store, gen_store=gen)
    a = _RecordingV3(config=[], context=None)
    plan = Plan()
    plan.add(Change("packages", Op.REMOVE, "vim"))
    results = [ActionPlanResult(action=a, changes=list(plan.changes))]

    answers = iter(["y"])
    new_manifest = r.apply(
        plan, results,
        assume_yes=False,
        input_fn=lambda _: next(answers),
    )
    assert new_manifest is not None
    store.save.assert_called_once()
    gen.new.assert_called_once()


def test_apply_with_assume_yes_skips_prompt_even_for_destructive():
    store = MagicMock()
    gen = MagicMock()
    gen.new.return_value = 1
    r = _make_reconciler(store=store, gen_store=gen)
    a = _RecordingV3(config=[], context=None)
    plan = Plan()
    plan.add(Change("packages", Op.REMOVE, "vim"))
    results = [ActionPlanResult(action=a, changes=list(plan.changes))]

    sentinel = MagicMock(side_effect=AssertionError("prompt called"))
    new_manifest = r.apply(plan, results, assume_yes=True, input_fn=sentinel)
    assert new_manifest is not None
    sentinel.assert_not_called()


def test_apply_merges_managed_keys_into_new_manifest():
    store = MagicMock()
    gen = MagicMock()
    gen.new.return_value = 2
    r = _make_reconciler(
        config={"packages": ["git", "htop"]},
        manifest={"managed": {"packages": ["vim"]}, "generation": 1},
        store=store,
        gen_store=gen,
    )
    a = _RecordingV3(config=["git", "htop"], context=None)
    plan = Plan()
    plan.add(Change("packages", Op.INSTALL, "git"))
    results = [ActionPlanResult(action=a, changes=list(plan.changes))]

    new_manifest = r.apply(plan, results, assume_yes=True)
    assert new_manifest.managed == {"packages": ["git", "htop"]}
    assert new_manifest.generation == 2  # bumped from 1
    assert new_manifest.config_hash is not None
    assert new_manifest.applied_at is not None
    # Persisted: StateStore.save received the new Manifest;
    # GenerationStore.new received (config, manifest_dict).
    saved = store.save.call_args.args[0]
    assert saved.generation == 2
    gen_args = gen.new.call_args.args
    assert gen_args[0] == {"packages": ["git", "htop"]}  # config
    assert gen_args[1]["generation"] == 2  # manifest dict


def test_apply_generation_starts_at_one_when_manifest_is_none():
    store = MagicMock()
    gen = MagicMock()
    gen.new.return_value = 1
    r = _make_reconciler(
        config={"packages": ["git"]},
        manifest=None,
        store=store,
        gen_store=gen,
    )
    a = _RecordingV3(config=["git"], context=None)
    plan = Plan()
    plan.add(Change("packages", Op.INSTALL, "git"))
    results = [ActionPlanResult(action=a, changes=list(plan.changes))]

    new_manifest = r.apply(plan, results, assume_yes=True)
    assert new_manifest.generation == 1


def test_apply_without_stores_runs_actions_but_skips_persistence():
    """When state_store/generation_store are None (e.g., dry tests), the
    actions still run but persistence is skipped."""
    r = _make_reconciler(store=None, gen_store=None)
    a = _RecordingV3(config=["git"], context=None)
    plan = Plan()
    c = Change("packages", Op.INSTALL, "git")
    plan.add(c)
    results = [ActionPlanResult(action=a, changes=[c])]
    _RecordingV3.last_applied = []
    new_manifest = r.apply(plan, results, assume_yes=True)
    assert _RecordingV3.last_applied == [c]
    # Even without stores, the Manifest is returned so the caller can see
    # what was built. Persistence is what's skipped, not the return value.
    assert new_manifest is not None
    assert new_manifest.generation == 1


def test_a_prompt_nobody_can_answer_aborts_instead_of_crashing():
    """No terminal (a pipe, a cron job, a headless verification run) means the
    confirmation cannot be answered — `input()` raises EOFError. Nothing was
    applied either way, but the user got a traceback where the tool should have
    said what it decided and why."""
    store = MagicMock()
    gen = MagicMock()
    r = _make_reconciler(store=store, gen_store=gen)
    a = _RecordingV3(config=[], context=None)
    plan = Plan()
    plan.add(Change("packages", Op.REMOVE, "vim"))
    results = [ActionPlanResult(action=a, changes=list(plan.changes))]

    _RecordingV3.last_applied = []      # class-level record, shared per module

    def no_terminal(_prompt):
        raise EOFError

    assert r.apply(plan, results, assume_yes=False, input_fn=no_terminal) is None
    store.save.assert_not_called()
    gen.new.assert_not_called()
    assert a.last_applied == []


def test_ctrl_c_at_the_prompt_aborts_the_same_way():
    store = MagicMock()
    gen = MagicMock()
    r = _make_reconciler(store=store, gen_store=gen)
    a = _RecordingV3(config=[], context=None)
    plan = Plan()
    plan.add(Change("packages", Op.REMOVE, "vim"))
    results = [ActionPlanResult(action=a, changes=list(plan.changes))]

    _RecordingV3.last_applied = []

    def interrupted(_prompt):
        raise KeyboardInterrupt

    assert r.apply(plan, results, assume_yes=False, input_fn=interrupted) is None
    store.save.assert_not_called()
    assert a.last_applied == []

import pytest

from dasik.lib.actions.abstract_action import AbstractAction
from dasik.lib.state.change import Change, Op


class _LegacyAction(AbstractAction):
    """A pre-v3 action: only implements is_needed/execute."""

    @property
    def name(self) -> str:
        return "legacy"

    def is_needed(self) -> bool:
        return False

    def execute(self) -> None:
        pass


class _V3Action(AbstractAction):
    """A v3 action: overrides plan/apply/actual/import_state/managed_keys."""

    @property
    def name(self) -> str:
        return "v3"

    def is_needed(self) -> bool:
        return bool(self.plan(managed=set()))

    def execute(self) -> None:
        self.apply(self.plan(managed=set()))

    def actual(self):
        return {"git"}

    def plan(self, managed):
        return [Change("packages", Op.INSTALL, "git")]

    def apply(self, plan):
        self._applied = list(plan)

    def import_state(self):
        return {"packages": ["git"]}

    def managed_keys(self):
        return {"packages": ["git"]}


def test_legacy_action_default_plan_is_empty():
    a = _LegacyAction(config={})
    assert a.plan(managed=set()) == []


def test_legacy_action_default_apply_is_noop():
    a = _LegacyAction(config={})
    a.apply([])  # must not raise


def test_legacy_action_default_actual_is_empty_set():
    a = _LegacyAction(config={})
    assert a.actual() == set()


def test_legacy_action_default_import_state_is_empty_dict():
    a = _LegacyAction(config={})
    assert a.import_state() == {}


def test_legacy_action_default_managed_keys_is_empty_dict():
    a = _LegacyAction(config={})
    assert a.managed_keys() == {}


def test_legacy_action_is_v3_false():
    """Legacy actions don't override plan → is_v3 is False."""
    assert _LegacyAction.is_v3() is False


def test_v3_action_is_v3_true():
    """Overriding plan flips the discriminator."""
    assert _V3Action.is_v3() is True


def test_v3_action_plan_apply_round_trip():
    a = _V3Action(config={})
    plan = a.plan(managed=set())
    assert plan == [Change("packages", Op.INSTALL, "git")]
    a.apply(plan)
    assert a._applied == plan


def test_abstract_action_cannot_be_instantiated_directly():
    """name/is_needed/execute remain abstract — sanity check."""
    with pytest.raises(TypeError):
        AbstractAction(config={})  # type: ignore[abstract]


def test_action_overriding_only_apply_is_not_v3():
    """Overriding apply without plan does NOT flip the discriminator.

    The v3 opt-in is solely on ``plan`` identity. A subclass that overrides
    only ``apply`` is still treated as legacy by the Reconciler — apply is
    never reached without a non-empty plan first.
    """
    class _ApplyOnly(_LegacyAction):
        def apply(self, plan):
            pass

    assert _ApplyOnly.is_v3() is False


def test_v3_subclass_inheriting_plan_is_still_v3():
    """A concrete v3 subclass that does not re-override plan is still v3.

    Identity comparison works through MRO — ``_V3Child.plan is _V3Action.plan``
    which ``is not AbstractAction.plan``, so ``is_v3()`` returns True.
    """
    class _V3Child(_V3Action):
        pass  # inherits plan from _V3Action

    assert _V3Child.is_v3() is True

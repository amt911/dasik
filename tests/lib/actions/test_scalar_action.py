from dasik.lib.actions.scalar_action import ScalarV3Action
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Op


class _FakeScalar(ScalarV3Action):
    _DOMAIN = "thing"

    def __init__(self, desired, actual, context=None):
        super().__init__({}, context)
        self._d = desired
        self._a = actual
        self.set_calls = 0

    def _desired_value(self):
        return self._d

    def _actual_value(self):
        return self._a

    def _set_value(self):
        self.set_calls += 1

    def _import_fragment(self, value):
        return {"thing": value}

    @property
    def name(self):
        return "Fake Scalar"


def _ctx(root="/"):
    return ActionContext(target=Target(root=root))


def test_actual_wraps_value_in_set():
    assert _FakeScalar("x", "x").actual() == {"x"}


def test_actual_empty_when_no_value():
    assert _FakeScalar("x", None).actual() == set()


def test_plan_modify_when_desired_differs():
    changes = _FakeScalar("new", "old").plan(managed=[])
    assert [(c.op, c.item) for c in changes] == [(Op.MODIFY, "new")]


def test_plan_empty_when_equal():
    assert _FakeScalar("same", "same").plan(managed=[]) == []


def test_plan_empty_when_no_desired():
    assert _FakeScalar(None, "old").plan(managed=[]) == []


def test_apply_sets_value_when_changes_and_target():
    a = _FakeScalar("new", "old", _ctx("/"))
    a.apply([object()])
    assert a.set_calls == 1


def test_apply_noop_without_changes():
    a = _FakeScalar("new", "old", _ctx("/"))
    a.apply([])
    assert a.set_calls == 0


def test_apply_noop_without_target():
    a = _FakeScalar("new", "old", None)
    a.apply([object()])
    assert a.set_calls == 0


def test_managed_keys_lists_desired():
    assert _FakeScalar("x", None).managed_keys() == {"thing": ["x"]}
    assert _FakeScalar(None, None).managed_keys() == {"thing": []}


def test_import_state_uses_actual_then_desired():
    assert _FakeScalar("d", "a").import_state() == {"thing": "a"}
    assert _FakeScalar("d", None).import_state() == {"thing": "d"}
    assert _FakeScalar(None, None).import_state() == {}


def test_is_v3_true():
    assert _FakeScalar("x", "x").is_v3() is True


def test_empty_config_is_empty_dict():
    """Scalar domains are dict-shaped, so their bootstrap empty config is {}."""
    assert _FakeScalar.empty_config() == {}

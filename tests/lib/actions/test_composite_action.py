from dasik.lib.actions.composite_action import CompositeV3Action
from dasik.lib.state.change import Op


class _FakeComposite(CompositeV3Action):
    _DOMAIN = "thing"

    def __init__(self, desired, actual):
        super().__init__({}, None)
        self._d = desired
        self._a = actual
        self.set_calls = 0

    def _desired_state(self):
        return self._d

    def _actual_state(self):
        return self._a

    def _set_value(self):
        self.set_calls += 1

    def _import_fragment(self, value):
        return {"thing": self._actual_state()}

    @property
    def name(self):
        return "Fake Composite"


def test_plan_empty_when_states_equal():
    a = _FakeComposite({"x": 1, "y": 2}, {"x": 1, "y": 2})
    assert a.plan(managed=[]) == []


def test_plan_modify_lists_changed_keys():
    a = _FakeComposite({"x": 1, "y": 2}, {"x": 1, "y": 9})
    changes = a.plan(managed=[])
    assert [(c.op, c.item) for c in changes] == [(Op.MODIFY, "y")]


def test_plan_all_keys_when_actual_none():
    a = _FakeComposite({"x": 1, "y": 2}, None)
    changes = a.plan(managed=[])
    assert changes[0].op is Op.MODIFY
    assert changes[0].item == "x,y"


def test_actual_wraps_serialized_value():
    a = _FakeComposite({"x": 1}, {"x": 1})
    assert a.actual() == {'{"x": 1}'}


def test_actual_empty_when_state_none():
    a = _FakeComposite({"x": 1}, None)
    assert a.actual() == set()


def test_managed_keys_carries_serialized_desired():
    a = _FakeComposite({"b": 2, "a": 1}, None)
    assert a.managed_keys() == {"thing": ['{"a": 1, "b": 2}']}


def test_is_v3_true():
    assert _FakeComposite({"x": 1}, {"x": 1}).is_v3() is True


def test_empty_config_is_empty_dict():
    assert _FakeComposite.empty_config() == {}

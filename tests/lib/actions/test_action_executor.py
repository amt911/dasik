from dasik.lib.actions.abstract_action import AbstractAction
from dasik.lib.actions.action_executor import ActionExecutor, ActionResult
from dasik.lib.actions.action_registry import ActionRegistry


class _FakeAction(AbstractAction):
    """Configurable fake action for executor tests."""
    needed = True
    verifies = True
    raises = False
    executed = False

    @property
    def name(self):
        return "Fake"

    @property
    def is_optional(self):
        return True

    def is_needed(self):
        if self.raises:
            raise RuntimeError("boom")
        return self.needed

    def execute(self):
        type(self).executed = True

    def verify(self):
        return self.verifies


def _registry(action_class, config_key="fake", is_optional=True, required_fields=None):
    reg = ActionRegistry()
    reg.register(action_class, config_key, is_optional=is_optional,
                 required_fields=required_fields)
    return reg


def _make(cls, **attrs):
    cls.needed = attrs.get("needed", True)
    cls.verifies = attrs.get("verifies", True)
    cls.raises = attrs.get("raises", False)
    cls.executed = False
    return cls


def test_result_object_fields():
    r = ActionResult("X", "success", "msg")
    assert (r.name, r.status, r.message) == ("X", "success", "msg")


def test_action_runs_when_needed():
    _make(_FakeAction, needed=True)
    ex = ActionExecutor({"fake": {"a": 1}}, _registry(_FakeAction))
    assert ex.execute_all() is True
    assert _FakeAction.executed is True
    assert ex.results[0].status == "success"


def test_action_skipped_when_not_needed():
    _make(_FakeAction, needed=False)
    ex = ActionExecutor({"fake": {"a": 1}}, _registry(_FakeAction))
    ex.execute_all()
    assert _FakeAction.executed is False
    assert ex.results[0].status == "not_needed"


def test_optional_action_skipped_when_config_absent():
    _make(_FakeAction)
    ex = ActionExecutor({}, _registry(_FakeAction, is_optional=True))
    ex.execute_all()
    assert ex.results[0].status == "skipped"


def test_required_action_fails_when_config_absent():
    _make(_FakeAction)
    ex = ActionExecutor({}, _registry(_FakeAction, is_optional=False))
    assert ex.execute_all() is False
    assert ex.results[0].status == "failed"


def test_verification_failure_marks_failed():
    _make(_FakeAction, needed=True, verifies=False)
    ex = ActionExecutor({"fake": {"a": 1}}, _registry(_FakeAction))
    assert ex.execute_all() is False
    assert ex.results[0].status == "failed"


def test_exception_during_run_marks_failed():
    _make(_FakeAction, raises=True)
    ex = ActionExecutor({"fake": {"a": 1}}, _registry(_FakeAction))
    assert ex.execute_all() is False
    assert ex.results[0].status == "failed"


def test_get_partition_delegates_to_context():
    ex = ActionExecutor({}, _registry(_FakeAction))
    ex.context.set_partition("root", "/dev/sda2")
    assert ex.get_partition("root") == "/dev/sda2"
    assert ex.get_partition("missing") is None

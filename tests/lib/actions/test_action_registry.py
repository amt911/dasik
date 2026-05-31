from dasik.lib.actions.abstract_action import AbstractAction
from dasik.lib.actions.action_registry import ActionRegistry


class _Dummy(AbstractAction):
    @property
    def name(self): return "dummy"
    def is_needed(self): return False
    def execute(self): pass


def _meta(config_key, is_optional=True, required_fields=None, depends_on=None):
    reg = ActionRegistry()
    reg.register(_Dummy, config_key, is_optional=is_optional,
                 required_fields=required_fields, depends_on=depends_on)
    return reg, reg.get_all_actions()[0]


# -- normal (named) config keys ------------------------------------------- #

def test_named_key_present_is_valid():
    reg, meta = _meta("timezone", required_fields=["region"])
    assert reg.validate_config({"timezone": {"region": "Europe"}}, meta) == (True, None)


def test_named_key_absent_optional_is_invalid_but_skippable():
    reg, meta = _meta("timezone", is_optional=True)
    ok, msg = reg.validate_config({}, meta)
    assert ok is False and "Optional" in msg


def test_named_key_missing_required_field():
    reg, meta = _meta("timezone", required_fields=["region", "city"])
    ok, msg = reg.validate_config({"timezone": {"region": "Europe"}}, meta)
    assert ok is False and "city" in msg


# -- __root__ sentinel (issue #67) ---------------------------------------- #

def test_root_key_validates_against_root_config():
    reg, meta = _meta("__root__", is_optional=False, required_fields=["enable_microcode"])
    assert reg.validate_config({"enable_microcode": True}, meta) == (True, None)


def test_root_key_missing_required_field_at_root():
    reg, meta = _meta("__root__", is_optional=False, required_fields=["enable_microcode"])
    ok, msg = reg.validate_config({"other": 1}, meta)
    assert ok is False and "enable_microcode" in msg


def test_root_key_with_no_required_fields_is_valid():
    reg, meta = _meta("__root__", is_optional=True)
    assert reg.validate_config({"anything": 1}, meta) == (True, None)


def test_root_key_respects_dependencies():
    reg, meta = _meta("__root__", depends_on=["hostname"])
    ok, msg = reg.validate_config({"enable_microcode": True}, meta)
    assert ok is False and "hostname" in msg

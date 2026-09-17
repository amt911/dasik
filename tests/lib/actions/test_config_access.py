"""dasik.lib.actions.config_access.field — the shared dict-or-pydantic-model
field reader, extracted from five byte-identical private ``_field`` copies
(mcp_servers_action, ai_skills_action, uv_tools_action,
pacman_repositories_action, pacman_repos_state). See its module docstring."""
from dasik.lib.actions.config_access import field


def test_field_reads_dict_key_or_default():
    assert field({"a": 1}, "a", "default") == 1
    assert field({"a": 1}, "b", "default") == "default"


def test_field_reads_attribute_or_default():
    class _Obj:
        pass
    obj = _Obj()
    obj.a = 1
    assert field(obj, "a", "default") == 1
    assert field(obj, "b", "default") == "default"


def test_field_default_is_none_when_omitted():
    assert field({}, "missing") is None

    class _Obj:
        pass
    assert field(_Obj(), "missing") is None

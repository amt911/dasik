from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target


def test_default_target_is_none():
    """Legacy actions construct ActionContext() with no args — must still work."""
    ctx = ActionContext()
    assert ctx.target is None
    assert ctx.manifest is None


def test_can_set_target_at_construction():
    t = Target(root="/mnt")
    ctx = ActionContext(target=t)
    assert ctx.target is t


def test_can_set_manifest_at_construction():
    m = {"generation": 1, "managed": {"packages": ["git"]}}
    ctx = ActionContext(manifest=m)
    assert ctx.manifest == {"generation": 1, "managed": {"packages": ["git"]}}


def test_legacy_partition_api_preserved():
    """Existing call-sites use partition_map / set_partition / get_partition."""
    ctx = ActionContext()
    ctx.set_partition("root", "/dev/sda2")
    assert ctx.get_partition("root") == "/dev/sda2"
    assert ctx.get_all_partitions() == {"root": "/dev/sda2"}
    assert ctx.get_partition("missing") is None


def test_legacy_get_set_has_preserved():
    ctx = ActionContext()
    assert ctx.has("k") is False
    ctx.set("k", 42)
    assert ctx.has("k") is True
    assert ctx.get("k") == 42
    assert ctx.get("absent", "default") == "default"

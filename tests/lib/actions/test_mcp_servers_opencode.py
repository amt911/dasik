"""opencode as a fourth `mcp_servers` agent: the model accepts it and the
reader normalizes opencode's entries (`local`/`remote`) to the shared spec."""
import json

import pytest
from pydantic import ValidationError

from dasik.lib.actions.mcp_servers_state import _spec, opencode_mcp
from dasik.lib.models.mcp_servers_model import AGENTS, McpServersModel

STDIO = {"name": "s", "command": "uvx"}
HTTP = {"name": "h", "url": "https://x.example/mcp"}


def _home(tmp_path, mcp, name="opencode.json"):
    cfg = tmp_path / ".config/opencode"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / name).write_text(json.dumps({"mcp": mcp}))
    return str(tmp_path)


def test_opencode_is_a_known_agent():
    assert AGENTS == ("claude-code", "codex", "antigravity", "opencode")
    McpServersModel(entries=[{**STDIO, "agents": ["opencode"]}])


def test_headers_accepted_for_opencode():
    McpServersModel(entries=[{**HTTP, "headers": {"A": "b"},
                              "agents": ["opencode"]}])


def test_bearer_variable_still_codex_only():
    with pytest.raises(ValidationError):
        McpServersModel(entries=[{**HTTP, "bearer_token_env_var": "T",
                                  "agents": ["opencode"]}])


def test_reads_local_server(tmp_path):
    home = _home(tmp_path, {"inkscape_mcp": {
        "type": "local",
        "command": ["uvx", "--python", "3.13", "inkscape_mcp"],
        "environment": {"K": "v"}}})
    assert opencode_mcp(home)["inkscape_mcp"] == _spec(
        "uvx", ["--python", "3.13", "inkscape_mcp"], {"K": "v"}, None)


def test_reads_remote_server_with_headers(tmp_path):
    home = _home(tmp_path, {"r": {"type": "remote", "url": "https://x/mcp",
                                  "headers": {"A": "b"}}})
    assert opencode_mcp(home)["r"] == _spec(
        None, [], {}, "https://x/mcp", headers={"A": "b"})


def test_disabled_server_counts_as_registered(tmp_path):
    home = _home(tmp_path, {"d": {"type": "local", "command": ["x"],
                                  "enabled": False}})
    assert opencode_mcp(home)["d"] == _spec("x", [], {}, None)


def test_entry_without_command_or_url_is_skipped(tmp_path):
    home = _home(tmp_path, {"bad": {"type": "local"}, "bad2": "x",
                            "bad3": {"command": "notalist"}})
    assert opencode_mcp(home) == {}


def test_reads_nested_servers_shape(tmp_path):
    home = _home(tmp_path, {"servers": {"n": {"type": "local",
                                              "command": ["a", "b"]}}})
    assert opencode_mcp(home)["n"] == _spec("a", ["b"], {}, None)


# --- plan / apply / sync through McpServersAction (Task 3) ------------------ #

import os
from unittest.mock import MagicMock, patch

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.mcp_servers_action import McpServersAction
from dasik.lib.exceptions.exceptions import CommandExecutionError
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target

ITEM = "andres:opencode:inkscape_mcp"
ENTRY = {"name": "inkscape_mcp", "command": "uvx",
         "args": ["--python", "3.13", "inkscape_mcp"],
         "env": {"K": "v"}, "agents": ["opencode"]}
NESTED = {"type": "local",
          "command": ["uvx", "--python", "3.13", "inkscape_mcp"],
          "environment": {"K": "v"}}


def _uid():
    return os.getuid(), os.getgid()


def _act(tmp_path, entries=(ENTRY,), binary=True, **block):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    uid, gid = _uid()
    (tmp_path / "etc/passwd").write_text(
        f"andres:x:{uid}:{gid}::/home/andres:/bin/zsh\n")
    if binary:
        (tmp_path / "usr/bin").mkdir(parents=True, exist_ok=True)
        (tmp_path / "usr/bin/opencode").write_text("")
    (tmp_path / "home/andres").mkdir(parents=True, exist_ok=True)
    cfg = {"users": [{"username": "andres"}],
           "mcp_servers": dict(block, entries=list(entries))}
    return McpServersAction(cfg, ActionContext(target=Target(root=str(tmp_path))))


def _cfgdir(tmp_path):
    d = tmp_path / "home/andres/.config/opencode"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _put(tmp_path, data, name="opencode.json"):
    (_cfgdir(tmp_path) / name).write_text(json.dumps(data))


def _load(tmp_path, name="opencode.json"):
    return json.loads((tmp_path / "home/andres/.config/opencode" / name).read_text())


def _apply(action, changes):
    with patch("dasik.lib.actions.mcp_servers_action.Command.execute") as ex:
        try:
            action.apply(changes)
        finally:
            ex.assert_not_called()  # opencode never goes through a CLI
    return ex


def _kinds(changes):
    return [(c.op, c.item) for c in changes]


def test_missing_on_opencode_plans_a_create(tmp_path):
    assert _kinds(_act(tmp_path).plan([])) == [(Op.CREATE, ITEM)]


def test_registered_on_opencode_plans_nothing(tmp_path):
    _put(tmp_path, {"mcp": {"servers": {"inkscape_mcp": NESTED}}})
    assert _act(tmp_path).plan([]) == []


def test_registered_in_users_jsonc_flat_plans_nothing(tmp_path):
    _put(tmp_path, {"mcp": {"inkscape_mcp": NESTED}}, "opencode.jsonc")
    assert _act(tmp_path).plan([]) == []


def test_different_args_on_opencode_are_a_modify(tmp_path):
    _put(tmp_path, {"mcp": {"servers": {"inkscape_mcp": {
        **NESTED, "command": ["uvx", "other"]}}}})
    assert _kinds(_act(tmp_path).plan([])) == [(Op.MODIFY, ITEM)]


def test_owned_but_undeclared_on_opencode_is_deleted(tmp_path):
    _put(tmp_path, {"mcp": {"servers": {"inkscape_mcp": NESTED}}})
    action = _act(tmp_path, entries=[])
    changes = action.plan([ITEM])
    assert _kinds(changes) == [(Op.DELETE, ITEM)]


def test_an_unowned_opencode_server_is_left_alone(tmp_path):
    _put(tmp_path, {"mcp": {"servers": {"inkscape_mcp": NESTED}}})
    assert _act(tmp_path, entries=[]).plan([]) == []


def test_create_writes_nested_entry_into_opencode_json(tmp_path):
    _apply(_act(tmp_path), [Change("mcp_servers", Op.CREATE, ITEM)])
    data = _load(tmp_path)
    assert data["mcp"]["servers"]["inkscape_mcp"] == NESTED
    assert data["$schema"]


def test_create_omits_empty_environment_and_writes_remote(tmp_path):
    entries = [{"name": "a", "command": "x", "agents": ["opencode"]},
               {"name": "r", "url": "https://x/mcp", "agents": ["opencode"]},
               {"name": "h", "url": "https://y/mcp", "headers": {"A": "b"},
                "agents": ["opencode"]}]
    _apply(_act(tmp_path, entries),
           [Change("mcp_servers", Op.CREATE, f"andres:opencode:{n}")
            for n in "arh"])
    servers = _load(tmp_path)["mcp"]["servers"]
    assert servers["a"] == {"type": "local", "command": ["x"]}
    assert servers["r"] == {"type": "remote", "url": "https://x/mcp"}
    assert servers["h"] == {"type": "remote", "url": "https://y/mcp",
                            "headers": {"A": "b"}}


def test_create_leaves_users_jsonc_byte_identical(tmp_path):
    text = '// mine\n{"mcp": {"other": {"type": "local", "command": ["z"]}},}\n'
    (_cfgdir(tmp_path) / "opencode.jsonc").write_text(text)
    _apply(_act(tmp_path), [Change("mcp_servers", Op.CREATE, ITEM)])
    assert (_cfgdir(tmp_path) / "opencode.jsonc").read_text() == text


def test_create_never_runs_a_cli(tmp_path):
    ex = _apply(_act(tmp_path), [Change("mcp_servers", Op.CREATE, ITEM)])
    ex.assert_not_called()


def test_delete_removes_from_json_nested_and_cleans_empty_parents(tmp_path):
    _put(tmp_path, {"$schema": "s", "mcp": {"servers": {"inkscape_mcp": NESTED}}})
    _apply(_act(tmp_path, entries=[]), [Change("mcp_servers", Op.DELETE, ITEM)])
    assert _load(tmp_path) == {"$schema": "s"}


def test_delete_removes_from_jsonc_flat_keeping_siblings(tmp_path):
    _put(tmp_path, {"theme": "x", "mcp": {"inkscape_mcp": NESTED,
                                          "keep": {"type": "local",
                                                   "command": ["k"]}}},
         "opencode.jsonc")
    _apply(_act(tmp_path, entries=[]), [Change("mcp_servers", Op.DELETE, ITEM)])
    data = _load(tmp_path, "opencode.jsonc")
    assert data == {"theme": "x", "mcp": {"keep": {"type": "local",
                                                    "command": ["k"]}}}
    assert not (tmp_path / "home/andres/.config/opencode/opencode.json").exists()


def test_modify_of_a_jsonc_server_moves_it_to_json(tmp_path):
    _put(tmp_path, {"mcp": {"inkscape_mcp": {"type": "local",
                                             "command": ["old"]}}},
         "opencode.jsonc")
    _apply(_act(tmp_path), [Change("mcp_servers", Op.MODIFY, ITEM)])
    assert _load(tmp_path)["mcp"]["servers"]["inkscape_mcp"] == NESTED
    assert "mcp" not in _load(tmp_path, "opencode.jsonc")


def test_modify_of_a_flat_json_server_becomes_nested(tmp_path):
    _put(tmp_path, {"mcp": {"inkscape_mcp": {"type": "local",
                                             "command": ["old"]}}})
    _apply(_act(tmp_path), [Change("mcp_servers", Op.MODIFY, ITEM)])
    assert _load(tmp_path)["mcp"] == {"servers": {"inkscape_mcp": NESTED}}


@pytest.mark.parametrize("policy", ["warn-and-continue", "abort"])
def test_broken_json_fails_the_item_and_keeps_the_file(tmp_path, policy, capsys):
    (_cfgdir(tmp_path) / "opencode.json").write_text("{ not json")
    action = _act(tmp_path, failure_policy=policy)
    change = Change("mcp_servers", Op.CREATE, ITEM)
    if policy == "abort":
        with pytest.raises(CommandExecutionError):
            _apply(action, [change])
    else:
        _apply(action, [change])
        assert action.failed_items == [ITEM]
        assert ITEM not in action.managed_keys()["mcp_servers"]
    assert (_cfgdir(tmp_path) / "opencode.json").read_text() == "{ not json"


def test_symlinked_config_dir_fails_the_item(tmp_path):
    real = tmp_path / "elsewhere"
    real.mkdir()
    (tmp_path / "home/andres/.config").mkdir(parents=True)
    os.symlink(real, tmp_path / "home/andres/.config/opencode")
    action = _act(tmp_path)
    _apply(action, [Change("mcp_servers", Op.CREATE, ITEM)])
    assert action.failed_items == [ITEM]
    assert list(real.iterdir()) == []


def test_no_opencode_binary_fails_the_item(tmp_path):
    action = _act(tmp_path, binary=False)
    _apply(action, [Change("mcp_servers", Op.CREATE, ITEM)])
    assert action.failed_items == [ITEM]
    assert not (tmp_path / "home/andres/.config/opencode").exists()


def test_sync_captures_opencode_with_other_agents_in_one_entry(tmp_path):
    (tmp_path / "home/andres").mkdir(parents=True, exist_ok=True)
    (tmp_path / "home/andres/.claude.json").write_text(json.dumps({
        "mcpServers": {"inkscape_mcp": {
            "type": "stdio", "command": "uvx",
            "args": ["--python", "3.13", "inkscape_mcp"], "env": {"K": "v"}}}}))
    _put(tmp_path, {"mcp": {"servers": {"inkscape_mcp": NESTED}}})
    block = _act(tmp_path).import_state()["mcp_servers"]
    assert [e["agents"] for e in block["entries"]] == [["claude-code", "opencode"]]


def test_captured_block_validates_and_replans_to_nothing(tmp_path):
    _put(tmp_path, {"mcp": {"servers": {"inkscape_mcp": NESTED}}})
    block = _act(tmp_path).import_state()["mcp_servers"]
    McpServersModel(**block)
    again = _act(tmp_path, entries=block["entries"])
    assert again.plan([]) == []

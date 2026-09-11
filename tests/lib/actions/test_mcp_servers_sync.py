"""`sync` reads the machine's MCP registrations back as a declaration.

The invariant that matters is the last test: sync -> check -> plan must end in
silence. A capture the tool then refuses, or one that re-plans a change, is a
one-way street — the feature disappears the moment a config is rebuilt from the
machine.
"""
import json

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.mcp_servers_action import McpServersAction
from dasik.lib.models.mcp_servers_model import McpServersModel
from dasik.lib.target.target import Target

STDIO_CLAUDE = {"inkscape_mcp": {"type": "stdio", "command": "uvx",
                                 "args": ["inkscape_mcp"], "env": {}}}
STDIO_CODEX = ('[mcp_servers.inkscape_mcp]\ncommand = "uvx"\n'
               'args = ["inkscape_mcp"]\n')


def _root(tmp_path, users=("andres",), claude=None, codex=None, per_user=None):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    lines = ["root:x:0:0::/root:/bin/bash\n",
             "nobody:x:65534:65534::/:/usr/bin/nologin\n"]
    for index, user in enumerate(users):
        uid = 1000 + index
        lines.append(f"{user}:x:{uid}:{uid}::/home/{user}:/bin/zsh\n")
    (tmp_path / "etc/passwd").write_text("".join(lines))
    for user in users:
        home = tmp_path / f"home/{user}"
        home.mkdir(parents=True, exist_ok=True)
        servers = (per_user or {}).get(user, claude)
        if servers is not None:
            (home / ".claude.json").write_text(json.dumps({"mcpServers": servers}))
        if codex is not None:
            (home / ".codex").mkdir(exist_ok=True)
            (home / ".codex/config.toml").write_text(codex)
    return tmp_path


def _sync(tmp_path, config=None, **kwargs):
    action = McpServersAction(config or {}, ActionContext(
        target=Target(root=str(_root(tmp_path, **kwargs)))))
    return action.import_state()["mcp_servers"]


def test_a_server_on_both_agents_is_captured_once_with_both_agents(tmp_path):
    assert _sync(tmp_path, claude=STDIO_CLAUDE, codex=STDIO_CODEX) == {
        "users": ["andres"],
        "entries": [{"name": "inkscape_mcp", "command": "uvx",
                     "args": ["inkscape_mcp"],
                     "agents": ["claude-code", "codex"]}]}


def test_a_machine_with_no_servers_invents_nothing(tmp_path):
    assert _sync(tmp_path) == {}


def test_env_is_captured_verbatim(tmp_path):
    block = _sync(tmp_path, claude={"s": {"command": "x", "env": {"K": "v"}}})
    assert block["entries"][0]["env"] == {"K": "v"}


def test_an_empty_env_is_not_written_back(tmp_path):
    """The smallest config that reproduces the machine — and `env: {}` would
    read as a declaration somebody made."""
    block = _sync(tmp_path, claude={"s": {"command": "x", "env": {}}})
    assert "env" not in block["entries"][0]
    assert "args" not in block["entries"][0]


def test_an_http_server_is_captured_as_a_url(tmp_path):
    block = _sync(tmp_path, claude={"s": {"type": "http",
                                          "url": "https://e/mcp"}})
    assert block["entries"][0] == {"name": "s", "url": "https://e/mcp",
                                   "agents": ["claude-code"]}


def test_a_server_only_one_user_has_carries_its_own_users(tmp_path):
    """Without the per-entry list the capture would report the union, and the
    next plan would register each server for everybody."""
    block = _sync(tmp_path, users=("andres", "otro"),
                  per_user={"andres": {"mine": {"command": "x"}},
                            "otro": {"theirs": {"command": "y"}}})
    assert block["users"] == ["andres", "otro"]
    assert {e["name"]: e["users"] for e in block["entries"]} == {
        "mine": ["andres"], "theirs": ["otro"]}


def test_only_the_users_who_own_something_are_declared(tmp_path):
    """A user with no MCP server at all is not part of the domain: sync
    reports what is there, and naming them would widen the boundary."""
    block = _sync(tmp_path, users=("andres", "otro"),
                  per_user={"andres": {"s": {"command": "x"}}, "otro": {}})
    assert block["users"] == ["andres"]
    assert "users" not in block["entries"][0]


def test_a_server_everybody_has_does_not_repeat_the_user_list(tmp_path):
    block = _sync(tmp_path, users=("andres", "otro"),
                  claude={"s": {"command": "x"}})
    assert block["users"] == ["andres", "otro"]
    assert "users" not in block["entries"][0]


def test_system_accounts_are_never_read(tmp_path):
    """A bootstrap sync starts from {}: it reads the machine's humans, and a
    service account's home is service state, not somebody's tools."""
    root = _root(tmp_path, claude={"s": {"command": "x"}})
    (root / "root").mkdir(exist_ok=True)
    (root / "root/.claude.json").write_text(
        json.dumps({"mcpServers": {"roots": {"command": "y"}}}))
    action = McpServersAction({}, ActionContext(target=Target(root=str(root))))
    names = [e["name"] for e in action.import_state()["mcp_servers"]["entries"]]
    assert names == ["s"]


def test_two_servers_with_different_commands_stay_two_entries(tmp_path):
    block = _sync(tmp_path, claude={"a": {"command": "x"},
                                    "b": {"command": "y", "args": ["z"]}})
    assert [e["name"] for e in block["entries"]] == ["a", "b"]


def test_a_declared_failure_policy_is_carried_over(tmp_path):
    """A machine cannot report a policy; losing it on every sync would silently
    turn `abort` back into `warn-and-continue`."""
    block = _sync(tmp_path, config={"mcp_servers": {"failure_policy": "abort"}},
                  claude={"s": {"command": "x"}})
    assert block["failure_policy"] == "abort"


def test_no_target_captures_nothing(tmp_path):
    assert McpServersAction({}, None).import_state() == {"mcp_servers": {}}


def test_the_captured_block_validates_and_replans_to_nothing(tmp_path):
    kwargs = dict(claude=STDIO_CLAUDE, codex=STDIO_CODEX)
    captured = _sync(tmp_path, **kwargs)
    McpServersModel(**captured)                       # `check` accepts it
    root = _root(tmp_path, **kwargs)
    action = McpServersAction({"users": [{"username": "andres"}],
                               "mcp_servers": captured},
                              ActionContext(target=Target(root=str(root))))
    assert action.plan(managed=[]) == []              # sync -> plan is silent


def test_a_captured_http_server_also_replans_to_nothing(tmp_path):
    kwargs = dict(claude={"s": {"type": "http", "url": "https://e/mcp"}})
    captured = _sync(tmp_path, **kwargs)
    McpServersModel(**captured)
    action = McpServersAction({"users": [{"username": "andres"}],
                               "mcp_servers": captured},
                              ActionContext(target=Target(root=str(
                                  _root(tmp_path, **kwargs)))))
    assert action.plan(managed=[]) == []

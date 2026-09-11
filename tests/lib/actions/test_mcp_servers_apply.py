"""`apply` drives each agent's own CLI — asserted as argv, never executed.

Two properties are pinned here and both came from real bugs in this repo:
`claude mcp add` defaults to the LOCAL (per-directory) scope, so `-s user` is
what makes the registration belong to the machine; and every value travels as a
positional `$N` so a name or an env value can never reach the shell as code.
"""
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.mcp_servers_action import McpServersAction
from dasik.lib.exceptions.exceptions import CommandExecutionError
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target


def _action(tmp_path, entries, **block):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/passwd").write_text(
        "andres:x:1000:1000::/home/andres:/bin/zsh\n")
    cfg = {"users": [{"username": "andres"}],
           "mcp_servers": dict(block, entries=entries)}
    return McpServersAction(cfg, ActionContext(target=Target(root=str(tmp_path))))


def _calls(action, changes, returncode=0):
    with patch("dasik.lib.actions.mcp_servers_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=returncode, stdout="",
                                         stderr="boom" if returncode else "")
        action.apply(changes)
    return [call.args for call in execute.call_args_list]


def _create(user_agent_name):
    return Change("mcp_servers", Op.CREATE, user_agent_name)


def test_claude_stdio_add_is_user_scoped_and_passes_values_as_argv(tmp_path):
    action = _action(tmp_path, [{"name": "inkscape_mcp", "command": "uvx",
                                 "args": ["inkscape_mcp"],
                                 "agents": ["claude-code"]}])
    (binary, argv), = _calls(action, [_create("andres:claude-code:inkscape_mcp")])
    assert binary == "su"
    assert argv[:3] == ["-", "andres", "-c"]
    script = argv[3]
    assert "claude mcp add" in script
    assert "-s user" in script
    assert argv[4:] == ["--", "sh", "inkscape_mcp", "uvx", "inkscape_mcp"]


def test_codex_stdio_add(tmp_path):
    action = _action(tmp_path, [{"name": "inkscape_mcp", "command": "uvx",
                                 "args": ["inkscape_mcp"], "agents": ["codex"]}])
    (_, argv), = _calls(action, [_create("andres:codex:inkscape_mcp")])
    assert "codex mcp add" in argv[3]
    assert argv[4:] == ["--", "sh", "inkscape_mcp", "uvx", "inkscape_mcp"]


def test_env_is_passed_as_argv_never_interpolated(tmp_path):
    action = _action(tmp_path, [{"name": "s", "command": "x",
                                 "env": {"K": "v v"}, "agents": ["claude-code"]}])
    (_, argv), = _calls(action, [_create("andres:claude-code:s")])
    assert "K=v v" in argv[4:]
    assert "v v" not in argv[3]


def test_codex_env_uses_its_own_flag(tmp_path):
    action = _action(tmp_path, [{"name": "s", "command": "x",
                                 "env": {"K": "v"}, "agents": ["codex"]}])
    (_, argv), = _calls(action, [_create("andres:codex:s")])
    assert "--env" in argv[3]
    assert "K=v" in argv[4:]


def test_claude_http_add(tmp_path):
    action = _action(tmp_path, [{"name": "s", "url": "https://e/mcp",
                                 "headers": {"X": "y"},
                                 "agents": ["claude-code"]}])
    (_, argv), = _calls(action, [_create("andres:claude-code:s")])
    assert "--transport http" in argv[3]
    assert "-H" in argv[3]
    assert argv[4:] == ["--", "sh", "s", "https://e/mcp", "X: y"]


def test_codex_http_add(tmp_path):
    action = _action(tmp_path, [{"name": "s", "url": "https://e/mcp",
                                 "bearer_token_env_var": "TOKEN",
                                 "agents": ["codex"]}])
    (_, argv), = _calls(action, [_create("andres:codex:s")])
    assert "--url" in argv[3]
    assert "--bearer-token-env-var" in argv[3]
    assert argv[4:] == ["--", "sh", "s", "https://e/mcp", "TOKEN"]


def test_delete_uses_the_agents_remove_verb_even_when_undeclared(tmp_path):
    """A DELETE is not in the config any more: user, agent and name are all the
    removal needs, and they are in the item itself."""
    action = _action(tmp_path, [])
    calls = _calls(action, [Change("mcp_servers", Op.DELETE,
                                   "andres:claude-code:ghost"),
                            Change("mcp_servers", Op.DELETE,
                                   "andres:codex:ghost")])
    assert "claude mcp remove" in calls[0][1][3]
    assert "-s user" in calls[0][1][3]
    assert calls[0][1][4:] == ["--", "sh", "ghost"]
    assert "codex mcp remove" in calls[1][1][3]
    assert calls[1][1][4:] == ["--", "sh", "ghost"]


def test_modify_removes_then_adds(tmp_path):
    """`mcp add` on a name that already exists does not rewrite it."""
    action = _action(tmp_path, [{"name": "s", "command": "uvx",
                                 "agents": ["codex"]}])
    calls = _calls(action, [Change("mcp_servers", Op.MODIFY, "andres:codex:s")])
    assert "codex mcp remove" in calls[0][1][3]
    assert "codex mcp add" in calls[1][1][3]


def test_a_failed_registration_is_disowned_under_warn_and_continue(tmp_path):
    action = _action(tmp_path, [{"name": "s", "command": "uvx",
                                 "agents": ["codex"]}])
    _calls(action, [_create("andres:codex:s")], returncode=1)
    assert action.failed_items == ["andres:codex:s"]
    assert action.managed_keys() == {"mcp_servers": []}


def test_a_successful_registration_is_owned(tmp_path):
    action = _action(tmp_path, [{"name": "s", "command": "uvx",
                                 "agents": ["codex"]}])
    _calls(action, [_create("andres:codex:s")])
    assert action.managed_keys() == {"mcp_servers": ["andres:codex:s"]}


def test_a_failed_removal_stops_the_second_command_of_a_modify(tmp_path):
    action = _action(tmp_path, [{"name": "s", "command": "uvx",
                                 "agents": ["codex"]}])
    calls = _calls(action, [Change("mcp_servers", Op.MODIFY, "andres:codex:s")],
                   returncode=1)
    assert len(calls) == 1


def test_abort_policy_raises(tmp_path):
    action = _action(tmp_path, [{"name": "s", "command": "uvx",
                                 "agents": ["codex"]}],
                     failure_policy="abort")
    with pytest.raises(CommandExecutionError):
        _calls(action, [_create("andres:codex:s")], returncode=1)


def test_no_target_applies_nothing(tmp_path):
    action = McpServersAction({"mcp_servers": {"entries": []}}, None)
    with patch("dasik.lib.actions.mcp_servers_action.Command.execute") as execute:
        action.apply([_create("andres:codex:s")])
    execute.assert_not_called()

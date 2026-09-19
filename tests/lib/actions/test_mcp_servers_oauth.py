"""`codex mcp add --url` must not hang an install on an OAuth login nobody can do.

Measured in a guest and on the author's machine (codex-cli 0.154.0, Figma's
https://mcp.figma.com/mcp): `codex mcp add` discovers OAuth support, WRITES the
registration to ~/.codex/config.toml (0.9 s), and only then starts a browser
login that waits 300 s for a callback and exits non-zero when it never comes.
An unattended install has no browser, so every OAuth server stalled the install
for five minutes and was reported as a failed registration that was, in fact,
on disk. The source (codex-rs/cli/src/mcp_cmd.rs, `run_add`) has no switch to
skip that login.

So a codex http registration is bounded by `timeout`, and whether it happened is
decided the way `plan` decides it: by reading the registration back. The login
is the user's (`codex mcp login <name>`), like any other credential.
"""
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.mcp_servers_action import McpServersAction
from dasik.lib.exceptions.exceptions import CommandExecutionError
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target

_HTTP = {"name": "figma", "url": "https://mcp.figma.com/mcp", "agents": ["codex"]}
_ITEM = "andres:codex:figma"


def _action(tmp_path, entries, **block):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/passwd").write_text(
        "andres:x:1000:1000::/home/andres:/bin/zsh\n")
    cfg = {"users": [{"username": "andres"}],
           "mcp_servers": dict(block, entries=entries)}
    return McpServersAction(cfg, ActionContext(target=Target(root=str(tmp_path))))


def _codex_config(tmp_path, body):
    path = tmp_path / "home/andres/.codex/config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def _apply(action, returncode, op=Op.CREATE):
    with patch("dasik.lib.actions.mcp_servers_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=returncode, stdout="",
                                         stderr="timed out waiting for OAuth callback")
        action.apply([Change("mcp_servers", op, _ITEM)])
    return [call.args for call in execute.call_args_list]


def test_a_codex_http_add_is_bounded_by_timeout(tmp_path):
    (_, argv), = _apply(_action(tmp_path, [_HTTP]), returncode=0)
    assert argv[3].startswith('timeout 60 codex mcp add "$1" --url "$2"')


@pytest.mark.parametrize("entry", [
    {"name": "figma", "url": "https://mcp.figma.com/mcp", "agents": ["claude-code"]},
    {"name": "figma", "command": "npx", "args": ["x"], "agents": ["codex"]},
])
def test_other_adds_are_not_wrapped(tmp_path, entry):
    item = f"andres:{entry['agents'][0]}:figma"
    with patch("dasik.lib.actions.mcp_servers_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0, stdout="", stderr="")
        _action(tmp_path, [entry]).apply([Change("mcp_servers", Op.CREATE, item)])
    (_, argv), = [call.args for call in execute.call_args_list]
    assert "timeout" not in argv[3]


def test_registered_but_login_timed_out_counts_as_registered(tmp_path, capsys):
    action = _action(tmp_path, [_HTTP])
    _codex_config(tmp_path, '[mcp_servers.figma]\nurl = "https://mcp.figma.com/mcp"\n')
    _apply(action, returncode=124)
    assert action.failed_items == []
    assert action.managed_keys() == {"mcp_servers": [_ITEM]}
    out = capsys.readouterr().out
    assert "codex mcp login figma" in out


def test_registered_but_login_timed_out_does_not_abort(tmp_path):
    action = _action(tmp_path, [_HTTP], failure_policy="abort")
    _codex_config(tmp_path, '[mcp_servers.figma]\nurl = "https://mcp.figma.com/mcp"\n')
    _apply(action, returncode=124)          # must not raise
    assert action.failed_items == []


def test_nothing_registered_is_still_a_failure(tmp_path):
    action = _action(tmp_path, [_HTTP])
    _apply(action, returncode=124)
    assert action.failed_items == [_ITEM]
    assert action.managed_keys() == {"mcp_servers": []}


def test_nothing_registered_aborts_under_abort(tmp_path):
    action = _action(tmp_path, [_HTTP], failure_policy="abort")
    with pytest.raises(CommandExecutionError):
        _apply(action, returncode=124)


def test_a_different_registration_is_still_a_failure(tmp_path):
    action = _action(tmp_path, [_HTTP])
    _codex_config(tmp_path, '[mcp_servers.figma]\nurl = "https://other.example/mcp"\n')
    _apply(action, returncode=124)
    assert action.failed_items == [_ITEM]


def test_a_failed_codex_stdio_add_is_not_rescued_by_what_is_on_disk(tmp_path):
    """Only the http add has a login after its write. A stdio add that exits
    non-zero failed for some other reason, and a registration left over from an
    earlier run proves nothing about this one."""
    action = _action(tmp_path, [{"name": "figma", "command": "npx", "args": ["x"],
                                 "agents": ["codex"]}])
    _codex_config(tmp_path, '[mcp_servers.figma]\ncommand = "npx"\nargs = ["x"]\n')
    _apply(action, returncode=1)
    assert action.failed_items == [_ITEM]


def test_a_modify_readds_through_the_same_bounded_path(tmp_path):
    """MODIFY is remove + add: the add half gets the same bound and read-back."""
    action = _action(tmp_path, [_HTTP])
    _codex_config(tmp_path, '[mcp_servers.figma]\nurl = "https://mcp.figma.com/mcp"\n')
    removed = MagicMock(returncode=0, stdout="", stderr="")
    timed_out = MagicMock(returncode=124, stdout="", stderr="")
    with patch("dasik.lib.actions.mcp_servers_action.Command.execute",
               side_effect=[removed, timed_out]) as execute:
        action.apply([Change("mcp_servers", Op.MODIFY, _ITEM)])
    scripts = [call.args[1][3] for call in execute.call_args_list]
    assert scripts[0].startswith("codex mcp remove")
    assert scripts[1].startswith("timeout 60 codex mcp add")
    assert action.failed_items == []


def test_the_wait_is_announced_before_codex_prints_its_login_url(tmp_path, capsys):
    """codex prints an OAuth URL that reads as "sign in to continue"; the user
    must already know, when it appears, that the install moves on by itself."""
    seen_before_run = []

    def execute(*_args, **_kwargs):
        seen_before_run.append(capsys.readouterr().out)
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("dasik.lib.actions.mcp_servers_action.Command.execute",
               side_effect=execute):
        _action(tmp_path, [_HTTP]).apply([Change("mcp_servers", Op.CREATE, _ITEM)])
    notice, = seen_before_run
    assert "figma" in notice
    assert "60 s" in notice
    assert "codex mcp login figma" in notice


@pytest.mark.parametrize("entry", [
    {"name": "figma", "url": "https://mcp.figma.com/mcp", "agents": ["claude-code"]},
    {"name": "figma", "command": "npx", "args": ["x"], "agents": ["codex"]},
])
def test_other_adds_announce_no_wait(tmp_path, capsys, entry):
    item = f"andres:{entry['agents'][0]}:figma"
    with patch("dasik.lib.actions.mcp_servers_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0, stdout="", stderr="")
        _action(tmp_path, [entry]).apply([Change("mcp_servers", Op.CREATE, item)])
    assert "60 s" not in capsys.readouterr().out

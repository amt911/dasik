"""Antigravity as a third `mcp_servers` agent, driven through `agy mcp`.

Measured on antigravity-cli 1.2.5 under a scratch HOME (docs/FACTS.md
FACT-AGY-1..3): `agy mcp add` writes ONLY `~/.gemini/config/mcp_config.json`
(`{"mcpServers": {name: {"command","args","env","disabled"} |
{"serverUrl","headers","disabled"}}}`), every flag must precede the name, a
re-add of an existing name replaces the whole registration, `agy mcp remove`
of a missing name exits 1, and `agy mcp disable` keeps the entry with
`"disabled": true`. It never blocks on an OAuth login.
"""
import json
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.mcp_servers_action import McpServersAction
from dasik.lib.actions.mcp_servers_state import antigravity_mcp
from dasik.lib.models.mcp_servers_model import McpServersModel
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target


def _agy_home(home, servers):
    config = home / ".gemini/config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "mcp_config.json").write_text(json.dumps({"mcpServers": servers}))


def _root(tmp_path, agy=None):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/passwd").write_text(
        "root:x:0:0::/root:/bin/bash\nandres:x:1000:1000::/home/andres:/bin/zsh\n")
    home = tmp_path / "home/andres"
    home.mkdir(parents=True, exist_ok=True)
    if agy is not None:
        _agy_home(home, agy)
    return tmp_path


def _action(tmp_path, entries=None, agy=None, block=None):
    cfg = {"users": [{"username": "andres"}]}
    if entries is not None:
        cfg["mcp_servers"] = dict(block or {}, entries=entries)
    return McpServersAction(cfg, ActionContext(
        target=Target(root=str(_root(tmp_path, agy)))))


STDIO = {"name": "chrome-devtools", "command": "npx",
         "args": ["-y", "chrome-devtools-mcp@latest", "--executablePath",
                  "/usr/bin/chromium"],
         "agents": ["antigravity"]}
STDIO_REGISTERED = {"chrome-devtools": {
    "command": "npx", "disabled": False,
    "args": ["-y", "chrome-devtools-mcp@latest", "--executablePath",
             "/usr/bin/chromium"]}}
HTTP = {"name": "figma", "url": "https://mcp.figma.com/mcp",
        "agents": ["antigravity"]}
HTTP_REGISTERED = {"figma": {"serverUrl": "https://mcp.figma.com/mcp",
                             "disabled": False}}


# -- model ------------------------------------------------------------------ #

def test_antigravity_is_a_known_agent():
    entry = McpServersModel(entries=[STDIO]).entries[0]
    assert entry.agents == ["antigravity"]


def test_headers_are_accepted_for_antigravity_and_claude_together():
    """`agy mcp add -H` exists, like `claude mcp add -H`: one entry for both."""
    entry = McpServersModel(entries=[{
        "name": "x", "url": "https://e/mcp", "headers": {"X": "y"},
        "agents": ["claude-code", "antigravity"]}]).entries[0]
    assert entry.headers == {"X": "y"}


def test_headers_still_refuse_codex():
    with pytest.raises(ValidationError, match="headers"):
        McpServersModel(entries=[{"name": "x", "url": "https://e/mcp",
                                  "headers": {"X": "y"},
                                  "agents": ["antigravity", "codex"]}])


def test_bearer_variable_is_still_codex_only():
    with pytest.raises(ValidationError, match="bearer_token_env_var"):
        McpServersModel(entries=[{"name": "x", "url": "https://e/mcp",
                                  "bearer_token_env_var": "TOKEN",
                                  "agents": ["antigravity"]}])


# -- reader ----------------------------------------------------------------- #

def test_reads_a_stdio_server_with_env(tmp_path):
    _agy_home(tmp_path, {"s": {"command": "npx", "args": ["-y", "pkg"],
                               "env": {"K": "v"}, "disabled": False}})
    assert antigravity_mcp(str(tmp_path)) == {"s": {
        "transport": "stdio", "command": "npx", "args": ["-y", "pkg"],
        "env": {"K": "v"}, "url": None, "headers": {},
        "bearer_token_env_var": None}}


def test_reads_an_http_server_from_server_url(tmp_path):
    _agy_home(tmp_path, {"h": {"serverUrl": "https://e/mcp",
                               "headers": {"X-A": "1"}, "disabled": False}})
    assert antigravity_mcp(str(tmp_path)) == {"h": {
        "transport": "http", "command": None, "args": [], "env": {},
        "url": "https://e/mcp", "headers": {"X-A": "1"},
        "bearer_token_env_var": None}}


def test_a_disabled_server_is_not_registered(tmp_path):
    """A server the user switched off is not one the agent can use; reporting
    it as present would keep plan silent about a declaration nobody honours."""
    _agy_home(tmp_path, {"h": {"serverUrl": "https://e/mcp", "disabled": True}})
    assert antigravity_mcp(str(tmp_path)) == {}


def test_a_missing_empty_or_broken_file_is_nothing(tmp_path):
    assert antigravity_mcp(str(tmp_path)) == {}
    config = tmp_path / ".gemini/config"
    config.mkdir(parents=True)
    (config / "mcp_config.json").write_text("")        # what the host really had
    assert antigravity_mcp(str(tmp_path)) == {}
    (config / "mcp_config.json").write_text("{not json")
    assert antigravity_mcp(str(tmp_path)) == {}
    (config / "mcp_config.json").write_text(json.dumps({"mcpServers": {"x": 3}}))
    assert antigravity_mcp(str(tmp_path)) == {}


# -- plan ------------------------------------------------------------------- #

def _plan(action, managed=()):
    return [(c.op.name, c.item) for c in action.plan(managed=list(managed))]


def test_missing_on_antigravity_plans_a_create(tmp_path):
    assert _plan(_action(tmp_path, [STDIO])) == [
        ("CREATE", "andres:antigravity:chrome-devtools")]


def test_registered_on_antigravity_plans_nothing(tmp_path):
    assert _plan(_action(tmp_path, [STDIO], agy=STDIO_REGISTERED)) == []
    assert _plan(_action(tmp_path / "h", [HTTP], agy=HTTP_REGISTERED)) == []


def test_a_disabled_registration_plans_a_create(tmp_path):
    agy = {"figma": dict(HTTP_REGISTERED["figma"], disabled=True)}
    assert _plan(_action(tmp_path, [HTTP], agy=agy)) == [
        ("CREATE", "andres:antigravity:figma")]


def test_different_args_on_antigravity_are_a_modify(tmp_path):
    agy = {"chrome-devtools": dict(STDIO_REGISTERED["chrome-devtools"],
                                   args=["-y", "chrome-devtools-mcp@latest"])}
    assert _plan(_action(tmp_path, [STDIO], agy=agy)) == [
        ("MODIFY", "andres:antigravity:chrome-devtools")]


def test_owned_but_undeclared_on_antigravity_is_deleted(tmp_path):
    action = _action(tmp_path, [], agy=STDIO_REGISTERED)
    assert _plan(action, managed=["andres:antigravity:chrome-devtools"]) == [
        ("DELETE", "andres:antigravity:chrome-devtools")]


def test_an_unowned_antigravity_server_is_left_alone(tmp_path):
    assert _plan(_action(tmp_path, [], agy=STDIO_REGISTERED)) == []


# -- apply ------------------------------------------------------------------ #

def _calls(action, changes, returncode=0):
    with patch("dasik.lib.actions.mcp_servers_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=returncode, stdout="",
                                         stderr="boom" if returncode else "")
        action.apply(changes)
    return [call.args for call in execute.call_args_list]


def test_stdio_add_puts_flags_before_the_name_and_values_in_argv(tmp_path):
    entry = dict(STDIO, env={"K": "v v"})
    (binary, argv), = _calls(_action(tmp_path, [entry]), [
        Change("mcp_servers", Op.CREATE, "andres:antigravity:chrome-devtools")])
    assert binary == "su"
    script = argv[3]
    assert script == 'agy mcp add --env "$1" "$2" -- "$3" "$4" "$5" "$6" "$7"'
    assert argv[4:] == ["--", "sh", "K=v v", "chrome-devtools", "npx", "-y",
                        "chrome-devtools-mcp@latest", "--executablePath",
                        "/usr/bin/chromium"]


def test_http_add_passes_headers_before_the_name(tmp_path):
    entry = dict(HTTP, headers={"X-B": "two words", "X-A": "1"})
    (_, argv), = _calls(_action(tmp_path, [entry]), [
        Change("mcp_servers", Op.CREATE, "andres:antigravity:figma")])
    assert argv[3] == 'agy mcp add -H "$1" -H "$2" "$3" "$4"'
    assert argv[4:] == ["--", "sh", "X-A: 1", "X-B: two words", "figma",
                        "https://mcp.figma.com/mcp"]
    assert "timeout" not in argv[3]      # agy never blocks on an OAuth login


def test_delete_uses_agy_mcp_remove_even_when_undeclared(tmp_path):
    (_, argv), = _calls(_action(tmp_path, []), [
        Change("mcp_servers", Op.DELETE, "andres:antigravity:figma")])
    assert argv[3] == 'agy mcp remove "$1"'
    assert argv[4:] == ["--", "sh", "figma"]


def test_modify_re_adds_without_removing_first(tmp_path):
    """`agy mcp add` REPLACES an existing name (measured), unlike claude/codex:
    a remove first would only open a window with no server at all."""
    calls = _calls(_action(tmp_path, [STDIO]), [
        Change("mcp_servers", Op.MODIFY, "andres:antigravity:chrome-devtools")])
    assert [argv[3].split(" ")[:3] for _b, argv in calls] == [["agy", "mcp", "add"]]


# -- sync ------------------------------------------------------------------- #

def test_sync_captures_antigravity_registrations_as_their_own_agent(tmp_path):
    action = McpServersAction({}, ActionContext(target=Target(root=str(
        _root(tmp_path, agy=dict(STDIO_REGISTERED, **HTTP_REGISTERED))))))
    block = action.import_state()["mcp_servers"]
    assert block == {"users": ["andres"], "entries": [
        {"name": "chrome-devtools", "command": "npx",
         "args": ["-y", "chrome-devtools-mcp@latest", "--executablePath",
                  "/usr/bin/chromium"],
         "agents": ["antigravity"]},
        {"name": "figma", "url": "https://mcp.figma.com/mcp",
         "agents": ["antigravity"]}]}


def test_a_captured_antigravity_block_validates_and_replans_to_nothing(tmp_path):
    agy = dict(STDIO_REGISTERED, **HTTP_REGISTERED)
    root = _root(tmp_path, agy=agy)
    captured = McpServersAction({}, ActionContext(target=Target(root=str(root)))
                                ).import_state()["mcp_servers"]
    McpServersModel(**captured)
    action = McpServersAction({"users": [{"username": "andres"}],
                               "mcp_servers": captured},
                              ActionContext(target=Target(root=str(root))))
    assert action.plan(managed=[]) == []

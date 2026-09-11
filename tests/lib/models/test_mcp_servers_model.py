"""The `mcp_servers` block: what a declaration may and may not say.

Every rule here exists so a config cannot promise something `apply` would not
do. The CLIs are not symmetric — only Claude Code takes `--header`, only codex
takes `--bearer-token-env-var` — and a block that named one for both agents
would plan a registration that silently arrives without it.
"""
import pytest
from pydantic import ValidationError

from dasik.lib.models.mcp_servers_model import McpServersModel


def _model(**entry):
    return McpServersModel(entries=[{"name": "x", "agents": ["claude-code"],
                                     **entry}])


def test_a_stdio_entry_is_accepted():
    model = _model(command="uvx", args=["inkscape_mcp"])
    assert model.entries[0].command == "uvx"
    assert model.entries[0].transport == "stdio"


def test_an_http_entry_is_accepted():
    assert _model(url="https://e/mcp").entries[0].transport == "http"


def test_neither_command_nor_url_is_rejected():
    with pytest.raises(ValidationError, match="exactly one of `command`"):
        _model()


def test_both_command_and_url_is_rejected():
    with pytest.raises(ValidationError, match="exactly one of `command`"):
        _model(command="uvx", url="https://e/mcp")


def test_args_or_env_on_an_http_entry_is_rejected():
    with pytest.raises(ValidationError, match="`args`"):
        _model(url="https://e/mcp", args=["x"])
    with pytest.raises(ValidationError, match="`env`"):
        _model(url="https://e/mcp", env={"K": "v"})


def test_headers_on_a_stdio_entry_is_rejected():
    with pytest.raises(ValidationError, match="`headers`"):
        _model(command="uvx", headers={"X": "y"})


def test_bearer_token_env_var_on_a_stdio_entry_is_rejected():
    with pytest.raises(ValidationError, match="`bearer_token_env_var`"):
        _model(command="uvx", bearer_token_env_var="TOKEN")


def test_headers_require_claude_alone():
    """codex has no --header: a mixed entry would promise what apply cannot do."""
    with pytest.raises(ValidationError, match="headers"):
        McpServersModel(entries=[{"name": "x", "url": "https://e/mcp",
                                  "agents": ["claude-code", "codex"],
                                  "headers": {"X": "y"}}])


def test_bearer_token_env_var_requires_codex_alone():
    with pytest.raises(ValidationError, match="bearer_token_env_var"):
        McpServersModel(entries=[{"name": "x", "url": "https://e/mcp",
                                  "agents": ["claude-code"],
                                  "bearer_token_env_var": "TOKEN"}])


def test_headers_for_claude_alone_are_accepted():
    entry = McpServersModel(entries=[{"name": "x", "url": "https://e/mcp",
                                      "agents": ["claude-code"],
                                      "headers": {"X": "y"}}]).entries[0]
    assert entry.headers == {"X": "y"}


def test_an_unknown_agent_is_rejected():
    with pytest.raises(ValidationError, match="claude-code"):
        McpServersModel(entries=[{"name": "x", "command": "uvx",
                                  "agents": ["opencode"]}])


def test_agents_cannot_be_empty_or_duplicated():
    with pytest.raises(ValidationError, match="at least one agent"):
        McpServersModel(entries=[{"name": "x", "command": "uvx", "agents": []}])
    with pytest.raises(ValidationError, match="duplicate"):
        McpServersModel(entries=[{"name": "x", "command": "uvx",
                                  "agents": ["codex", "codex"]}])


def test_duplicate_users_are_rejected_on_the_block_and_on_an_entry():
    with pytest.raises(ValidationError, match="duplicate"):
        McpServersModel(users=["a", "a"], entries=[])
    with pytest.raises(ValidationError, match="duplicate"):
        McpServersModel(entries=[{"name": "x", "command": "uvx",
                                  "agents": ["codex"], "users": ["a", "a"]}])


def test_two_entries_may_not_declare_the_same_server_for_the_same_agent():
    """One name, one agent, one registration: the second would silently win."""
    with pytest.raises(ValidationError, match="declared twice"):
        McpServersModel(entries=[
            {"name": "x", "command": "uvx", "agents": ["codex"]},
            {"name": "x", "command": "npx", "agents": ["codex"]}])


def test_the_same_name_on_different_agents_is_fine():
    model = McpServersModel(entries=[
        {"name": "x", "url": "https://e/mcp", "agents": ["claude-code"],
         "headers": {"X": "y"}},
        {"name": "x", "url": "https://e/mcp", "agents": ["codex"],
         "bearer_token_env_var": "TOKEN"}])
    assert len(model.entries) == 2


def test_an_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        McpServersModel(entries=[{"name": "x", "command": "uvx",
                                  "agents": ["codex"], "typo": 1}])


def test_failure_policy_defaults_to_warn_and_continue():
    assert McpServersModel(entries=[]).failure_policy == "warn-and-continue"


def test_the_block_is_optional_on_json_model():
    from dasik.lib.models.json_model import JsonModel
    assert JsonModel(hostname="h").mcp_servers is None


def test_json_model_accepts_the_block():
    from dasik.lib.models.json_model import JsonModel
    model = JsonModel(hostname="h", mcp_servers={"entries": [
        {"name": "inkscape_mcp", "command": "uvx", "args": ["inkscape_mcp"],
         "agents": ["claude-code", "codex"]}]})
    assert model.mcp_servers is not None
    assert model.mcp_servers.entries[0].agents == ["claude-code", "codex"]

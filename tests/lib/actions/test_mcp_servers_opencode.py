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

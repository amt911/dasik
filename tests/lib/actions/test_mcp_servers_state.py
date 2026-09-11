"""What each agent says about the MCP servers it is registered against.

Both readers normalize to ONE shape, because `plan` compares a claude
registration with a codex one against the same declaration: a difference in
spelling between the two files would otherwise read as drift and MODIFY
forever.
"""
import json

from dasik.lib.actions.mcp_servers_state import claude_mcp, codex_mcp


def _home(tmp_path, claude=None, codex=None):
    if claude is not None:
        (tmp_path / ".claude.json").write_text(json.dumps(claude))
    if codex is not None:
        (tmp_path / ".codex").mkdir(exist_ok=True)
        (tmp_path / ".codex/config.toml").write_text(codex)
    return str(tmp_path)


def test_claude_reads_a_stdio_server(tmp_path):
    home = _home(tmp_path, claude={"mcpServers": {"inkscape_mcp": {
        "type": "stdio", "command": "uvx", "args": ["inkscape_mcp"], "env": {}}}})
    assert claude_mcp(home) == {"inkscape_mcp": {
        "transport": "stdio", "command": "uvx", "args": ["inkscape_mcp"],
        "env": {}, "url": None}}


def test_claude_ignores_project_scoped_servers(tmp_path):
    """`projects.<path>.mcpServers` belongs to a repository, not to the machine.

    dasik declares machines. Capturing a project's servers would put somebody's
    working directory in the config, and planning them would register them
    machine-wide.
    """
    home = _home(tmp_path, claude={"projects": {"/home/andres/repos/x": {
        "mcpServers": {"local_only": {"command": "foo"}}}}})
    assert claude_mcp(home) == {}


def test_claude_reads_an_http_server(tmp_path):
    home = _home(tmp_path, claude={"mcpServers": {"sentry": {
        "type": "http", "url": "https://mcp.sentry.dev/mcp"}}})
    assert claude_mcp(home) == {"sentry": {
        "transport": "http", "command": None, "args": [], "env": {},
        "url": "https://mcp.sentry.dev/mcp"}}


def test_a_url_makes_it_http_even_without_a_type(tmp_path):
    home = _home(tmp_path, claude={"mcpServers": {"s": {"url": "https://e/mcp"}}})
    assert claude_mcp(home)["s"]["transport"] == "http"


def test_a_missing_or_broken_file_is_nothing_registered(tmp_path):
    assert claude_mcp(str(tmp_path)) == {}
    assert codex_mcp(str(tmp_path)) == {}
    (tmp_path / ".claude.json").write_text("{not json")
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex/config.toml").write_text("[mcp_servers.x\n")
    assert claude_mcp(str(tmp_path)) == {}
    assert codex_mcp(str(tmp_path)) == {}


def test_a_non_dict_entry_is_skipped_not_crashed(tmp_path):
    home = _home(tmp_path, claude={"mcpServers": {"bad": "nonsense",
                                                  "good": {"command": "x"}}})
    assert list(claude_mcp(home)) == ["good"]


def test_codex_reads_a_stdio_server(tmp_path):
    home = _home(tmp_path, codex='[mcp_servers.inkscape_mcp]\n'
                                 'command = "uvx"\nargs = ["inkscape_mcp"]\n')
    assert codex_mcp(home) == {"inkscape_mcp": {
        "transport": "stdio", "command": "uvx", "args": ["inkscape_mcp"],
        "env": {}, "url": None}}


def test_codex_reads_env_and_url(tmp_path):
    home = _home(tmp_path, codex='[mcp_servers.a]\ncommand = "x"\n'
                                 'env = { K = "v" }\n\n'
                                 '[mcp_servers.b]\nurl = "https://e/mcp"\n')
    state = codex_mcp(home)
    assert state["a"]["env"] == {"K": "v"}
    assert state["b"]["transport"] == "http"
    assert state["b"]["url"] == "https://e/mcp"


def test_codex_ignores_every_other_section(tmp_path):
    """The file is codex's own state: projects, hooks, model. Only one table
    describes MCP servers."""
    home = _home(tmp_path, codex='model = "gpt-5.6-sol"\n'
                                 '[projects."/home/andres"]\ntrust_level = "trusted"\n'
                                 '[mcp_servers.only]\ncommand = "x"\n'
                                 '[hooks.state]\n')
    assert list(codex_mcp(home)) == ["only"]

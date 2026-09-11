"""`plan` for the mcp_servers domain: one item per (user, agent, server).

The two directions that matter are both asserted for every shape: missing on
the target produces a change, present produces none. A domain that is silent in
both cases is indistinguishable from one dasik ignores.
"""
import json

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.mcp_servers_action import McpServersAction
from dasik.lib.target.target import Target

CFG = {"users": [{"username": "andres"}],
       "mcp_servers": {"entries": [{"name": "inkscape_mcp", "command": "uvx",
                                    "args": ["inkscape_mcp"],
                                    "agents": ["claude-code", "codex"]}]}}

REGISTERED_CLAUDE = {"inkscape_mcp": {"type": "stdio", "command": "uvx",
                                      "args": ["inkscape_mcp"]}}
REGISTERED_CODEX = ('[mcp_servers.inkscape_mcp]\ncommand = "uvx"\n'
                    'args = ["inkscape_mcp"]\n')


def _root(tmp_path, claude=None, codex=None, users=("andres",)):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    lines = ["root:x:0:0::/root:/bin/bash\n"]
    for index, user in enumerate(users):
        uid = 1000 + index
        lines.append(f"{user}:x:{uid}:{uid}::/home/{user}:/bin/zsh\n")
    (tmp_path / "etc/passwd").write_text("".join(lines))
    for user in users:
        home = tmp_path / f"home/{user}"
        home.mkdir(parents=True, exist_ok=True)
        if claude is not None:
            (home / ".claude.json").write_text(json.dumps({"mcpServers": claude}))
        if codex is not None:
            (home / ".codex").mkdir(exist_ok=True)
            (home / ".codex/config.toml").write_text(codex)
    return tmp_path


def _plan(tmp_path, config=CFG, managed=(), **kwargs):
    action = McpServersAction(config, ActionContext(
        target=Target(root=str(_root(tmp_path, **kwargs)))))
    return [(c.op.name, c.item) for c in action.plan(managed=list(managed))]


def test_absent_on_both_agents_plans_both(tmp_path):
    assert _plan(tmp_path) == [("CREATE", "andres:claude-code:inkscape_mcp"),
                               ("CREATE", "andres:codex:inkscape_mcp")]


def test_registered_on_both_agents_plans_nothing(tmp_path):
    assert _plan(tmp_path, claude=REGISTERED_CLAUDE,
                 codex=REGISTERED_CODEX) == []


def test_registered_on_one_agent_only_plans_the_other(tmp_path):
    assert _plan(tmp_path, claude=REGISTERED_CLAUDE, codex="") == [
        ("CREATE", "andres:codex:inkscape_mcp")]


def test_a_different_command_is_a_modify(tmp_path):
    assert _plan(tmp_path,
                 claude={"inkscape_mcp": {"type": "stdio", "command": "npx",
                                          "args": ["inkscape_mcp"]}},
                 codex=REGISTERED_CODEX) == [
        ("MODIFY", "andres:claude-code:inkscape_mcp")]


def test_different_args_are_a_modify(tmp_path):
    assert _plan(tmp_path,
                 claude={"inkscape_mcp": {"command": "uvx", "args": ["other"]}},
                 codex=REGISTERED_CODEX) == [
        ("MODIFY", "andres:claude-code:inkscape_mcp")]


def test_absent_env_and_empty_env_are_the_same_registration(tmp_path):
    """Without this the plan MODIFYs forever: `claude mcp add` writes `env: {}`
    and the config never said `env` at all — apply reports success every time
    and the next plan asks for the same change."""
    assert _plan(tmp_path,
                 claude={"inkscape_mcp": {"type": "stdio", "command": "uvx",
                                          "args": ["inkscape_mcp"], "env": {}}},
                 codex=REGISTERED_CODEX + "env = {}\n") == []


def test_a_declared_env_that_the_machine_lacks_is_a_modify(tmp_path):
    config = {"users": [{"username": "andres"}], "mcp_servers": {"entries": [
        {"name": "s", "command": "x", "env": {"K": "v"},
         "agents": ["claude-code"]}]}}
    assert _plan(tmp_path, config, claude={"s": {"command": "x"}}) == [
        ("MODIFY", "andres:claude-code:s")]


def test_a_transport_change_is_a_modify(tmp_path):
    config = {"users": [{"username": "andres"}], "mcp_servers": {"entries": [
        {"name": "s", "url": "https://e/mcp", "agents": ["claude-code"]}]}}
    assert _plan(tmp_path, config, claude={"s": {"command": "uvx"}}) == [
        ("MODIFY", "andres:claude-code:s")]


def test_an_http_server_already_registered_plans_nothing(tmp_path):
    config = {"users": [{"username": "andres"}], "mcp_servers": {"entries": [
        {"name": "s", "url": "https://e/mcp", "agents": ["claude-code"]}]}}
    assert _plan(tmp_path, config,
                 claude={"s": {"type": "http", "url": "https://e/mcp"}}) == []


def test_owned_but_no_longer_declared_is_deleted(tmp_path):
    assert _plan(tmp_path, {"users": [{"username": "andres"}]},
                 managed=["andres:claude-code:inkscape_mcp"],
                 claude={"inkscape_mcp": {"command": "uvx"}}) == [
        ("DELETE", "andres:claude-code:inkscape_mcp")]


def test_a_server_nobody_declared_or_owns_is_left_alone(tmp_path):
    """Drift is reported, never removed: somebody registered it by hand."""
    claude = dict(REGISTERED_CLAUDE, somebody_elses={"command": "x"})
    assert _plan(tmp_path, claude=claude, codex=REGISTERED_CODEX) == []


def test_an_entry_can_narrow_but_not_widen_the_users(tmp_path):
    config = {"users": [{"username": "andres"}, {"username": "otro"}],
              "mcp_servers": {"users": ["andres"], "entries": [
                  {"name": "x", "command": "uvx", "agents": ["codex"],
                   "users": ["otro"]}]}}
    assert _plan(tmp_path, config, users=("andres", "otro")) == []


def test_the_block_users_default_to_the_declared_humans(tmp_path):
    config = {"users": [{"username": "andres"}, {"username": "otro"}],
              "mcp_servers": {"entries": [
                  {"name": "x", "command": "uvx", "agents": ["codex"]}]}}
    assert _plan(tmp_path, config, users=("andres", "otro")) == [
        ("CREATE", "andres:codex:x"), ("CREATE", "otro:codex:x")]


def test_a_user_the_machine_does_not_have_yet_still_plans(tmp_path):
    """The whole plan is computed before UsersAction has created anybody."""
    config = {"users": [{"username": "nuevo"}], "mcp_servers": {"entries": [
        {"name": "x", "command": "uvx", "agents": ["codex"]}]}}
    assert _plan(tmp_path, config) == [("CREATE", "nuevo:codex:x")]


def test_no_target_plans_nothing(tmp_path):
    assert McpServersAction(CFG, None).plan(managed=[]) == []


def test_verify_is_true_once_converged(tmp_path):
    action = McpServersAction(CFG, ActionContext(target=Target(root=str(
        _root(tmp_path, claude=REGISTERED_CLAUDE, codex=REGISTERED_CODEX)))))
    assert action.verify() is True


def test_actual_reports_what_is_registered(tmp_path):
    action = McpServersAction(CFG, ActionContext(target=Target(root=str(
        _root(tmp_path, claude=REGISTERED_CLAUDE, codex=REGISTERED_CODEX)))))
    assert action.actual() == {"andres:claude-code:inkscape_mcp",
                               "andres:codex:inkscape_mcp"}

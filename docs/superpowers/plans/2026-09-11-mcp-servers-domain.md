# `mcp_servers` Domain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Declare MCP servers in the config — per user, per agent — and have dasik register, remove and capture them through each agent's own CLI.

**Architecture:** A v3 domain shaped exactly like `ai_skills`: item `<user>:<agent>:<name>`, state read from the agents' own files (`~/.claude.json` root `mcpServers`, `~/.codex/config.toml` `[mcp_servers.*]`), changes applied by driving `claude mcp add|remove` / `codex mcp add|remove` as the user via `su - <user> -c '…' -- sh <args>`. dasik never writes either registry file: both are the programs' mutable state.

**Tech Stack:** Python ≥3.10, pydantic v2, pytest (+hypothesis available), `Command.execute` for shelling out, QEMU harness `scripts/vmtest/qemu.sh`.

**Spec:** [docs/superpowers/specs/2026-09-11-mcp-servers-design.md](../specs/2026-09-11-mcp-servers-design.md)

## Global Constraints

- Python floor `>=3.10` — `tomllib` is 3.11+, so TOML reading MUST go through the tomllib-with-fallback helper, never a bare `import tomllib`.
- Runtime dependencies stay `pydantic` + `colorama`. No new dependency.
- Agents supported: `claude-code`, `codex`. Any other id is a model error.
- Claude Code's MCP scope is ALWAYS `-s user` (its default is `local`, i.e. per-directory).
- `env` values are captured verbatim by `sync` (WireGuard precedent); a captured config with `env` is private.
- Never run `execute()`/`apply` against the host: unit tests mock `Command.execute`; the VM guest is the only place real verbs run.
- TDD is mandatory for every new decision function (model validators, `plan`, `import_state`).
- Coverage gate 80%; mypy and bandit clean; `scripts/mutation.sh` (set_math tier) clean before push.

---

### Task 1: Shared TOML reader + MCP state readers

**Files:**
- Create: `dasik/lib/actions/toml_reader.py`
- Modify: `dasik/lib/actions/ai_skills_state.py` (delete its private `_load_toml`, import the shared one)
- Create: `dasik/lib/actions/mcp_servers_state.py`
- Test: `tests/lib/actions/test_mcp_servers_state.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `toml_reader.load_toml(text: str) -> dict` — parsed TOML, `{}` on anything unparseable.
  - `mcp_servers_state.claude_mcp(home: str) -> dict[str, dict]`
  - `mcp_servers_state.codex_mcp(home: str) -> dict[str, dict]`
  - Both return `{name: spec}` where `spec` is the normalized shape
    `{"transport": "stdio"|"http", "command": str|None, "args": list[str], "env": dict[str,str], "url": str|None}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/lib/actions/test_mcp_servers_state.py
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
    """`projects.<path>.mcpServers` belongs to a repository, not to the machine."""
    home = _home(tmp_path, claude={"projects": {"/home/andres/repos/x": {
        "mcpServers": {"local_only": {"command": "foo"}}}}})
    assert claude_mcp(home) == {}


def test_claude_reads_an_http_server(tmp_path):
    home = _home(tmp_path, claude={"mcpServers": {"sentry": {
        "type": "http", "url": "https://mcp.sentry.dev/mcp"}}})
    assert claude_mcp(home)["sentry"]["transport"] == "http"
    assert claude_mcp(home)["sentry"]["url"] == "https://mcp.sentry.dev/mcp"


def test_a_missing_or_broken_file_is_nothing_registered(tmp_path):
    assert claude_mcp(str(tmp_path)) == {}
    (tmp_path / ".claude.json").write_text("{not json")
    assert claude_mcp(str(tmp_path)) == {}
    assert codex_mcp(str(tmp_path)) == {}


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


def test_codex_ignores_other_sections(tmp_path):
    home = _home(tmp_path, codex='[projects."/home/andres"]\ntrust_level = "trusted"\n'
                                 '[mcp_servers.only]\ncommand = "x"\n')
    assert list(codex_mcp(home)) == ["only"]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/lib/actions/test_mcp_servers_state.py -v`
Expected: FAIL — `ModuleNotFoundError: dasik.lib.actions.mcp_servers_state`.

- [ ] **Step 3: Extract the TOML helper**

Move the body of `ai_skills_state._load_toml` into `dasik/lib/actions/toml_reader.py` as `load_toml(text: str) -> dict`, keeping its docstring (tomllib when available, hand parser otherwise, `{}` on anything it cannot parse). In `ai_skills_state.py` delete the private copy and `from .toml_reader import load_toml`, updating its call sites. The hand parser must keep handling `key = ["a", "b"]` and `key = { K = "v" }` values, because that is what `args`/`env` look like on 3.10.

- [ ] **Step 4: Write `mcp_servers_state.py`**

Both readers normalize to the same dict shape so `plan` compares like with like:
`transport` is `"http"` when the entry has a `url` and `"stdio"` otherwise;
missing `args` is `[]`, missing `env` is `{}`. A non-dict entry is skipped.

- [ ] **Step 5: Run the tests, plus the ai_skills state suite that shares the helper**

Run: `pytest tests/lib/actions/test_mcp_servers_state.py tests/lib/actions/test_ai_skills_state.py -v`
Expected: PASS — both. The second one is the migration's proof.

- [ ] **Step 6: Commit**

```bash
git add dasik/lib/actions/toml_reader.py dasik/lib/actions/mcp_servers_state.py \
        dasik/lib/actions/ai_skills_state.py tests/lib/actions/test_mcp_servers_state.py
git commit -m "feat(mcp): read what claude and codex say about their MCP servers"
```

---

### Task 2: The model

**Files:**
- Create: `dasik/lib/models/mcp_servers_model.py`
- Modify: `dasik/lib/models/json_model.py` (import + `mcp_servers: Optional[McpServersModel] = None`, next to `ai_skills`)
- Test: `tests/lib/models/test_mcp_servers_model.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `McpServersModel(users: list[str], failure_policy: Literal["warn-and-continue","abort"], entries: list[McpServerEntry])`, `McpServerEntry(name, agents, command, args, env, url, headers, bearer_token_env_var, users)`, and `AGENTS = ("claude-code", "codex")`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/lib/models/test_mcp_servers_model.py
import pytest
from pydantic import ValidationError
from dasik.lib.models.mcp_servers_model import McpServersModel


def _model(**entry):
    return McpServersModel(entries=[{"name": "x", "agents": ["claude-code"], **entry}])


def test_a_stdio_entry_is_accepted():
    model = _model(command="uvx", args=["inkscape_mcp"])
    assert model.entries[0].command == "uvx"
    assert model.entries[0].transport == "stdio"


def test_an_http_entry_is_accepted():
    assert _model(url="https://e/mcp").entries[0].transport == "http"


def test_neither_command_nor_url_is_rejected():
    with pytest.raises(ValidationError, match="exactly one of `command` or `url`"):
        _model()


def test_both_command_and_url_is_rejected():
    with pytest.raises(ValidationError, match="exactly one of `command` or `url`"):
        _model(command="uvx", url="https://e/mcp")


def test_args_or_env_on_an_http_entry_is_rejected():
    with pytest.raises(ValidationError, match="`args`"):
        _model(url="https://e/mcp", args=["x"])
    with pytest.raises(ValidationError, match="`env`"):
        _model(url="https://e/mcp", env={"K": "v"})


def test_headers_on_a_stdio_entry_is_rejected():
    with pytest.raises(ValidationError, match="`headers`"):
        _model(command="uvx", headers={"X": "y"})


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


def test_an_unknown_agent_is_rejected():
    with pytest.raises(ValidationError, match="claude-code"):
        McpServersModel(entries=[{"name": "x", "command": "uvx",
                                  "agents": ["opencode"]}])


def test_agents_cannot_be_empty_or_duplicated():
    with pytest.raises(ValidationError):
        McpServersModel(entries=[{"name": "x", "command": "uvx", "agents": []}])
    with pytest.raises(ValidationError, match="duplicate"):
        McpServersModel(entries=[{"name": "x", "command": "uvx",
                                  "agents": ["codex", "codex"]}])


def test_an_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        McpServersModel(entries=[{"name": "x", "command": "uvx",
                                  "agents": ["codex"], "typo": 1}])


def test_the_block_is_optional_on_json_model():
    from dasik.lib.models.json_model import JsonModel
    assert JsonModel(hostname="h").mcp_servers is None
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/lib/models/test_mcp_servers_model.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write the model**

`ConfigDict(extra="forbid")` on both classes. `transport` is a property (`"http"` when `url` else `"stdio"`), not a field — it is derived, and a field would let a config contradict itself. Validators exactly as the tests demand, each raising with the field name in the message so `dasik check` tells the user which key is wrong.

- [ ] **Step 4: Wire it into `JsonModel` and re-run**

Run: `pytest tests/lib/models/test_mcp_servers_model.py tests/lib/models -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/models/mcp_servers_model.py dasik/lib/models/json_model.py \
        tests/lib/models/test_mcp_servers_model.py
git commit -m "feat(mcp): the mcp_servers config block"
```

---

### Task 3: `plan` — desired vs actual

**Files:**
- Create: `dasik/lib/actions/mcp_servers_action.py`
- Test: `tests/lib/actions/test_mcp_servers_plan.py`

**Interfaces:**
- Consumes: `mcp_servers_state.claude_mcp/codex_mcp` (Task 1), the model (Task 2).
- Produces: `McpServersAction(config, context)` with `name`, `is_optional=True`, `empty_config() -> {}`, `_item(user, agent, name) -> str`, `_desired() -> dict[str, dict]`, `_scan() -> tuple[set, dict[str, dict]]`, `actual() -> set`, `plan(managed) -> list[Change]`, `verify()`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/lib/actions/test_mcp_servers_plan.py
import json
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.mcp_servers_action import McpServersAction
from dasik.lib.target.target import Target

CFG = {"users": [{"username": "andres"}],
       "mcp_servers": {"entries": [{"name": "inkscape_mcp", "command": "uvx",
                                    "args": ["inkscape_mcp"],
                                    "agents": ["claude-code", "codex"]}]}}


def _root(tmp_path, claude=None, codex=None):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/passwd").write_text(
        "root:x:0:0::/root:/bin/bash\nandres:x:1000:1000::/home/andres:/bin/zsh\n")
    home = tmp_path / "home/andres"
    home.mkdir(parents=True, exist_ok=True)
    if claude is not None:
        (home / ".claude.json").write_text(json.dumps({"mcpServers": claude}))
    if codex is not None:
        (home / ".codex").mkdir(exist_ok=True)
        (home / ".codex/config.toml").write_text(codex)
    return tmp_path


def _plan(tmp_path, managed=(), **kwargs):
    action = McpServersAction(CFG, ActionContext(target=Target(root=str(
        _root(tmp_path, **kwargs)))))
    return [(c.op.name, c.item) for c in action.plan(managed=list(managed))]


def test_absent_on_both_agents_plans_both(tmp_path):
    assert _plan(tmp_path) == [("CREATE", "andres:claude-code:inkscape_mcp"),
                               ("CREATE", "andres:codex:inkscape_mcp")]


def test_registered_on_both_agents_plans_nothing(tmp_path):
    assert _plan(tmp_path,
                 claude={"inkscape_mcp": {"type": "stdio", "command": "uvx",
                                          "args": ["inkscape_mcp"]}},
                 codex='[mcp_servers.inkscape_mcp]\ncommand = "uvx"\n'
                       'args = ["inkscape_mcp"]\n') == []


def test_a_different_command_is_a_modify(tmp_path):
    plan = _plan(tmp_path,
                 claude={"inkscape_mcp": {"type": "stdio", "command": "npx",
                                          "args": ["inkscape_mcp"]}},
                 codex='[mcp_servers.inkscape_mcp]\ncommand = "uvx"\n'
                       'args = ["inkscape_mcp"]\n')
    assert plan == [("MODIFY", "andres:claude-code:inkscape_mcp")]


def test_absent_env_and_empty_env_are_the_same_registration(tmp_path):
    """Without this the plan MODIFYs forever: apply writes `env = {}` and the
    config never said `env` at all."""
    assert _plan(tmp_path,
                 claude={"inkscape_mcp": {"type": "stdio", "command": "uvx",
                                          "args": ["inkscape_mcp"], "env": {}}},
                 codex='[mcp_servers.inkscape_mcp]\ncommand = "uvx"\n'
                       'args = ["inkscape_mcp"]\nenv = {}\n') == []


def test_owned_but_no_longer_declared_is_deleted(tmp_path):
    action = McpServersAction({"users": [{"username": "andres"}]},
                              ActionContext(target=Target(root=str(_root(
                                  tmp_path,
                                  claude={"inkscape_mcp": {"command": "uvx"}})))))
    assert [(c.op.name, c.item) for c in action.plan(
        managed=["andres:claude-code:inkscape_mcp"])] == [
        ("DELETE", "andres:claude-code:inkscape_mcp")]


def test_a_server_nobody_declared_or_owns_is_left_alone(tmp_path):
    assert _plan(tmp_path,
                 claude={"inkscape_mcp": {"type": "stdio", "command": "uvx",
                                          "args": ["inkscape_mcp"]},
                         "somebody_elses": {"command": "x"}},
                 codex='[mcp_servers.inkscape_mcp]\ncommand = "uvx"\n'
                       'args = ["inkscape_mcp"]\n') == []


def test_an_entry_can_narrow_but_not_widen_the_users(tmp_path):
    cfg = {"users": [{"username": "andres"}, {"username": "otro"}],
           "mcp_servers": {"users": ["andres"], "entries": [
               {"name": "x", "command": "uvx", "agents": ["codex"],
                "users": ["otro"]}]}}
    action = McpServersAction(cfg, ActionContext(target=Target(
        root=str(_root(tmp_path)))))
    assert action.plan(managed=[]) == []


def test_no_target_plans_nothing(tmp_path):
    assert McpServersAction(CFG, None).plan(managed=[]) == []
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/lib/actions/test_mcp_servers_plan.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the action's read side**

Mirror `ai_skills_action`: `_target()`, `_abs()`, `_passwd_entries()`, `_home_of()`, `_users()` (block list, else every declared non-root user). `_desired()` walks users × entries × agents, skipping a user an entry's own `users` excludes, and stores the normalized spec (same shape the state readers produce). `_scan()` reads both agents for each managed user. `plan()` calls `compute_changes(..., op_install=Op.CREATE, op_remove=Op.DELETE)` and adds a MODIFY for every item in `desired & actual` whose normalized spec differs. Normalization for the comparison: `args or []`, `env or {}`, `url or None`, `command or None`.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/lib/actions/test_mcp_servers_plan.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/mcp_servers_action.py tests/lib/actions/test_mcp_servers_plan.py
git commit -m "feat(mcp): plan MCP registrations per user and agent"
```

---

### Task 4: `apply` — drive the official CLIs

**Files:**
- Modify: `dasik/lib/actions/mcp_servers_action.py`
- Test: `tests/lib/actions/test_mcp_servers_apply.py`

**Interfaces:**
- Consumes: Task 3's `_desired()`/`plan()`.
- Produces: `_command_for(change, spec) -> list[tuple[str, tuple[str, ...]]]`, `_su_argv(user, script, *args)`, `apply(changes)`, `managed_keys()`, `failed_items`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/lib/actions/test_mcp_servers_apply.py
from unittest.mock import MagicMock, patch
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.mcp_servers_action import McpServersAction
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target


def _action(tmp_path, entries):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/passwd").write_text(
        "andres:x:1000:1000::/home/andres:/bin/zsh\n")
    cfg = {"users": [{"username": "andres"}], "mcp_servers": {"entries": entries}}
    return McpServersAction(cfg, ActionContext(target=Target(root=str(tmp_path))))


def _calls(action, changes):
    with patch("dasik.lib.actions.mcp_servers_action.Command.execute") as ex:
        ex.return_value = MagicMock(returncode=0, stdout="", stderr="")
        action.apply(changes)
    return [c.args for c in ex.call_args_list]


def test_claude_stdio_add_is_user_scoped_and_passes_values_as_argv(tmp_path):
    action = _action(tmp_path, [{"name": "inkscape_mcp", "command": "uvx",
                                 "args": ["inkscape_mcp"],
                                 "agents": ["claude-code"]}])
    (binary, argv), = _calls(action, [Change("mcp_servers", Op.CREATE,
                                             "andres:claude-code:inkscape_mcp")])
    assert binary == "su"
    assert argv[:3] == ["-", "andres", "-c"]
    script = argv[3]
    assert "claude mcp add" in script and "-s user" in script
    assert argv[4:] == ["--", "sh", "inkscape_mcp", "uvx", "inkscape_mcp"]


def test_codex_stdio_add(tmp_path):
    action = _action(tmp_path, [{"name": "inkscape_mcp", "command": "uvx",
                                 "args": ["inkscape_mcp"], "agents": ["codex"]}])
    (_, argv), = _calls(action, [Change("mcp_servers", Op.CREATE,
                                        "andres:codex:inkscape_mcp")])
    assert "codex mcp add" in argv[3]
    assert argv[4:] == ["--", "sh", "inkscape_mcp", "uvx", "inkscape_mcp"]


def test_env_is_passed_as_argv_never_interpolated(tmp_path):
    action = _action(tmp_path, [{"name": "s", "command": "x",
                                 "env": {"K": "v v"}, "agents": ["claude-code"]}])
    (_, argv), = _calls(action, [Change("mcp_servers", Op.CREATE,
                                        "andres:claude-code:s")])
    assert "K=v v" in argv[4:]
    assert "v v" not in argv[3]


def test_http_add_per_agent(tmp_path):
    action = _action(tmp_path, [{"name": "s", "url": "https://e/mcp",
                                 "agents": ["claude-code"]}])
    (_, argv), = _calls(action, [Change("mcp_servers", Op.CREATE,
                                        "andres:claude-code:s")])
    assert "--transport http" in argv[3]
    action = _action(tmp_path, [{"name": "s", "url": "https://e/mcp",
                                 "agents": ["codex"]}])
    (_, argv), = _calls(action, [Change("mcp_servers", Op.CREATE,
                                        "andres:codex:s")])
    assert "--url" in argv[3]


def test_delete_uses_the_agents_remove_verb_even_when_undeclared(tmp_path):
    action = _action(tmp_path, [])
    calls = _calls(action, [Change("mcp_servers", Op.DELETE,
                                   "andres:claude-code:ghost"),
                            Change("mcp_servers", Op.DELETE,
                                   "andres:codex:ghost")])
    assert "claude mcp remove" in calls[0][1][3] and "-s user" in calls[0][1][3]
    assert "codex mcp remove" in calls[1][1][3]


def test_modify_removes_then_adds(tmp_path):
    action = _action(tmp_path, [{"name": "s", "command": "uvx",
                                 "agents": ["codex"]}])
    calls = _calls(action, [Change("mcp_servers", Op.MODIFY, "andres:codex:s")])
    assert "codex mcp remove" in calls[0][1][3]
    assert "codex mcp add" in calls[1][1][3]


def test_a_failed_install_is_disowned_under_warn_and_continue(tmp_path):
    action = _action(tmp_path, [{"name": "s", "command": "uvx",
                                 "agents": ["codex"]}])
    with patch("dasik.lib.actions.mcp_servers_action.Command.execute") as ex:
        ex.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
        action.apply([Change("mcp_servers", Op.CREATE, "andres:codex:s")])
    assert action.managed_keys() == {"mcp_servers": []}


def test_abort_policy_raises(tmp_path):
    import pytest
    from dasik.lib.exceptions.exceptions import CommandExecutionError
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/passwd").write_text("andres:x:1000:1000::/home/andres:/bin/zsh\n")
    action = McpServersAction(
        {"users": [{"username": "andres"}],
         "mcp_servers": {"failure_policy": "abort",
                         "entries": [{"name": "s", "command": "uvx",
                                      "agents": ["codex"]}]}},
        ActionContext(target=Target(root=str(tmp_path))))
    with patch("dasik.lib.actions.mcp_servers_action.Command.execute") as ex:
        ex.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
        with pytest.raises(CommandExecutionError):
            action.apply([Change("mcp_servers", Op.CREATE, "andres:codex:s")])
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/lib/actions/test_mcp_servers_apply.py -v`
Expected: FAIL — `_command_for`/`apply` missing.

- [ ] **Step 3: Implement `apply`**

Scripts, with every value as `$N` (never interpolated):

```python
# claude-code, stdio
'claude mcp add "$1" -s user' + ''.join(f' -e "${i}"' for ...) + ' -- "$c" "$a1"…'
# claude-code, http
'claude mcp add "$1" -s user --transport http "$2"' + ' -H "$3"'…
# claude-code, removal
'claude mcp remove "$1" -s user'
# codex, stdio
'codex mcp add "$1"' + ' --env "$i"'… + ' -- "$c" "$a1"…'
# codex, http
'codex mcp add "$1" --url "$2"' + ' --bearer-token-env-var "$3"'
# codex, removal
'codex mcp remove "$1"'
```

Build the positional list in the same order the script reads it. MODIFY returns `[remove, add]`. A DELETE whose item the config no longer declares rebuilds its spec from the item string (`user:agent:name` — the removal verb needs nothing else). `_run` is `ai_skills_action._run`'s twin: `check=False`, `stream=True`, `label=f"mcp_servers: {item}"`, `abort` raises `CommandExecutionError`, `warn-and-continue` prints red and appends to `failed_items`. `managed_keys()` = desired minus failed.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/lib/actions/test_mcp_servers_apply.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/mcp_servers_action.py tests/lib/actions/test_mcp_servers_apply.py
git commit -m "feat(mcp): register and remove servers through each agent's CLI"
```

---

### Task 5: `import_state`, registry wiring, samples and docs

**Files:**
- Modify: `dasik/lib/actions/mcp_servers_action.py` (add `import_state`)
- Modify: `dasik/lib/actions/actions_handler_v2.py` (import + `register_action` right after `AiSkillsAction`)
- Modify: `tests/lib/test_feature_detectability.py`, `tests/lib/test_feature_sync_capture.py`
- Create: `tests/lib/actions/test_mcp_servers_sync.py`
- Modify: `config/install-megamix.json` (an `mcp_servers` block so a sample exercises it), `docs/config-reference.md`, `README.md`
- Test: as listed

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: `import_state(managed=None) -> {"mcp_servers": {...}}`, and the registered action.

- [ ] **Step 1: Write the failing tests**

```python
# tests/lib/actions/test_mcp_servers_sync.py
import json
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.mcp_servers_action import McpServersAction
from dasik.lib.target.target import Target


def _root(tmp_path, users=("andres",), claude=None, codex=None):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    lines = ["root:x:0:0::/root:/bin/bash\n"]
    for i, user in enumerate(users):
        lines.append(f"{user}:x:{1000 + i}:{1000 + i}::/home/{user}:/bin/zsh\n")
    (tmp_path / "etc/passwd").write_text("".join(lines))
    for user in users:
        home = tmp_path / f"home/{user}"
        home.mkdir(parents=True, exist_ok=True)
        if claude:
            (home / ".claude.json").write_text(json.dumps({"mcpServers": claude}))
        if codex:
            (home / ".codex").mkdir(exist_ok=True)
            (home / ".codex/config.toml").write_text(codex)
    return tmp_path


def _sync(tmp_path, config=None, **kwargs):
    action = McpServersAction(config or {}, ActionContext(
        target=Target(root=str(_root(tmp_path, **kwargs)))))
    return action.import_state()["mcp_servers"]


def test_a_server_on_both_agents_is_captured_once_with_both_agents(tmp_path):
    block = _sync(tmp_path,
                  claude={"inkscape_mcp": {"type": "stdio", "command": "uvx",
                                           "args": ["inkscape_mcp"]}},
                  codex='[mcp_servers.inkscape_mcp]\ncommand = "uvx"\n'
                        'args = ["inkscape_mcp"]\n')
    assert block == {"users": ["andres"], "entries": [
        {"name": "inkscape_mcp", "command": "uvx", "args": ["inkscape_mcp"],
         "agents": ["claude-code", "codex"]}]}


def test_a_machine_with_no_servers_invents_nothing(tmp_path):
    assert _sync(tmp_path) == {}


def test_env_is_captured_verbatim(tmp_path):
    block = _sync(tmp_path, claude={"s": {"command": "x", "env": {"K": "v"}}})
    assert block["entries"][0]["env"] == {"K": "v"}


def test_an_http_server_is_captured_as_a_url(tmp_path):
    block = _sync(tmp_path, claude={"s": {"type": "http", "url": "https://e/mcp"}})
    assert block["entries"][0] == {"name": "s", "url": "https://e/mcp",
                                   "agents": ["claude-code"]}


def test_a_server_only_one_user_has_carries_its_own_users(tmp_path):
    root = _root(tmp_path, users=("andres", "otro"))
    (root / "home/otro/.claude.json").write_text(json.dumps({"mcpServers": {}}))
    (root / "home/andres/.claude.json").write_text(
        json.dumps({"mcpServers": {"s": {"command": "x"}}}))
    action = McpServersAction({}, ActionContext(target=Target(root=str(root))))
    block = action.import_state()["mcp_servers"]
    assert block["users"] == ["andres"]


def test_the_captured_block_validates_and_replans_to_nothing(tmp_path):
    kwargs = dict(claude={"inkscape_mcp": {"type": "stdio", "command": "uvx",
                                           "args": ["inkscape_mcp"]}},
                  codex='[mcp_servers.inkscape_mcp]\ncommand = "uvx"\n'
                        'args = ["inkscape_mcp"]\n')
    captured = _sync(tmp_path, **kwargs)
    from dasik.lib.models.mcp_servers_model import McpServersModel
    McpServersModel(**captured)                      # `check` accepts it
    root = _root(tmp_path, **kwargs)
    action = McpServersAction({"users": [{"username": "andres"}],
                               "mcp_servers": captured},
                              ActionContext(target=Target(root=str(root))))
    assert action.plan(managed=[]) == []             # sync -> plan is silent
```

Rows to add to the two matrices:

```python
# tests/lib/test_feature_detectability.py  — after the ai_skills section
_MCP_CFG = {"users": [{"username": "andres"}], "mcp_servers": {"entries": [
    {"name": "inkscape_mcp", "command": "uvx", "args": ["inkscape_mcp"],
     "agents": ["claude-code"]}]}}
_MCP_ITEM = "andres:claude-code:inkscape_mcp"


def _mcp_root(tmp_path, registered):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/passwd").write_text(
        "andres:x:1000:1000::/home/andres:/bin/zsh\n")
    home = tmp_path / "home/andres"
    home.mkdir(parents=True, exist_ok=True)
    (home / ".claude.json").write_text(json.dumps({"mcpServers": registered}))
    return tmp_path


def _mcp_plan(tmp_path, config, registered, managed=()):
    from dasik.lib.actions.mcp_servers_action import McpServersAction
    action = McpServersAction(config, _ctx(_mcp_root(tmp_path, registered)))
    return [(c.op.name, c.item) for c in action.plan(managed=list(managed))]


def test_an_unregistered_mcp_server_is_planned(tmp_path):
    assert _mcp_plan(tmp_path, _MCP_CFG, {}) == [("CREATE", _MCP_ITEM)]


def test_a_registered_mcp_server_plans_nothing(tmp_path):
    assert _mcp_plan(tmp_path, _MCP_CFG, {"inkscape_mcp": {
        "type": "stdio", "command": "uvx", "args": ["inkscape_mcp"]}}) == []


def test_dropping_the_block_removes_the_server_dasik_owns(tmp_path):
    assert _mcp_plan(tmp_path, {"users": [{"username": "andres"}]},
                     {"inkscape_mcp": {"command": "uvx"}},
                     managed=[_MCP_ITEM]) == [("DELETE", _MCP_ITEM)]


def test_a_server_dasik_never_registered_is_left_alone(tmp_path):
    assert _mcp_plan(tmp_path, {"users": [{"username": "andres"}]},
                     {"somebody_elses": {"command": "x"}}) == []
```

```python
# tests/lib/test_feature_sync_capture.py
def test_sync_captures_the_mcp_servers_a_machine_has(tmp_path):
    block = _mcp_import(tmp_path, {"inkscape_mcp": {
        "type": "stdio", "command": "uvx", "args": ["inkscape_mcp"]}})
    assert block["entries"] == [{"name": "inkscape_mcp", "command": "uvx",
                                 "args": ["inkscape_mcp"],
                                 "agents": ["claude-code"]}]


def test_sync_invents_no_mcp_servers_on_a_machine_without_any(tmp_path):
    assert _mcp_import(tmp_path, {}) == {}
```

where `_mcp_import` builds the same fake root and returns
`McpServersAction({}, _ctx(root)).import_state()["mcp_servers"]`.

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/lib/actions/test_mcp_servers_sync.py tests/lib/test_feature_detectability.py tests/lib/test_feature_sync_capture.py -v`
Expected: FAIL — `import_state` returns the base class's value.

- [ ] **Step 3: Implement `import_state`**

Group by `(transport, command, tuple(args), tuple(sorted(env.items())), url, headers…)` → `{agents, users}`. `_sync_users()` is `ai_skills`': the declared users, else every uid in `[1000, 65534)` from the target's passwd. Emit `users` on an entry only when its owners differ from the block's. Omit `args`/`env` when empty so a captured config is the smallest thing that reproduces the machine. Carry `failure_policy` over from the config when it is not the default (a machine cannot report a policy).

- [ ] **Step 4: Register the action**

In `setup_actions()`, right after the `AiSkillsAction` block, with the same comment shape explaining the ordering:

```python
    # MCP servers, registered through each agent's own CLI. Same phase as
    # AiSkillsAction: after Users ($HOME must exist and the CLI runs AS the
    # user) and after Packages (the agent's binary has to be there).
    register_action(
        action_class=McpServersAction,
        config_key='__root__',   # reads root-level `mcp_servers` + `users`
        is_optional=True,
    )
```

- [ ] **Step 5: Sample config + docs**

Add to `config/install-megamix.json` an `mcp_servers` block declaring `inkscape_mcp` for both agents. Regenerate the `mcp_servers` section of `docs/config-reference.md` the way that file documents (pydantic introspection), including the `env` privacy note. Add the block to `README.md` where the other domains are listed.

- [ ] **Step 6: Run the full suite + the gates**

Run: `pytest -q && mypy dasik && bandit -q -r dasik -c pyproject.toml && dasik check config/install-megamix.json`
Expected: all green, `check` rc 0.

- [ ] **Step 7: Commit**

```bash
git add -- dasik/lib/actions/mcp_servers_action.py dasik/lib/actions/actions_handler_v2.py \
        tests/lib config/install-megamix.json docs/config-reference.md README.md
git commit -m "feat(mcp): capture MCP servers in sync and register the domain"
```

(Explicit paths — never `git add -A` in this repo.)

---

### Task 6: VM — every verb, and the server actually drawing something

**Files:**
- Create: `config/vm-mcp.json`
- Create: `scripts/vmtest/guest-mcp.sh`
- Modify: `docs/vm-testing.md` (list the new pair)

**Interfaces:**
- Consumes: everything above.
- Produces: a guest log with `MCP-<step>=<rc>` lines.

- [ ] **Step 1: Write `config/vm-mcp.json`**

Copy `config/vm-ai-skills.json`'s shape (ext4, sd-boot, `console=ttyS0,115200`, root autologin, `python-pydantic`/`python-colorama` so dasik runs from the 9p mount) and replace the `ai_skills` block with:

```json
"packages": ["base", "linux", "linux-firmware", "sudo", "git", "nodejs", "npm",
             "uv", "python", "python-pydantic", "python-colorama", "inkscape"],
"mcp_servers": {"users": ["tester"], "entries": [
  {"name": "inkscape_mcp", "command": "uvx", "args": ["inkscape_mcp"],
   "agents": ["claude-code", "codex"]}]}
```

- [ ] **Step 2: Write `scripts/vmtest/guest-mcp.sh`**

Install the agent CLIs with npm (`npm i -g @anthropic-ai/claude-code @openai/codex`), then, echoing one `MCP-…=rc` line per step:

```sh
run MCP-CHECK        dasik check   /repo/config/vm-mcp.json
run MCP-PLAN-EMPTY   dasik plan    /repo/config/vm-mcp.json --target /   # 2 CREATEs
run MCP-APPLY        dasik apply   /repo/config/vm-mcp.json --target / --yes
# the machine, not the log: both registries name it
grep -q inkscape_mcp /home/tester/.claude.json
grep -q '\[mcp_servers.inkscape_mcp\]' /home/tester/.codex/config.toml
run MCP-PLAN-AGAIN   dasik plan …    # must print no change
run MCP-SYNC         dasik sync    /repo/config/vm-mcp.json --target / --output /tmp/cap.json
run MCP-CHECK-CAP    dasik check   /tmp/cap.json
run MCP-PLAN-CAP     dasik plan    /tmp/cap.json --target /         # silent
run MCP-GENERATIONS  dasik generations --target /
run MCP-ROLLBACK     dasik rollback --target / --yes
run MCP-PLAN-ROLLED  dasik plan    /repo/config/vm-mcp.json --target /
# block removed: the owned registration is DELETEd, a hand-made one is not
jq 'del(.mcp_servers)' /repo/config/vm-mcp.json > /tmp/noblock.json
su - tester -c 'claude mcp add manual -s user -- true'
run MCP-PLAN-DROPPED dasik plan  /tmp/noblock.json --target /        # DELETE ours only
run MCP-APPLY-DROPPED dasik apply /tmp/noblock.json --target / --yes
grep -q manual /home/tester/.claude.json                            # untouched
```

- [ ] **Step 3: Add the functional probe of the server itself**

In the same guest script, a minimal stdio MCP client (python, stdlib only) that speaks JSON-RPC to `uvx inkscape_mcp`: `initialize`, `tools/list`, then a `tools/call` that writes an SVG, and finally assert the file exists and starts with `<?xml`/`<svg`. Echo `MCP-SERVER-DRAWS=<rc>`. This is the "does the thing work at all" check, separate from the domain.

- [ ] **Step 4: Run the VM**

```bash
export DASIK_VM_ISO=/path/to/archlinux-x86_64.iso
export DASIK_VM_WORKDIR=/var/tmp/dasik-vmtest DASIK_VM_RAM=4096
scripts/vmtest/qemu.sh install-driven config/vm-mcp.json      # fresh qcow2
scripts/vmtest/qemu.sh drive $DASIK_VM_WORKDIR/vda.qcow2 guest-mcp.sh MCP-DONE
```

Expected: every `MCP-…=rc` line is `=0`, `MCP-PLAN-AGAIN` and `MCP-PLAN-CAP` print no change.

- [ ] **Step 5: Prove the checks can fail**

Break one thing at a time in the guest, confirm the matching marker goes non-zero, restore: (a) make `_command_for` drop `-s user` → `MCP-PLAN-AGAIN` must stop being silent (the registration lands in the wrong scope); (b) make `plan` ignore `env` drift → the MODIFY test must fail. A green never seen red is not evidence.

- [ ] **Step 6: Commit**

```bash
git add config/vm-mcp.json scripts/vmtest/guest-mcp.sh docs/vm-testing.md
git commit -m "test(mcp): drive every verb in a guest, and the server itself"
```

---

### Task 7: Ship it

- [ ] **Step 1: Run the four gates exactly as the pre-push hook does**

Run: `pytest --cov=dasik && mypy dasik && bandit -q -r dasik -c pyproject.toml && scripts/mutation.sh`
Expected: coverage ≥ 80, mypy clean, bandit clean, no surviving mutants.

- [ ] **Step 2: Declare the server in the personal config**

In `~/repos/dasik-personal-config`: add `common/mcp-servers.json` with the `inkscape_mcp` entry for both agents, and an `"mcp_servers": {"$include": "common/mcp-servers.json"}` line in each of the three machine files. Run `dasik check` on each (needs the machine's `secrets/`).

- [ ] **Step 3: Open the PR**

`gh pr create` with a **How to test manually** section: `dasik check config/vm-mcp.json`, `dasik plan config/install-megamix.json`, the VM pair, and the verdict of which verbs ran for real in the guest versus which were asserted with mocks.

# opencode as an AI agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `mcp_servers` accepts `opencode` as an agent and `ai_skills` gains an `opencode-plugin` method, both converging, captured and removed like the other agents.

**Architecture:** one new module, `dasik/lib/actions/opencode_config.py`, owns reading (JSONC, both global files, both shapes) and writing (`opencode.json`, atomic, symlink-safe) opencode's config. `McpServersAction` writes MCP servers through it instead of a CLI; `AiSkillsAction` installs plugins with `opencode plugin add` and reads/removes through it.

**Tech Stack:** Python ≥ 3.10 stdlib + pydantic; pytest; QEMU guest harness (`scripts/vmtest/`).

**Spec:** `docs/superpowers/specs/2026-11-23-opencode-agent-design.md`

## Global Constraints

- Runtime deps stay `pydantic`, `colorama` — the JSONC parser is stdlib only.
- Files: `<home>/.config/opencode/opencode.json` and `<home>/.config/opencode/opencode.jsonc`; fixed paths, no `XDG_CONFIG_HOME`.
- Write target for anything dasik adds: `opencode.json`, MCP under `mcp.servers.<name>`; a new file starts as `{"$schema": "https://opencode.ai/config.json"}`.
- Written JSON: `json.dumps(data, indent=2) + "\n"`.
- Agent id: `opencode`. Method: `opencode-plugin`.
- `apply` only runs inside the guest; tests mock `Command.execute` and use a `tmp_path` root (`Target(root=...)`).
- TDD for every task touching `models/` or `actions/`; each new check seen failing once.
- Stage explicit paths only — never `git add -A` (private captures live untracked in `config/`).
- Commits end with the two attribution lines from the session (Co-Authored-By / Claude-Session).

## Review Focus

1. **Symlinked config** (`~/.config/opencode` or `opencode.json` a symlink, e.g. a dotfiles manager or a hostile link to `/etc/shadow`): dasik refuses that user's item with a message naming the path. It never reads through the link or writes through it as root. → Task 1 tests.
2. **The user's `opencode.jsonc` survives an MCP add byte-for-byte**, including comments, `provider` and `plugin`. → Task 3 test.
3. **Invalid JSON in `opencode.json`** (the user hand-edited it badly): the item fails under `failure_policy` and the file is unchanged. dasik never "repairs" it into `{}`. → Task 3 test.
4. **`opencode plugin remove` exits 0 having removed nothing:** the DELETE is judged by re-reading, so a plugin still present ends up in `failed_items`. → Task 5 test.
5. **A specifier with `:` in the item key** (`superpowers@git+https://…`) survives `_spec_from_item` for a DELETE of an undeclared plugin. → Task 5 test.

---

### Task 1: `opencode_config` — read and write opencode's config

**Files:**
- Create: `dasik/lib/actions/opencode_config.py`
- Test: `tests/lib/actions/test_opencode_config.py`

**Interfaces:**
- Produces:
  - `JSON_REL = ".config/opencode/opencode.json"`, `JSONC_REL = ".config/opencode/opencode.jsonc"`, `SCHEMA_URL = "https://opencode.ai/config.json"`
  - `parse_jsonc(text: str) -> Any`, which raises `ValueError` on anything unparseable.
  - `@dataclass(frozen=True) class Found: rel: str; nested: bool; entry: Any`. `rel` is the file it lives in. `nested` is True for `mcp.servers.<n>` (MCP) and for the `plugins` key (plugins), False for the flat forms.
  - `read_mcp(home: str) -> Dict[str, Found]`: the union of both files. On a name clash, `opencode.jsonc` wins, because opencode loads it second.
  - `read_plugins(home: str) -> Dict[str, Found]`: keys are the exact specifier strings. Non-string elements (tuple-with-options) are ignored.
  - `edit(home: str, rel: str, mutate: Callable[[dict], None], uid: int, gid: int) -> None`. Raises `OpencodeConfigError(Exception)` (defined here) on a symlinked file or parent, a non-dict top level, or an unparseable existing file.

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_jsonc_strips_comments_but_not_inside_strings():
    text = '{\n // c\n "u": "http://x//y", /* b */ "a": [1,2,],\n}'
    assert parse_jsonc(text) == {"u": "http://x//y", "a": [1, 2]}

def test_parse_jsonc_rejects_garbage():
    with pytest.raises(ValueError): parse_jsonc("{nope")

def test_read_mcp_unions_both_files_and_both_shapes(tmp_path):
    # .json: flat "a" + nested servers "b"; .jsonc: flat "c" with a comment
    found = read_mcp(str(home))
    assert set(found) == {"a", "b", "c"}
    assert found["b"].nested and found["b"].rel == JSON_REL
    assert found["c"].rel == JSONC_REL

def test_jsonc_wins_a_name_clash(tmp_path): ...   # same name in both → rel == JSONC_REL

def test_missing_or_broken_files_read_as_nothing(tmp_path):
    assert read_mcp(str(empty_home)) == {} and read_plugins(str(broken_home)) == {}

def test_read_plugins_takes_both_keys_and_skips_non_strings(tmp_path):
    # .jsonc plugin: ["superpowers@git+https://github.com/obra/superpowers.git", ["x", {}]]
    # .json plugins: ["pj@1.0.0"]
    assert set(read_plugins(str(home))) == {
        "superpowers@git+https://github.com/obra/superpowers.git", "pj@1.0.0"}

def test_edit_creates_json_with_schema_owned_by_user(tmp_path):
    edit(str(home), JSON_REL, lambda d: d.setdefault("mcp", {}), uid, gid)
    data = json.loads((home / JSON_REL).read_text())
    assert data == {"$schema": SCHEMA_URL, "mcp": {}}
    # os.chown patched: called for the file and every created directory with (uid, gid)

def test_edit_preserves_mode_and_other_keys(tmp_path):
    # existing 0600 file with {"provider": {...}}; after edit, mode 0600 and provider intact

def test_edit_refuses_a_symlinked_file_and_parent(tmp_path):
    # opencode.json -> ../../etc/passwd   and   .config/opencode -> /tmp/elsewhere
    with pytest.raises(OpencodeConfigError): edit(...)
    # the link target is unchanged

def test_edit_refuses_an_unparseable_file_and_leaves_it(tmp_path):
    (home / JSON_REL).write_text("{nope")
    with pytest.raises(OpencodeConfigError): edit(...)
    assert (home / JSON_REL).read_text() == "{nope"
```

- [ ] **Step 2: Run them, expect failure on the import.** `pytest tests/lib/actions/test_opencode_config.py -q`. Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement.** The parser is a single pass over the string that tracks whether it is inside a string literal (honouring `\` escapes). It drops `//…\n` and `/*…*/`, then removes trailing commas before `}`/`]` with the same in-string tracking, then calls `json.loads`. `edit`:
  - reuse `HomeFilesAction._check_parent`'s rule (symlinked or non-directory parent → refuse) and also refuse when the file itself is a link;
  - create the missing directories, `chown` each one;
  - write a temp file in the same directory, `fchmod` it to the old mode (0644 when new), `os.replace`, `chown`.

- [ ] **Step 4: Run the tests, expect a pass.** Then see each test go red once by breaking its line, e.g. drop the in-string tracking and watch `test_parse_jsonc_strips_comments_but_not_inside_strings` fail.

- [ ] **Step 5: Commit** — `feat(opencode): read and write opencode's global config`.

---

### Task 2: `mcp_servers` model + reader for opencode

**Files:**
- Modify: `dasik/lib/models/mcp_servers_model.py` (`AGENTS`, `_HEADERS_AGENTS`, field descriptions)
- Modify: `dasik/lib/actions/mcp_servers_state.py` (add `opencode_mcp`)
- Test: `tests/lib/actions/test_mcp_servers_opencode.py` (new; follow the layout of `test_mcp_servers_antigravity.py`)

**Interfaces:**
- Consumes: `read_mcp` (Task 1).
- Produces: `opencode_mcp(home: str) -> Dict[str, Dict[str, Any]]` in the shared `_spec` shape. `AGENTS == ("claude-code", "codex", "antigravity", "opencode")`.

- [ ] **Step 1: Failing tests**

```python
def test_opencode_is_a_known_agent(): McpServersModel(entries=[{**STDIO, "agents": ["opencode"]}])
def test_headers_accepted_for_opencode(): ...          # http entry with headers, agents ["opencode"]
def test_bearer_variable_still_codex_only(): ...       # agents ["opencode"] + bearer → ValidationError
def test_reads_local_server(tmp_path):
    # {"type":"local","command":["uvx","--python","3.13","inkscape_mcp"],"environment":{"K":"v"}}
    assert opencode_mcp(home)["inkscape_mcp"] == _spec("uvx", ["--python","3.13","inkscape_mcp"], {"K":"v"}, None)
def test_reads_remote_server_with_headers(tmp_path): ...  # → transport http, url, headers
def test_disabled_server_counts_as_registered(tmp_path):  # "enabled": false → still present (spec decision)
```

- [ ] **Step 2: Run them.** Expected FAIL (`opencode` rejected; `opencode_mcp` missing).
- [ ] **Step 3: Implement.** `command[0]`/`command[1:]` → `command`/`args`; `environment` → `env`; `remote` → `url`/`headers`. An entry with no list `command` and no `url` is skipped.
- [ ] **Step 4: Run, expect PASS.** Also run `pytest tests/lib/actions/test_mcp_servers_*.py tests/lib/models -q`: the other agents must not change.
- [ ] **Step 5: Commit** — `feat(mcp_servers): accept and read opencode registrations`.

---

### Task 3: `mcp_servers` plan/apply/sync for opencode

**Files:**
- Modify: `dasik/lib/actions/mcp_servers_action.py` (`_READERS`, `apply`, new `_apply_opencode`)
- Test: `tests/lib/actions/test_mcp_servers_opencode.py`

**Interfaces:**
- Consumes: `opencode_mcp`, `read_mcp`, `edit`, `OpencodeConfigError`, `JSON_REL`.
- Produces: `McpServersAction._apply_opencode(self, change: Change, spec: Dict[str, Any]) -> None`. For opencode items, `apply` calls it instead of `_command_for`. Failures go through the same failure_policy path as `_run`: extract `_fail(item, message)` from `_run`'s tail and have both use it.

- [ ] **Step 1: Failing tests**

```python
def test_missing_on_opencode_plans_a_create(tmp_path)
def test_registered_on_opencode_plans_nothing(tmp_path)           # nested shape in opencode.json
def test_registered_in_users_jsonc_flat_plans_nothing(tmp_path)
def test_different_args_on_opencode_are_a_modify(tmp_path)
def test_owned_but_undeclared_on_opencode_is_deleted(tmp_path)
def test_an_unowned_opencode_server_is_left_alone(tmp_path)

def test_create_writes_nested_entry_into_opencode_json(tmp_path):
    # after apply: data["mcp"]["servers"]["inkscape_mcp"] ==
    #   {"type":"local","command":["uvx","--python","3.13","inkscape_mcp"],"environment":{...}}
    # (environment omitted when env is empty; remote → {"type":"remote","url":...,"headers":{...}})
def test_create_leaves_users_jsonc_byte_identical(tmp_path)        # Review Focus 2
def test_create_never_runs_a_cli(tmp_path)                          # Command.execute not called for opencode items
def test_delete_removes_from_the_file_it_lives_in(tmp_path)         # once in .json nested, once in .jsonc flat
def test_modify_of_a_jsonc_server_moves_it_to_json(tmp_path)
def test_broken_json_fails_the_item_and_keeps_the_file(tmp_path)    # Review Focus 3; warn → failed_items, abort → CommandExecutionError
def test_symlinked_config_dir_fails_the_item(tmp_path)              # Review Focus 1
def test_no_opencode_binary_fails_the_item(tmp_path)                # target has no usr/bin/opencode → failed, no file created
def test_sync_captures_opencode_with_other_agents_in_one_entry(tmp_path)
    # same stdio spec on claude (~/.claude.json) and opencode → one entry, agents ["claude-code","opencode"]
def test_captured_block_validates_and_replans_to_nothing(tmp_path)
```

- [ ] **Step 2: Run them.** Expected FAIL.
- [ ] **Step 3: Implement.**
  - Add `"opencode": opencode_mcp` to `_READERS`; sync then works unchanged.
  - Binary check: `os.path.exists(self._abs("/usr/bin/opencode"))`.
  - uid/gid: widen `_passwd_entries` to `(home, uid, gid)`, the way `home_files_action._passwd` does, and update its two callers.
  - CREATE/MODIFY: drop the name from wherever `read_mcp` found it, then set `mcp.servers.<name>` in `JSON_REL`.
  - DELETE: drop it from its `Found.rel`, flat or nested.
  - Remove a `mcp.servers` or `mcp` dict that is left empty; leave all other keys alone.
- [ ] **Step 4: Run, expect PASS**, and see each new test go red once.
- [ ] **Step 5: Commit** — `feat(mcp_servers): converge opencode by writing opencode.json`.

---

### Task 4: `ai_skills` model — `opencode-plugin`

**Files:**
- Modify: `dasik/lib/models/ai_skills_model.py` (`Method`, `METHOD_AGENT`, `_coherent`, descriptions)
- Test: `tests/lib/models/test_ai_skills_model.py` (append; create a dedicated file if it passes ~300 LoC)

**Interfaces:**
- Produces:
  - `METHOD_AGENT["opencode-plugin"] == "opencode"`;
  - `OPENCODE_SPEC_RE`, the accepted specifier: `^[a-z0-9][a-z0-9._-]*(@[^\s@]+|@git\+https://\S+)?$`;
  - `opencode_plugin_name(spec: str) -> str`, the part before the first `@`.

- [ ] **Step 1: Failing tests**

```python
def test_opencode_plugin_needs_source():                       # missing source → ValidationError
def test_opencode_plugin_accepts_npm_and_git_specs():
    for s in ["pj", "pj@1.0.0", "superpowers@git+https://github.com/obra/superpowers.git"]: ok(s)
def test_opencode_plugin_rejects(): for s in ["-x", "@scope/pkg", "file:../p", "pj@1\n", "../p"]: bad(s)
def test_opencode_plugin_name_must_match_source(): name "other", source "pj@1" → ValidationError
def test_opencode_plugin_takes_no_marketplace_or_agents()
def test_agent_ids_of_opencode_plugin(): assert entry.agent_ids == ["opencode"]
```

- [ ] **Step 2: Run them.** Expected FAIL.
- [ ] **Step 3: Implement** a new `elif self.method == "opencode-plugin":` branch: `reject_control_chars`, the regex, `name == opencode_plugin_name(source)`, no `marketplace`/`agents`. Its `elif` has to come before the generic marketplace `else`.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat(ai_skills): model the opencode-plugin method`.

---

### Task 5: `ai_skills` plan/apply for opencode plugins

**Files:**
- Modify: `dasik/lib/actions/ai_skills_action.py` (`_desired`, `_scan`, `_command_for`, `apply`, `_spec_from_item`)
- Test: `tests/lib/actions/test_ai_skills_opencode_plugin.py` (new; follow the layout of `test_ai_skills_antigravity_plugin.py`)

**Interfaces:**
- Consumes: `read_plugins`, `edit`, `OpencodeConfigError`, `JSON_REL`, `JSONC_REL` (Task 1); `opencode_plugin_name` (Task 4).
- Produces: the item `f"{user}:opencode:plugin:{source}"`; the spec `{"kind": "plugin", "user", "agent": "opencode", "method": "opencode-plugin", "name", "source"}`.

- [ ] **Step 1: Failing tests**

```python
def test_declared_plugin_missing_plans_a_create(tmp_path)
def test_plugin_in_users_jsonc_legacy_key_plans_nothing(tmp_path)   # the user's machines today
def test_changed_source_plans_delete_old_and_create_new(tmp_path)
def test_create_runs_opencode_plugin_add_as_user(tmp_path):
    # su argv carries script 'opencode plugin add "$1"' and $1 == source
def test_delete_in_json_runs_plugin_remove_with_exact_spec(tmp_path)
def test_delete_that_removed_nothing_fails_the_item(tmp_path)      # Review Focus 4: rc 0, still in file → failed_items
def test_delete_in_jsonc_edits_the_file_and_runs_no_cli(tmp_path)
def test_delete_of_undeclared_git_spec_rebuilds_from_item(tmp_path) # Review Focus 5
```

- [ ] **Step 2: Run them.** Expected FAIL.
- [ ] **Step 3: Implement.**
  - `_desired`: add an `opencode-plugin` branch keyed by `source`.
  - `_scan`: add `read_plugins(home)` items.
  - `_command_for`: CREATE → `('opencode plugin add "$1"', (source,))`, DELETE → `('opencode plugin remove "$1"', (source,))`.
  - `apply`: a DELETE whose `Found.rel == JSONC_REL` is an `edit` that drops it from `plugin`/`plugins`; after any DELETE, re-read and `_fail` the item if it is still present.
  - `_spec_from_item`: when `agent == "opencode"`, the value is the source, so set `name` with `opencode_plugin_name`.
- [ ] **Step 4: Run, expect PASS.** Then run `pytest tests/lib/actions/test_ai_skills_*.py -q`.
- [ ] **Step 5: Commit** — `feat(ai_skills): install and remove opencode plugins`.

---

### Task 6: `ai_skills` sync for opencode plugins

**Files:**
- Modify: `dasik/lib/actions/ai_skills_action.py` (`import_state`)
- Test: `tests/lib/actions/test_ai_skills_opencode_plugin.py`

**Interfaces:**
- Consumes: Task 5's item/spec shapes, and `OPENCODE_SPEC_RE`/`opencode_plugin_name` from Task 4.
- Produces: sync entries `{"name": ..., "method": "opencode-plugin", "source": spec}`.

- [ ] **Step 1: Failing tests**

```python
def test_sync_captures_each_plugin_as_an_entry(tmp_path)
def test_sync_skips_scoped_and_file_specs_with_a_warning(tmp_path, capsys):
    # "@scope/pkg", "file:../p" → not in entries; capsys shows "not captured"
def test_captured_block_validates_and_replans_to_nothing(tmp_path)
```

- [ ] **Step 2: Run them.** Expected FAIL.
- [ ] **Step 3: Implement.** Add the key `("opencode-plugin", name, spec)` to `found`. Specs the regex rejects go to `skipped`. Add an output branch for the new key shape.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat(ai_skills): capture opencode plugins on sync`.

---

### Task 7: Journey test, docs, sample config

**Files:**
- Modify: `tests/cli/test_verbs_integration.py` (one opencode journey)
- Modify: `docs/config-reference.md`, `docs/wiki/Configuration.md` (the agent and the method)
- Modify: `config/install-megamix.json` (add `opencode` to the `inkscape_mcp` agents)

- [ ] **Step 1: Write the journey.** Run it through `main([...])` against a `tmp_path` root, with `usr/bin/opencode` present, `Command.execute` mocked, and one stdio MCP plus one opencode plugin declared:
  - `plan → apply → plan` comes back empty;
  - `sync → check → plan` comes back empty;
  - with the block removed, `plan` shows both DELETEs and `apply` clears them;
  - `generations` lists the apply;
  - `rollback` restores the previous generation.
- [ ] **Step 2: Run it.** Expected PASS. Break `_READERS["opencode"]` once and watch it go red.
- [ ] **Step 3: Docs + sample.** Then `dasik check config/install-megamix.json` → rc 0, and `dasik plan` dry run against the sample configs.
- [ ] **Step 4: Commit** — `test+docs: opencode agent journey and reference`.

---

### Task 8: Guest proof

**Files:**
- Create: `config/vm-opencode.json`, `scripts/vmtest/guest-opencode.sh` (copy `guest-mcp.sh`'s structure: `OC-<step>-RC=` markers, `OC-DONE rc=N`, `--no-log`, network via SLIRP)

- [ ] **Step 1:** Write the header saying why this is not a unit test: the real `opencode plugin add/remove` behaviour, real `su` execution, and opencode loading the merged config.
- [ ] **Step 2:** The guest script runs these steps, each with its own `rc`:
  1. Install `opencode` (pacman).
  2. Hand-write a `.jsonc` with a comment and a legacy `plugin`.
  3. `plan`, then `apply`, then `plan` again: the second plan must be silent.
  4. `opencode debug config` lists the dasik server under `mcp.servers`, and `opencode mcp list` shows it. Set the service port first.
  5. The `.jsonc` checksum is unchanged.
  6. `sync`, then `check`, then `plan`: silent.
  7. Change an arg: `plan` shows a MODIFY, then `apply`.
  8. Remove the block: `plan` shows the DELETEs, `apply` removes them, and the server and plugin are gone from the files.
  9. `generations`, then `rollback`.

  Also try `opencode plugin add` under `arch-chroot` during the install-driven apply. If it fails, record that fact in `docs/FACTS.md`, and the header says the plugin step is post-install only.
- [ ] **Step 3:** Run from a fresh qcow2 under `timeout --kill-after=60s 3600`, wait on the `OC-DONE` marker, and see one check go red by sabotage. The obvious one: comment out the `.jsonc` checksum preservation.
- [ ] **Step 4: Commit** — `test(vm): opencode mcp + plugin guest proof`.

---

### Final

- [ ] `timeout --kill-after=60s 1800 pytest --cov=dasik` green, coverage ≥ 80.
- [ ] `mypy dasik`, `bandit`, `scripts/mutation.sh` green (the pre-push gates, run locally — no push unless the user says so).
- [ ] Whole-branch review (`superpowers:requesting-code-review`).
- [ ] Report which verbs ran for real (guest) vs mocked.

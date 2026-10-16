# `dasik upgrade` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new verb `dasik upgrade <config>` that runs the native updater of every declared and installed `uv_tools` / `ai_skills` item, and prints a per-item summary.

**Architecture:** Each of the two actions gets a pure `upgrade_steps()`, which says *what* to run per item, reusing its own `_desired()` / `actual()` / home resolution. A small `dasik/lib/upgrade/` module runs those steps through `Command.execute` (`su - user -c …`), reads each item's version before and after, classifies the outcome and renders it. The CLI wires it as a verb: no reconciler, no manifest, no generation.

**Tech Stack:** Python ≥3.10, pytest + monkeypatch, the existing `Command.execute`, and the QEMU harness under `scripts/vmtest/`.

**Spec:** `docs/superpowers/specs/2026-10-16-ai-upgrade-verb-design.md` (read it; the "Calls made by the designer" section binds).

## Global Constraints

- AI only: `uv_tools` and `ai_skills`. `mcp_servers` gets no command; the summary prints one line saying why (see Task 4). Never pacman/AUR.
- Only items both **declared** and **installed** are touched. Declared-but-absent → `skipped (not installed — run dasik apply)`.
- Exact pins (`==`, `===`) → `skipped (pinned)`. Ranges are upgraded.
- Refreshes are scoped by name; never the "all marketplaces" form.
- No manifest write, no generation; `plan` must be silent after `upgrade`.
- Every command is non-interactive (`-y` where the CLI has one).
- Every command runs as the declared user via `su - <user> -c <script> -- sh <args>`, with values only as `$1..`.
- A failing item doesn't stop the rest. Exit 1 if any item failed, else 0.
- TDD for every new decision; unit tests under `tests/lib/`, mirroring the layout.
- Stage explicit paths only; **never `git add -A`** (private configs live untracked in `config/`).

## Review Focus

1. **A declared user with no passwd entry / no home** (fresh target, typo in `users`): the user's items come out `skipped`, with no traceback. → Task 2 and Task 3 tests.
2. **No AI blocks in the config:** prints `Nothing to upgrade.` and exits 0. → Task 4 journey test.
3. **One `skills` entry for three agents:** `npx skills update` runs **once** per (user, skill), not once per agent. → Task 3 test.
4. **No network:** every command fails, so every item is `failed`, rc 1, and `plan` afterwards is still silent. → Task 1 test (runner) + Task 5 guest.
5. **A uv tool declared with extras or odd casing** (`semgrep[all]`, `Inkscape_MCP`): the version is still found (normalised dist-info lookup). → Task 2 test.

---

### Task 1: Upgrade step model, runner and summary (+ extract `su_argv`)

**Files:**
- Create: `dasik/lib/command_worker/su_argv.py`
- Modify: `dasik/lib/actions/uv_tools_action.py` and `dasik/lib/actions/ai_skills_action.py`. Their `_su_argv` staticmethods delegate to the new function. Keep the method names, because existing tests call them.
- Create: `dasik/lib/upgrade/__init__.py`, `dasik/lib/upgrade/steps.py`, `dasik/lib/upgrade/runner.py`
- Test: `tests/lib/command_worker/test_su_argv.py`, `tests/lib/upgrade/test_runner.py`

**Interfaces:**
- Produces:
  - `su_argv(user: str, script: str, *args: str) -> List[str]` returns `["-", user, "-c", script, "--", "sh", *args]`. This is the third copy, so it is extracted per AGENTS.md "Reuse before you write".
  - `@dataclass(frozen=True) class UpgradeStep` in `steps.py`, with fields:
    - `domain: str`
    - `item: str`
    - `user: str`
    - `commands: Tuple[Tuple[str, Tuple[str, ...]], ...] = ()`
    - `skip: Optional[str] = None`
    - `version: Callable[[], Optional[str]] = lambda: None`
  - `@dataclass(frozen=True) class UpgradeOutcome`, with fields:
    - `item: str`
    - `status: str`, one of `"updated" | "current" | "refreshed" | "skipped" | "failed"`
    - `before: Optional[str]`
    - `after: Optional[str]`
    - `detail: str = ""`
  - `run_steps(steps: Sequence[UpgradeStep], target) -> List[UpgradeOutcome]` in `runner.py`. For each step:
    - `skip` → `skipped`, with `detail=skip`, and no command runs.
    - Otherwise read `version()` before, then run each command via `Command.execute("su", su_argv(...), target=target, check=False, stream=True, label=f"upgrade: {item}")`.
    - The first command with rc≠0 makes the step `failed`. `detail` is the command's output (stdout or stderr, whichever is non-empty) and the step's remaining commands do not run.
    - If every command succeeds, read `version()` after. Both versions known and equal → `current`; both known and different → `updated`; either one None → `refreshed`.
  - `render(outcomes: Sequence[UpgradeOutcome]) -> str` prints one line per outcome:
    - `updated` → `<item>: updated <before> → <after>`
    - `current` → `<item>: already latest (<v>)`
    - `refreshed` → `<item>: refreshed (version unknown)`
    - `skipped` → `<item>: skipped (<detail>)`
    - `failed` → `<item>: FAILED — <detail first line>`
    - Then a totals line, `N updated, N already latest, N refreshed, N skipped, N failed`. Colour is optional and left to the builder.
  - `exit_code(outcomes) -> int` returns 1 if any outcome is `failed`, else 0.

- [ ] **Step 1: Write the failing tests** in `test_runner.py`. Stub `Command.execute` with monkeypatch, returning an object with `returncode`/`stdout`/`stderr`.
  - `test_skip_runs_nothing`: `UpgradeStep(..., skip="pinned")` gives `[UpgradeOutcome(item, "skipped", None, None, "pinned")]`, and execute is never called.
  - `test_versions_differ_is_updated`: version returns `"1.0"` then `"1.1"`, rc 0 → `updated`, before `"1.0"`, after `"1.1"`.
  - `test_same_version_is_current` and `test_unknown_version_is_refreshed`.
  - `test_first_failure_stops_the_step_not_the_run`: step A has two commands and the first returns rc 1, so only one execute call is made for A and A is `failed`. Step B still runs and is `updated`. `exit_code` is 1.
  - `test_all_fail_offline`: three steps all rc 1 → three `failed`, rc 1 (Review Focus 4).
  - `test_su_argv_shape` in `test_su_argv.py`: `su_argv("u", 's "$1"', "a b")` equals `["-", "u", "-c", 's "$1"', "--", "sh", "a b"]`.
  - `test_render_lines`: one outcome of each status, asserting the exact line strings above and the totals line.
- [ ] **Step 2: Run them and see them fail.** `pytest tests/lib/upgrade tests/lib/command_worker/test_su_argv.py -v` → ImportError / FAIL.
- [ ] **Step 3: Implement** `su_argv`, `steps.py` and `runner.py`, and migrate the two `_su_argv` bodies to delegate.
- [ ] **Step 4: Run again and see them pass.** `pytest tests/lib/upgrade tests/lib/command_worker tests/lib/actions/test_uv_tools_action.py tests/lib/actions/test_ai_skills_apply.py -q` → PASS.
- [ ] **Step 5: Commit** with `git add` on the files above, message `feat(upgrade): step model, runner and summary; extract su_argv`.

### Task 2: `UvToolsAction.upgrade_steps()`

**Files:**
- Modify: `dasik/lib/actions/uv_tools_action.py`
- Test: `tests/lib/actions/test_uv_tools_upgrade.py`

**Interfaces:**
- Consumes `UpgradeStep` (Task 1).
- Produces:
  - `UvToolsAction.is_exact_pin(spec: str) -> bool` (staticmethod). True when the spec contains `==` or `===` after the name/extras.
  - `UvToolsAction.installed_version(self, user: str, distribution: str) -> Optional[str]` reads the version from the `*.dist-info` directory under `<tool dir>/<dist>/lib/python*/site-packages/`. The match is on the dist-info name normalised with `distribution()`. The directory is `graphifyy-0.6.7.dist-info` on the host, which gives `"0.6.7"`.
  - `UvToolsAction.upgrade_steps(self) -> List[UpgradeStep]`, one step per `_desired()` item, in `_desired()` order, with domain `"uv_tools"`:
    - **No target:** returns `[]`.
    - **Item not in `actual()`:** skip `"not installed — run dasik apply"`.
    - **Exact pin:** skip `"pinned"`.
    - **Otherwise:** one command, which keeps the declared python:
      - with a declared python: `('uv tool install --upgrade --python "$1" "$2"', (python, spec))`
      - without one: `('uv tool install --upgrade "$1"', (spec,))`

      The declaration wins over a receipt pinned by a manual install (spec call 5). If the guest in Task 5 shows that `--upgrade` does not override a pinned receipt, switch to `--reinstall` and pin that observation in a test.
    - `version=lambda: self.installed_version(user, dist)`.

- [ ] **Step 1: Write the failing tests.** Fake root under `tmp_path`, with a passwd line and tool dirs, using the conftest fixtures already used by `test_uv_tools_action.py`.
  - `test_installed_unpinned_gets_upgrade_command`: commands == `(('uv tool install --upgrade "$1"', ("graphifyy",)),)`.
  - `test_declared_python_is_kept`: `{"name": "inkscape_mcp", "python": "3.13"}` → `('uv tool install --upgrade --python "$1" "$2"', ("3.13", "inkscape_mcp"))`.
  - `test_exact_pin_skipped`: `"graphifyy==0.9.53"` → `skip == "pinned"`.
  - `test_range_not_skipped`: `"graphifyy>=0.9"` → has commands.
  - `test_missing_tool_skipped`: skip is `"not installed — run dasik apply"`.
  - `test_unknown_user_does_not_raise`: users `["ghost"]`, with no passwd entry and no home → steps are all skipped, no exception (Review Focus 1).
  - `test_installed_version_normalises`: dist-info `Inkscape_MCP-1.2.0.dist-info` under dir `inkscape-mcp` → `"1.2.0"`, and `semgrep[all]` resolves to the `semgrep` dist-info (Review Focus 5).
- [ ] **Step 2: Run and see them fail.** `pytest tests/lib/actions/test_uv_tools_upgrade.py -v` → FAIL.
- [ ] **Step 3: Implement** the three methods.
- [ ] **Step 4: Run and see them pass.** `pytest tests/lib/actions/test_uv_tools_upgrade.py tests/lib/actions/test_uv_tools_action.py -q` → PASS.
- [ ] **Step 5: Commit** with message `feat(uv_tools): upgrade steps for declared tools`.

### Task 3: `AiSkillsAction.upgrade_steps()` + version readers

**Files:**
- Modify: `dasik/lib/actions/ai_skills_state.py` and `dasik/lib/actions/ai_skills_action.py`
- Test: `tests/lib/actions/test_ai_skills_upgrade.py`

**Interfaces:**
- Consumes `UpgradeStep` (Task 1).
- Produces:
  - `claude_plugin_version(home: str, plugin_id: str) -> Optional[str]` in `ai_skills_state.py`. It is the `version` field of the first record in `installed_plugins.json["plugins"][plugin_id]`, falling back to `gitCommitSha`. None if absent or unreadable.
  - `skill_folder_hash(home: str, name: str) -> Optional[str]` in `ai_skills_state.py`. It is `skillFolderHash` of `LOCK_REL["skills"][name]`.
  - `AiSkillsAction.upgrade_steps(self) -> List[UpgradeStep]`, with domain `"ai_skills"`. Items come from `_desired()` in its existing order (marketplace → plugin → skill). Absent from `actual()` → skip `"not installed — run dasik apply"`. Commands per spec kind:

    | spec | commands | version |
    | --- | --- | --- |
    | marketplace, claude-code | `('claude plugin marketplace update "$1"', (name,))` | None |
    | marketplace, codex | `('codex plugin marketplace upgrade "$1"', (name,))` | None |
    | plugin, claude-code | `('claude plugin update "$1" -s user -y', (plugin@mkt,))` | `claude_plugin_version` |
    | plugin, codex | `('codex plugin add "$1"', (plugin@mkt,))`: re-add after the marketplace refresh. The guest measures whether this refreshes; if not, use `codex plugin remove` + `add` | None |
    | plugin, antigravity | the existing `_AGY_INSTALL` with `_clone_url(source)`. The guest measures whether install over an existing plugin works; if not, prepend `('agy plugin uninstall "$1"', (plugin,))` | None |
    | skill, method `skills` | `('npx -y skills update "$1" -g -y', (name,))`, **one step per (user, name)**, whatever the number of agents. The step's item is `user:skills:name` | `skill_folder_hash` |
    | skill, method `tool` | the existing `_command_for` tool command, one step per (user, agent) | None |

    The "no target" and "unknown user" behaviour is the same as in Task 2.

- [ ] **Step 1: Write the failing tests.** Use a fake home with `installed_plugins.json`, `known_marketplaces.json` and `.agents/.skill-lock.json`, and reuse the fixtures of `test_ai_skills_plan.py`. Write one test per table row asserting the exact `commands` tuple, plus:
  - `test_claude_plugin_version_reads_record`: `{"version": "63e797cd753b"}` → `"63e797cd753b"`.
  - `test_skill_folder_hash_reads_lock`.
  - `test_one_update_per_skill_not_per_agent`: one `skills` entry with agents `["codex","antigravity","antigravity-cli"]` → exactly one npx step (Review Focus 3).
  - `test_marketplace_steps_precede_plugin_steps`.
  - `test_undeclared_installed_plugin_untouched`: an installed plugin that isn't in the config gets no step.
  - `test_missing_plugin_skipped`.
  - `test_unknown_user_does_not_raise`.
- [ ] **Step 2: Run and see them fail.** `pytest tests/lib/actions/test_ai_skills_upgrade.py -v` → FAIL.
- [ ] **Step 3: Implement** the two readers and `upgrade_steps()`, reusing `_desired`, `actual`/`_scan`, `_home_of`, `_passwd`, `_command_for` and `_clone_url`. No copies.
- [ ] **Step 4: Run and see them pass.** `pytest tests/lib/actions/test_ai_skills_*.py -q` → PASS.
- [ ] **Step 5: Commit** with message `feat(ai_skills): upgrade steps for declared skills and plugins`.

### Task 4: The `upgrade` verb + journey test + docs

**Files:**
- Modify: `dasik/__main__.py`:
  - add to `_LOGGED_VERBS` and `_KNOWN_VERBS`
  - add a subparser `upgrade` with `config`, `--target` (default `/mnt`, same help as `apply`) and the common parent
  - add a dispatch branch and the `_cmd_upgrade`
- Modify: `README.md` (verb list) and `docs/config-reference.md` (a note in the `ai_skills`/`uv_tools` sections that versions move only through `dasik upgrade`). Also fix the `ai_skills_model.py` / `mcp_servers_model.py` docstrings that say updates are "the user's".
- Test: `tests/cli/test_upgrade_verb.py`

**Interfaces:**
- Consumes `UvToolsAction.upgrade_steps`, `AiSkillsAction.upgrade_steps`, and `run_steps` / `render` / `exit_code`.
- Produces `_cmd_upgrade(config_path: Path, target_root: str) -> int`:
  1. `_target_or_none`, then `_load_validated_config`, then `expand_config`.
  2. Take the `ApplyLock` (it mutates the target) and print the same busy error as `apply`.
  3. Build `ctx = ActionContext(target=target)`.
  4. Collect steps: `UvToolsAction(config, ctx).upgrade_steps()` **then** `AiSkillsAction(config, ctx).upgrade_steps()`, in that order, so graphify's skill follows the new binary.
  5. With no steps, print `Nothing to upgrade.` and return 0.
  6. Otherwise print `render(run_steps(...))`. When the config has an `mcp_servers` block, also print `mcp_servers: nothing to upgrade — npx/uvx servers resolve at start; uv-installed ones are upgraded through uv_tools`.
  7. Print `Note: dasik rollback does not revert an upgrade.` and return `exit_code`.
- The help string for the verb is `Update the declared AI tooling (ai_skills, uv_tools) to its latest version. Not undone by rollback.`

- [ ] **Step 1: Write the failing journey test** in the style of `tests/cli/test_verbs_integration.py`, using a `tmp_path` root, `--target <root>` and a mocked `Command.execute` that records the argv.
  - `test_upgrade_runs_uv_before_ai_skills`: the config has a uv tool and a claude plugin, both installed in the fake root. The recorded `su` scripts come in the order uv → claude marketplace → claude plugin, and rc is 0.
  - `test_upgrade_then_plan_is_silent`: `main(["upgrade", …])` then `main(["plan", …])`; the plan output says no changes for those domains.
  - `test_upgrade_writes_no_generation`: after `upgrade`, the generation store list is unchanged.
  - `test_upgrade_without_ai_blocks`: the output contains `Nothing to upgrade.`, rc is 0, and no `su` call is made (Review Focus 2).
  - `test_upgrade_failure_exit_1`: execute returns rc 1 for the plugin, so rc is 1 and the output contains `FAILED`.
- [ ] **Step 2: Run and see them fail.** `pytest tests/cli/test_upgrade_verb.py -v` → FAIL (`invalid choice: 'upgrade'`).
- [ ] **Step 3: Implement** the verb and update the docs/docstrings.
- [ ] **Step 4: Run and see them pass.** `pytest tests/cli -q` → PASS. Also run `dasik upgrade --help` and check it shows the help string.
- [ ] **Step 5: Commit** with message `feat(cli): dasik upgrade verb for the declared AI tooling`.

### Task 5: Guest — every verb in a VM with a basic package subset

**Files:**
- Create: `config/vm-ai-upgrade.json`. Base it on `config/vm-ai-skills.json`, with a basic package subset (memory "VM regression: trim packages"). It declares:
  - in `uv_tools`: `graphifyy`, plus one more unpinned tool and one exact-pinned tool such as `ruff==<an old release>`
  - in `ai_skills`: one `skills` entry (e.g. `pbakaus/impeccable` with one skill), one `claude-plugin`, the graphify `tool`, and the codex/antigravity entries if `vm-ai-skills.json` already gets their CLIs into the guest
  - in `mcp_servers`: one entry (it should produce the mcp note)
- Create: `scripts/vmtest/guest-ai-upgrade.sh`. Its header says why it's a guest and not a unit test: the real CLIs' update semantics, real versions, real network.
- Read before writing: `docs/agents/vm-and-real-environment.md`, `scripts/vmtest/guest-ai-skills.sh` (the network, autologin and `--no-log` gotchas), and memory `vmtest-harness-gotchas`.

The guest uses the `UPGRADE`-style convention, with each line `AIUP-<step>-RC=<n>` and a final `AIUP-DONE rc=<failures>`:

- [ ] **Step 1: Write the guest.** Each sequence:
  1. **Install:** `check` → `plan` → `apply --yes` → `plan`, which must be silent.
  2. **Make items stale on purpose:**
     - `su - test -c 'uv tool install graphifyy==<older> --force'`, then record the old version.
     - Set the skill's lock `skillFolderHash` to a bogus value and overwrite its `SKILL.md` with old content.
     - For the claude plugin: check out an older commit in its marketplace clone and reinstall if that is feasible; otherwise assert `refreshed`/`current` only.
  3. **Upgrade:** `dasik upgrade`, then assert:
     - `AIUP-UV-MOVED` — the graphify version after is greater than the old one.
     - `AIUP-PIN-HELD` — the pinned tool's version is unchanged.
     - `AIUP-SKILL-MOVED` — the lock hash is no longer bogus and the `SKILL.md` changed.
     - `AIUP-GRAPHIFY-SKILL` — the skill file matches the new binary's (e.g. re-running `graphify install` produces no diff).
     - The summary contains `updated`.
  4. **Round trips:**
     - `plan` silent (`AIUP-PLAN-SILENT`)
     - `sync --output /tmp/s.json` → `check /tmp/s.json` → `plan /tmp/s.json` silent
     - `generations` has the same count as before the upgrade (compare to the value captured earlier, not a literal)
     - `rollback --yes` to the previous generation behaves as before (rc 0); then `apply` and `plan` silent again
  5. **Block removed:** a copy of the config with the AI blocks dropped, run through `upgrade` → `Nothing to upgrade.`, rc 0.
  6. **Offline:** bring the network down, `upgrade` → rc 1 with every item `FAILED`; network back up, then `plan` silent (Review Focus 4).
  7. **Codex/antigravity measurement:** record whether re-add/reinstall moved anything, and if the table's default didn't work, fix Task 3's command + test and note the measurement in the commit.

  Every assertion that compares versions first prints both values (so that it is seen red once).
- [ ] **Step 2: Sabotage once.** Temporarily make `upgrade_steps` of uv_tools return `[]`, run the guest, and see `AIUP-UV-MOVED-RC=1`. Then revert.
- [ ] **Step 3: Run the guest for real** from a fresh qcow2, under `timeout --kill-after=60s <limit>`, and wait on the `AIUP-DONE` marker (no sleeps). Expected: `AIUP-DONE rc=0`.
- [ ] **Step 4: Commit** the config, the guest and any Task 3 adjustments, with message `test(vm): dasik upgrade through every verb`.

### Task 6: Final gates + PR

- [ ] `timeout --kill-after=60s 1800 pytest --cov=dasik` → green, coverage ≥ 80%; then `mypy dasik`, `bandit -r dasik -q` and `scripts/mutation.sh` → clean.
- [ ] `dasik plan` dry run against each sample config under `config/` that has AI blocks, to check nothing regressed. Then `dasik upgrade --help`.
- [ ] Ask the user before pushing (default mode: never push). If they approve, push and open a PR with a "How to test manually" section:
  - `dasik upgrade config/vm-ai-upgrade.json --target / -v` on a machine with the blocks applied
  - the expected summary
  - that a re-run says `already latest`
  - what happens with the blocks removed, and what happens offline

  Then run the agentic verification comment (`docs/agents/pr-verification.md`). **Never merge.**
- [ ] Report which verbs ran for real in the guest.

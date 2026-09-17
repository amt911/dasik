# dasik — Agent Guide

Declarative Arch Linux installer. Goal: behave like Nix/NixOS — describe the target system in one JSON file, run `dasik config.json`, and get that system. Running the **same** JSON again changes nothing (idempotent).

This file documents the `dasik/` package at the repo root — the active reimplementation (formerly `new/`, promoted to root in commit `3a17d00`). Ignore `archinstall/` (reference dumps) and the legacy scripts described in the repo-root `README.md`.

## Resources (local reference — bind-mounted, not committed)

`resources/` holds two read-only references plus the script that mounts them. They are **not tracked** (`git ls-files resources/` is empty) — recreate them on a new machine with [`resources/bind-mount.sh`](resources/bind-mount.sh):

| Path | What it is | Use it for |
| --- | --- | --- |
| `resources/archlinux-script-installer/` | The **old** personal install scripts (bind-mount of `~/repos/archlinux-script-installer`, its own git repo). | The imperative original that dasik reimplements declaratively. Same target system: LUKS encryption (+ pendrive unlock), ext4 or btrfs+subvolumes, snapper snapshots, GRUB/systemd-boot, KDE Plasma, NVIDIA passthrough. Read a step here to see how it was done before porting it to an idempotent Action — its own `TODO` already asks for "incremental changes on already installed systems", i.e. dasik's idempotency goal. |
| `resources/arch-wiki/` | Arch Wiki **offline HTML** mirror (bind-mount of `/usr/share/doc/arch-wiki/html/en`, from the `arch-wiki-docs` package). ~2,500 pages named by title: `Btrfs.html`, `Dm-crypt.html`, `Mkinitcpio.html`, `Systemd-boot.html`, … | Authoritative reference for the exact procedure an Action must reproduce (mkinitcpio hook order, dm-crypt cmdline flags, btrfs subvolume layout, …). gitignored via the `arch-wiki/` pattern. |

**Never enumerate or bulk-read `resources/`, and never graphify it** — the two subtrees are >12k files (~2,500 arch-wiki HTML pages, each bloated with markup). A `Glob resources/**`, a repo-wide grep that ingests them, or reading many pages at once swamps context (and the graph). Graph scope is the `dasik/` package only (~52 files); these dirs are read-only reference, not source.

**Consulting the arch-wiki (`resources/arch-wiki/`) IS fine when targeted:**

- **Read a known page** — pages are named by title, so open the exact file: `Read resources/arch-wiki/Btrfs.html`. A targeted `Read` works despite the gitignore.
- **Search for which page covers X** — scope the search to the wiki, never the whole repo: `Grep` with `path: resources/arch-wiki/`, or (the subtree is gitignored, so a plain repo grep skips it) `rg --no-ignore-vcs '<pattern>' resources/arch-wiki/` via Bash. Both return only matching lines; then `Read` only the page(s) the search points to.
- **Never** `Glob resources/**`, never read pages in bulk, never let `/graphify` ingest the tree.

Mechanism: the heavy subtrees are gitignored (`arch-wiki/`, `resources/archlinux-script-installer/`), so Grep skips them by default and Glob does too via `CLAUDE_CODE_GLOB_NO_IGNORE=false` (set in `.claude/settings.json`). Intentional, path-scoped lookups still work; only accidental mass enumeration is blocked.

## Start here

`/graphify` builds a persistent graph of the `dasik/` package (`graphify-out/graph.json`) so you can answer architecture questions without re-reading files. It is **opt-in, not per-session** — building or loading the graph costs tokens, so only reach for it when a task genuinely spans many modules.

### ⚡ graphify — on demand, not every session

Use it **only** for a cross-module architecture question that would otherwise mean opening several files — and then **query** the existing graph instead of rebuilding it:

```
/graphify query "<question>"    # architecture questions instead of opening multiple files
/graphify explain "<name>"      # locate a concept or symbol
/graphify path "A" "B"          # dependency path between two modules
/graphify --update              # refresh ONLY if you changed structure and will query again
```

Don't run `/graphify` for small, localized sessions — the fixed cost (re-extracting files + loading `graph.json` into context) outweighs the benefit. Outputs live in `graphify-out/` (gitignored). Note: `/graphify` may be **unavailable in the web/cloud environment** — don't burn turns hunting for it there.

## Agent compatibility — Codex and Claude Code

This file is `AGENTS.md`: the **one** instruction file for every coding agent in this repo. Codex reads
it directly; Claude Code reads `CLAUDE.md`, which only imports this file (`@AGENTS.md`) and holds what
applies to Claude alone. **Edit rules here, never in `CLAUDE.md`** — two copies of a rule drift apart
on the first edit, and each agent then obeys a different one.

| Concern | Claude Code | Codex |
| --- | --- | --- |
| Instruction file | `CLAUDE.md` → imports `AGENTS.md` | `AGENTS.md` (root down to the working directory) |
| Invoke a skill | `Skill` tool, or `/<skill>` | mention it (`$<skill>`), or let it trigger from its description |
| Skills on disk | `~/.claude/skills` (links into `~/.agents/skills`) | `.agents/skills`, then `~/.agents/skills` |
| superpowers | `superpowers@claude-plugins-official` (`/plugin install`) | `superpowers@openai-curated` (install from `/plugins`; that id is its key in `~/.codex/config.toml`) |
| MCP servers | `claude mcp add -s user <name> -- <cmd>` | `codex mcp add <name> -- <cmd>` (`~/.codex/config.toml`) |
| File size | imports load whole | `project_doc_max_bytes`, **32 KiB by default** — raise it when this file is bigger, or the tail is silently dropped |

- **Install shared skills once, for both agents:** `npx skills add <owner/repo> -g --skill <name>`
  writes to `~/.agents/skills` and links it for Claude Code, so both run the same version.
- **Names in this file are capabilities, not one agent's syntax.** "Invoke the `X` skill" means the
  `Skill` tool in Claude Code and a skill mention in Codex. An MCP server named here is used when it is
  registered for the agent you are running in; its absence never blocks ordinary work.
- **Modes, model caps and Git rules bind both agents.** "lite mode", "normal mode" and "modo
  desatendido" mean the same in Codex; a cap written as "no model above Sonnet" means "no model above
  the mid tier" there.
- **Claude-only commands** (`/graphify` and other slash commands that are not skills) are skipped by
  Codex unless the same capability is installed as a skill in `~/.agents/skills`.

## ⚡ superpowers — for substantial work

Prefer **superpowers** skills over ad-hoc approaches **for substantial work** — a feature, a debugging session, new logic, a review. Invoke via the `Skill` tool when a skill clearly matches. Do **not** invoke skills for trivial edits (a one-line fix, a doc/log/wording tweak, a rename), and **not** before clarifying a question with the user.

- **Process skills first** — `brainstorming` before creative/feature work, `systematic-debugging` before fixing bugs, `test-driven-development` before writing implementation.
- **Then implementation skills** — domain-specific skills guide execution.
- **Verify before claiming done** — `verification-before-completion` / `requesting-code-review` before merging.

**Exception — TDD is never skipped for being "trivial".** Any *new logic* in `models/`, `json_parser/`, `actions/` (`is_needed`/`verify`), or `command_worker/` requires the full TDD cycle even when the change is small. "Trivial" above means edits with **no new logic** (formatting, wording, renames), not small logic changes.

User instructions always take precedence over skills; skills override default behavior.

### Mode switch

- **"lite mode"** — fully disables superpowers: no skill is invoked, not even the applicability check, until **"normal mode"** is said.
- **"normal mode"** (default) — standard superpowers behavior, plus: when delegating coding work, dispatch at most 1 **implementation** agent at a time (a read-only review agent runs alongside it — see **Agent orchestration**), and never use a model above Sonnet (no Opus).
- **"modo desatendido"** (unattended mode) — the user is away and delegates autonomy: work without waiting for confirmations and make reasonable decisions yourself instead of asking. In this mode you MAY **`git push` the feature branches you create** and **open PRs via `gh`** on your own, so the work is ready for review when the user returns. The hard limits still hold and are NOT lifted: **never merge anything** (no `git merge`, no fast-forward integration, no `gh pr merge`), **never push to `main`** or any protected/default branch directly, and **never** `git push --force` / `--force-with-lease`. Deliver everything as pushed branches + PRs for the user to merge. Reverts to defaults on **"normal mode"**.

Confirm the switch briefly when it happens.

## Stack

- **Python** ≥ 3.10 — CLI tool, packaged via `setuptools` (`pyproject.toml`).
- **pydantic** — config schema + validation (`dasik/lib/models/`).
- **colorama** — colored terminal output.
- **System tooling** — wraps real commands (`arch-chroot`, `pacman`, `sgdisk`/partitioning, `ln`, `hwclock`, …) via `subprocess`. No Python bindings; it shells out.

## Commands

```bash
# install (editable, for development)
pip install -e .
pip install -e .[dev]        # pytest + pytest-cov + hypothesis + mypy + bandit
pip install -e '.[dev,mut]'  # + mutmut — REQUIRED: the pre-push hook gates on it
git config core.hooksPath .githooks   # enable the gates (once per clone)

# run against a config
dasik config/install-megamix.json          # console-script entry point
python -m dasik config/install-megamix.json # equivalent module form

# flags
dasik config.json -v        # verbose
dasik plan config.json      # the dry run: shows every change, touches nothing

# test / quality
pytest                       # unit tests (~430, all passing)
pytest --cov=dasik           # coverage (gate: 80%; needs the [dev] extra)
pytest -k is_needed          # filter by name
mypy dasik                   # static type checking (a .mypy_cache is present)
scripts/mutation.sh          # mutation gate, set_math tier (needs the [mut] extra)
```

**The four gates run on every `git push`** via `.githooks/pre-push` (pytest+coverage,
mypy, bandit, mutation) — the same set CI enforces. The hook activates `.venv`
itself and refuses to run if any tool is missing, so it can never pass by
skipping a gate. `--no-verify` bypasses it (discouraged; CI still gates).

A pytest suite **does** exist (`tests/` mirrors `dasik/lib/`, configured in `pyproject.toml`; ~430 tests, all passing — run `pytest`). See [Tests and quality](#tests-and-quality).

## How it works

```
config.json
  → JsonParser            (dasik/lib/json_parser/) — opens file, validates with pydantic JsonModel,
                           returns a plain dict via .debug()
  → preflight()           (dasik/lib/validation/) — cross-field coherence on the EXPANDED config
                           (groups without a provider, DM unit without its package, crypttab);
                           errors abort BEFORE the first mutation, warnings only inform
  → Reconciler            (dasik/lib/reconciler/) — walks the setup_actions() registry
  → Action.plan()         idempotency check: inspect current system state, diff vs config
  → Action.apply()        apply changes (only what plan() found)
  → Action.import_state() sync: capture system reality back into config

An apply that fails part-way persists what completed as a **partial** generation
(`Manifest.partial`): ownership of failed/unreached domains is carried forward
from the previous manifest, `rollback` refuses to restore it, and the next plan
still shows the divergence. It records progress, never convergence.
```

Every change runs against the **mounted install target at `/mnt`**, typically via `arch-chroot /mnt`. Actions read `/mnt/etc/...` to decide `plan()`. This is install-from-live-ISO tooling, not a config manager for the running host.

### One handler — the v3 registry

| File | Style | Idempotent? | Actions covered |
| --- | --- | --- | --- |
| `actions_handler_v2.py` | Registry + `ActionExecutor` / `Reconciler`; calls `plan()/apply()/import_state()` (and the `is_needed()/execute()/verify()` shims) | **Yes** | all ~20 actions (`setup_actions()`) |

✅ **`dasik/__main__.py` drives the v3 idempotent architecture.** The verbs — `plan`, `apply`, `sync`, `generations`, `rollback` — go through `Reconciler` (`setup_actions()` registry). The old monolithic `actions_handler.py` (`ActionsHandler`) and its no-verb `dasik <config>` fallback were **removed** (PR #151); a bare `dasik <config>` now errors and points at `dasik plan` / `dasik apply`. (`actions_handler_v2.py` still *defines* a class named `ActionsHandler`, exported as `ActionsHandlerV2` — that is the v3 one, unrelated to the deleted legacy handler.)

### The Action model (v2 — the target architecture)

`AbstractAction` (`dasik/lib/actions/abstract_action.py`) is the contract for every action:

- `name` (property) — human-readable label.
- `plan(managed) -> list[Change]` — **the idempotency check.** Inspect real system state under the target, diff against the config, and return the changes. An empty list means converged, which is what makes re-runs no-ops.
- `apply(changes) -> None` — carry out exactly what `plan()` returned.
- `is_needed() -> bool` / `execute() -> None` — the pre-v3 pair, **inherited, never overridden**: the base class answers them as `bool(self.plan(managed=[]))` and `self.apply(self.plan(managed=[]))`. Writing your own is a second implementation, and `tests/lib/actions/test_executor_shims_delegate.py` fails if you do (issue #238).
- `verify() -> bool` — optional post-check (default `True`).
- `finalize_apply() -> None` — optional, best-effort step the reconciler runs once the WHOLE apply succeeded and its manifest is saved (default no-op). For an effect that depends on something a later-registered action provides — `FirewallAction` retries a daemon reload there once `PackagesAction` has reinstalled `firewalld`. A raise is logged as a warning, never a failed apply.

`do_action()` and the `_before_check`/`after_check`/`KEY_NAME` members are **vestigial** shims (the legacy handler that used them is gone — PR #151); a couple of actions still carry them but nothing calls them. New code uses `plan()/apply()/import_state()`. `is_needed()/execute()` survive only as the base class's two-line delegation, so `ActionExecutor` and the older tests keep working without a second implementation behind them.

Actions are registered in `actions_handler_v2.setup_actions()` with `register_action(action_class, config_key, is_optional, required_fields, depends_on)`. `config_key='__root__'` means the action reads root-level config fields (e.g. `DropFilesAction`, `MkinitcpioAction`). `ActionExecutor` walks the registry in order; **ordering matters** (disk/base first, boot last) and two orderings are load-bearing:

- `PacmanHooksAction` runs in phase 1, **between the disk actions and pacstrap**: it writes the mkinitcpio neutralizer hooks, which must exist before the *first* pacman transaction or mkinitcpio clobbers dracut's initramfs (forensic report F-10).
- `SnapperAction` runs **before** `PackagesAction`: snap-pac's hooks snapshot each transaction, so the config must already exist. It installs `snapper`/`snap-pac` itself when missing (F-13).

### Adding a new config option (the common task)

To mirror "add an option to a NixOS module":

1. **Model** — add a pydantic model in `dasik/lib/models/<thing>_model.py`; wire it into `JsonModel` (`dasik/lib/models/json_model.py`) as an `Optional[...]` field (use `Optional`/`default_factory` so it stays optional — the whole point is many optional sections).
2. **Action** — create `dasik/lib/actions/<thing>_action.py` subclassing `AbstractAction`; implement `name`, `plan`, `apply`, `import_state` (and `verify` if it needs a post-check). Do NOT write `is_needed`/`execute`: they are inherited and delegate. Shell out via `Command.execute(...)` (`dasik/lib/command_worker/`), passing `run_as_chroot=True` for changes inside the target.
3. **Register** — add a `register_action(...)` call in `setup_actions()` at the correct phase, `is_optional=True` for optional sections.
4. **Config sample** — add/extend a JSON under `config/` so the option is exercised.
5. **Detectability** — prove `plan` sees it *and* `sync` reads it back, in BOTH directions (see the two rules below).

### Every feature must be detectable by `plan`/`apply`

**A declared block that converges but never shows up in `dasik plan` is a bug**,
even when `apply` does the right thing: you cannot tell "already applied" from
"dasik ignores this block", and `apply` then changes the machine in ways the dry
run never announced.

Two things make this easy to get wrong:

- **A feature usually rides another domain.** `sysrq` has no `[sysrq]` line in
  the plan — it appears as `+ [kernel_cmdline] install sysrq_always_enabled=1`,
  the `cpu` block as `amd_pstate=active` on the same domain plus a package, a
  unit and `/etc/default/cpupower`, `reflector` as a `[files]` entry plus
  `reflector.timer`. That is fine. Being invisible *everywhere* is not.
- **Silence is ambiguous.** A quiet plan means "already converged" — which is
  exactly what a feature nobody looks at also produces. On a machine whose boot
  entry already carried `sysrq_always_enabled=1` (the old imperative installer's
  `enable_reisub`) the silence was correct, and indistinguishable from a bug.

So every feature needs BOTH assertions, and the disable direction where it
exists: **missing on the target ⇒ a change is planned; present ⇒ no change;
declared off but owned in the manifest ⇒ REMOVE.** An unowned parameter someone
else set is deliberately left alone. The matrix for issue #173 block A lives in
[`tests/lib/test_feature_detectability.py`](tests/lib/test_feature_detectability.py) —
extend it when adding a feature.

### …and capturable by `sync`

The same rule on the way back: **a feature `apply` converges but `sync` cannot
read is a one-way street.** Capture the machine, re-apply the captured config,
and the feature silently disappears — which is exactly how `sysrq`, `cpu` and
`reflector` behaved until they got an `import_state`.

Two failure modes, both silent:

- **Nothing captures it.** `reflector` wrote `/etc/xdg/reflector/reflector.conf`
  and nothing read it back (file discovery only scans `DropFilesAction._SECTIONS`,
  and /etc/xdg is not one of them), so the mirrorlist policy was lost outright.
  A feature delivered purely by an expand toggle has no owner on the way back
  until you give it one — see the CAPTURE-ONLY actions `CpuAction` /
  `ReflectorAction` (`plan()` deliberately empty; they exist so
  `Reconciler.sync`, which only visits v3 actions, reaches them).
- **It captures as noise instead of as itself.** `sysrq_always_enabled=1` and
  `amd_pstate=active` came back as hand-set `kernel_cmdline` entries, so the
  captured config described the same policy without ever growing the block that
  explains it. Parameters a block owns are subtracted by NAME in
  `KernelCmdlineAction.import_state`, whether or not the config declares the
  block.

Assert per feature: **machine has it ⇒ the declaration is captured; machine
lacks it ⇒ nothing is invented (and a declared flag is CLEARED, since sync
reports reality); the captured config validates and re-plans to nothing.** The
last one is the real invariant — `sync` → `plan` must be silent. The matrix
lives in [`tests/lib/test_feature_sync_capture.py`](tests/lib/test_feature_sync_capture.py).

Watch out for two legitimate reasons a key is absent from a synced config:
`subtract_contributions` strips whatever the *seed's* toggles already derive
(`systemd-boot-update.service` never gets listed because `bootloader: sd-boot`
re-derives it), and `_cmd_sync` drops newly-added empty values. Assert
reproducibility (`expand_config(captured)`), not literal presence.

### …and exercised through EVERY verb before it is called done

**A feature is not finished until it has been driven through all of
`check`, `plan`, `apply`, `sync`, `generations` and `rollback`.** The unit suite
passing is not the same thing: each verb enters the code by a different door, and
the bugs found this way were invisible to a green suite.

| Verb | What only this verb proves |
| --- | --- |
| `check` | the sample config still validates, and — the one that keeps being missed — **a config `sync` just produced does too**. A capture the tool then refuses is a broken capture. |
| `plan` | the domain is visible at all, in both directions (missing ⇒ planned, present ⇒ silent), and it **converges**: plan → apply → plan must be empty the second time. |
| `apply` | the change is written where it was announced, and re-running writes nothing. Never against real hardware — assert the intent (mocked `Command.execute`, a scratch root). |
| `sync` | the feature reads back as its own block, and nothing is invented on a machine that lacks it. |
| `generations` / `rollback` | the manifest records the domain, and a restored generation re-plans to nothing. `rollback` re-applies: same destructive limits as `apply`. |

The pairs matter more than the verbs alone — run them as round trips:
**`sync` → `check` → `plan` must end in silence**, and **`plan` → `apply` →
`plan` must too.** Real defects that only these round trips catch:

- a domain that plans a change, applies it, and plans the *same* change forever
  (a systemd drop-in that another file outranks — `apply` reported success every
  time);
- a `sync` that reported the config back instead of the machine, so the captured
  file described a setting nobody had applied;
- a `sync` whose output `check` then rejected, because the capture omitted the
  package behind an enabled unit;
- an *undeclared* domain planning a destructive MODIFY — `ln -sf
  /usr/share/zoneinfo/None/None`, or commenting out every locale — reached by
  dropping a block a previous generation owned.

That last one is the general trap: **also exercise the domain with its config
block removed.** The reconciler hands an action its *empty* config when a
previous generation owned the domain, and an empty config is not the same thing
as "the empty value".

`apply` and `rollback` are destructive and must never run for real; drive them
against a scratch root or with `Command.execute` mocked, and say plainly in the
verdict which verbs were exercised for real and which were asserted.

### Running commands

Use `Command.execute(cmd, args, run_as_chroot=False)` (`dasik/lib/command_worker/command_worker.py`) rather than calling `subprocess` directly — it locates the binary (raising `CommandNotFoundException`) and optionally prefixes `arch-chroot /mnt`. Custom exceptions live in `dasik/lib/exceptions/exceptions.py`.

## Tests and quality

A pytest suite exists (~430 tests under `tests/`, mirroring `dasik/lib/`). These rules govern both existing tests and new ones.

- **pytest** for unit tests, **pytest-cov** for coverage (a `dev` extra in `pyproject.toml` — `pip install -e .[dev]`; not always installed, so `--cov` may be unavailable until you do). Config: `pyproject.toml` (`[tool.pytest.ini_options]` + `[tool.coverage.*]`).
- **pytest monkeypatch / unittest.mock** to stub system access (`Command.execute`, `pathlib.Path`, `subprocess`). Never touch a real disk in a test.
- File convention: `test_*.py` under a top-level `tests/` directory mirroring `dasik/lib/` layout.
- **Coverage gate: 80%** (statements/branches, `fail_under = 80`). Don't lower the gate — exclude untestable modules in config with a written justification instead.
- **Mutation gate: cero supervivientes reales** en el tier `set_math` (`scripts/mutation.sh`), bloqueante en `pre-push` y en CI. La plantilla fija un **suelo del 60%**; este repo va por encima porque el scope es lógica de decisión pura y pequeña, y ahí un mutante vivo es un test que falta, no ruido. El número es un **trinquete**: sube o se queda, nunca baja al suelo para dejar pasar un push. Si la corrida se vuelve pesada, se estrecha el **scope** (el tier 2, `--reconciler`, ya está separado por eso), nunca el criterio. Los mutantes **equivalentes** se documentan por firma de diff y se dejan vivos a propósito — perseguir el 100% literal no es el objetivo.

```bash
pytest                       # unit tests
pytest --cov=dasik           # coverage
pytest -k is_needed          # filter by name
```

### What to test per folder

| Folder | What | Status |
| --- | --- | --- |
| `dasik/lib/models/` | pydantic models — accept valid configs, reject invalid. Deterministic, no mocks | ✅ Covered |
| `dasik/lib/json_parser/` | `JsonParser` against fixture JSON files; assert parsed dict + bad-file handling | ✅ Covered (`tests/lib/json_parser/test_json_parser.py`) |
| `dasik/lib/actions/` | `is_needed()` / `verify()` decision logic — monkeypatch `/mnt` paths & `Command.execute`, assert the boolean. **Highest value: these guarantee idempotency** | ✅ Covered (largest area) |
| `dasik/lib/command_worker/` | `Command` — binary lookup, chroot prefix, `CommandNotFoundException`. Mock `subprocess.run` / `shutil.which` | ✅ Covered |
| `dasik/lib/reconciler/`, `dasik/lib/state/`, `dasik/lib/target/` | v3 `plan`/`apply`/`sync`, set-math, manifests/generations, config writer | ✅ Covered |

### TDD — required for new logic

For new code in `models/`, `json_parser/`, `actions/` (`is_needed`/`verify`), `command_worker/`:

1. **Red** — write a failing test that describes the behavior.
2. **Green** — implement the minimum to pass.
3. **Refactor** — clean up under green tests.

Exceptions (TDD not required):

- Pure output/formatting changes (colorama strings, log wording).
- `execute()` bodies that only shell out to destructive tooling (`pacman`, partitioning, `arch-chroot`) — cover the *decision* (`is_needed`) instead; assert `Command.execute` was called with the right args via mock, don't run it.
- Spikes/exploration — but add tests before merging.

Rules:

- **Don't run `execute()` against real hardware** — partitioning/`pacman`/`arch-chroot` are destructive. Mock `Command.execute`.
- **Test over mock**: exercise real code with minimal stubs; don't mock entire modules.
- **Don't lower the 80% gate** to ship — exclude untestable modules in config with a written reason.

### Operative conventions

- **Shared fixtures in `conftest.py`** — a fake `/mnt` tree, a mocked `Command.execute`, sample config dicts. Define once; don't redefine per test.
- **Split by aspect** when a test file exceeds ~300 LoC: `test_<thing>_needed.py`, `test_<thing>_errors.py`.
- **Exclude with justification** in config, never silently. Example:

  ```toml
  # execute() only shells out to arch-chroot/pacman — covered via is_needed + mocked Command
  [tool.coverage.run]
  omit = ["dasik/lib/actions/disk_partition_action.py"]
  ```

## Quality beyond coverage

**Coverage measures how much code runs, not whether it's correct.** This is especially treacherous with AI: it tends to write the test *and* the code in one move, so if it misread the requirement, both encode the same mistake and the test passes happily. 80% coverage with weak asserts is a false sense of security. For dasik the stakes are literal — a covered-but-wrong `is_needed()` breaks idempotency or wipes a disk. These gates attack that blind spot.

- **Mutation testing** *(highest priority)* — **mutmut** or **cosmic-ray** inject deliberate bugs (`>` → `>=`, drop a line, flip a boolean) and check some test fails. A surviving mutant means the code is *covered but not verified*. Target the **idempotency logic** first: the `is_needed()` / `verify()` methods in `dasik/lib/actions/` and the reconciler set-math in `dasik/lib/reconciler/`. A flipped comparison there is exactly the AI-shaped bug that passes coverage but re-runs destructively.
- **Property-based testing** *(highest priority)* — **Hypothesis**. Define invariants and let it generate hundreds of cases including weird boundaries: "parsing then re-serializing a config round-trips", "reconcile(current, current) yields an empty plan (a no-op)", "applying a plan twice is a no-op". This is the automated proof of the idempotency promise.
- **Runtime boundary validation** — **pydantic** already guards the config boundary (`dasik/lib/models/`); keep every new top-level field modeled and validated there rather than reaching into raw dicts. Untrusted input (the user's JSON, `/mnt/etc/...` file contents) must cross a validated boundary, not be trusted by shape.
- **Strict types + static analysis** — **mypy** (a `.mypy_cache` is already present — run `mypy dasik` and keep it clean) plus a SAST pass (**Semgrep** or **Bandit** for Python). SAST matters here because this tool shells out constantly: watch for command injection when building `Command.execute` argument lists from config, and never interpolate untrusted strings into a shell.
- **Smoke / dry-run validation** — there is no live-ISO in CI and installs are destructive, so the "does it actually boot" check is: run `dasik plan config/<file>.json` (read-only — the dry run) against the real sample configs under `config/` and confirm parsing + planning complete without exceptions. Never run a real install against the dev machine's disks.
- **Dependency auditing** — AI invents non-existent packages ("slopsquatting") and pulls vulnerable versions. Runtime deps are intentionally two (`pydantic`, `colorama`); use `pip-audit`, and verify every new dependency actually exists and is the one you think it is before adding it.

**Process rule (worth more than any tool): don't let the AI define the acceptance criteria.** You write or review the important test cases yourself — at least the key asserts and the requirement's edge cases (does a re-run really no-op? does the destructive flag really gate?) — and have the AI implement against them. That breaks the loop where the same misunderstanding lives in both the test and the code. Mutation testing is the automated backstop; the judgment about *what the system should do* stays yours.

Priority by immediate payoff: **mutation + property-based testing first** on the idempotency logic, then **keep mypy clean and a couple of `dasik plan` smoke checks**.

## Agentic PR verification (MANDATORY on every PR)

**Every PR MUST be verified end-to-end before merge, and the verdict MUST be posted as a PR
comment** via `gh pr comment`. A headless agent (`claude -p`, local) builds the package and drives
the CLI, then posts the result; it **never merges** — it waits for you. Running the pass and
posting the verdict comment is **not optional**. It catches what unit tests miss: a broken entry
point, a config that pydantic no longer parses, an action wired into the wrong handler, a verb
that crashes before it ever reaches `is_needed()`.

- **Engine.** CLI, no browser/server → **build + smoke**: in a scratch venv, `pip install -e .[dev]`,
  then exercise the CLI's non-destructive verbs against a real tracked sample config (e.g.
  `dasik plan config/install-megamix.json`, `dasik generations`, `dasik hash-password`), plus the
  entry point (`dasik --help`, `python -m dasik --help`). Configs touching `disks`/`arch-chroot`
  fail fast off Arch hardware with `CommandNotFoundException` (expected) — escalate to a
  disposable target (loopback→`nspawn`→qemu, lightest that fits) **only** for what the suites
  can't cover (real disk ops, a booting install); never a bare runner or real hardware. See
  [`docs/testing-without-a-vm.md`](docs/testing-without-a-vm.md). **Never run `apply`/`rollback` for real** —
  they partition disks and run `pacman`; assert intent via exit code/output/mocked
  `Command.execute`, never against real hardware. Attach the captured output to the verdict
  comment.
- **Two layers.** The pytest suite (Coverage gate ≥80%, `pytest --cov=dasik`) stays the hard merge
  gate; the agentic pass is advisory and never vetoes a merge on its own — but running it and
  posting the verdict comment is mandatory.
- **The verdict reads structure too.** Besides driving the CLI, it names what the diff does to the
  [Design principles](#design-principles--solid-applied-with-judgement): a new violation (an action's
  `plan()`/`is_needed()` importing `subprocess` directly instead of going through `Command.execute`,
  one more branch in a growing `if`/`elif` chain that `setup_actions()`'s registry should absorb
  instead) or a new speculative abstraction. Findings, not a veto — like the rest of the pass.
- **Hard limits.** The verdict awaits your close; the agent never merges. Scope `--allowedTools`;
  use `--dangerously-skip-permissions` only in a controlled local env — never against real
  hardware/disks (see *Safety*, below).

## Every change gets a VM

**Any change under `dasik/` is driven in a QEMU guest before it is called done.**
Not "when it looks risky", not "when it touches disks" — every change. The
harness is `scripts/vmtest/qemu.sh` (see [docs/vm-testing.md](docs/vm-testing.md));
the usual pair is

```bash
export DASIK_VM_ISO=/path/to/archlinux-x86_64.iso
export DASIK_VM_WORKDIR=/var/tmp/dasik-vmtest DASIK_VM_RAM=4096
scripts/vmtest/qemu.sh install-driven config/vm-<feature>.json
scripts/vmtest/qemu.sh drive $DASIK_VM_WORKDIR/vda.qcow2 guest-<feature>.sh <MARKER>-DONE
```

A change that has no config exercising it gets one (`config/vm-*.json`), and a
guest script that asserts the behaviour end to end (`scripts/vmtest/guest-*.sh`,
echoing `<MARKER>-…=rc` lines so the log is greppable). Start the VM from a
**fresh** qcow2 whenever an earlier run mutated it, or the next run is testing a
machine the previous test broke.

**Why this is not negotiable.** The bugs this repo has actually shipped were all
invisible to a green suite and to a careful reading, and every one of them was
caught by a guest:

| Bug | The suite said |
| --- | --- |
| `sync` dispossessed a declared pacman group, so removing it from the config removed nothing | 2860 passing |
| a systemd drop-in another file outranked: planned, applied, planned again, forever | passing |
| a `sync` whose output `dasik check` then rejected | passing |
| `_process_disk` skipping the format on a fresh disk → empty fstab, aborted install | passing |
| the initramfs written under a filename the bootloader does not look for | passing |

The unit suite proves the decision; the guest proves the machine. Say plainly in
the verdict which verbs ran for real in the guest and which were asserted with
mocks. `apply` inside the guest is fine — it only ever touches the guest's own
`/dev/vda`; **never** run it against the host.

## The vocabulary for those checks — and what no unit test can prove

*Every change gets a VM* (above) says **what** to do. This says **what the checks are called**, so
you can ask for one by name, and **the rules for writing one that is worth trusting**.

The reason the guest exists is that `~430` passing tests cannot see the machine. `Command.execute` is
monkeypatched, so `pacman`'s real output format, `sgdisk`'s real partition table, `arch-chroot`'s
real mount semantics and `hwclock`'s real effect never happen. The suite proves the *decision*;
nothing in it proves the *system*. Every bug in the table above lived in that gap.

| Name | What it means here |
| --- | --- |
| **E2E / in-guest acceptance test** | Drives the real `dasik` inside a QEMU guest against that guest's own `/dev/vda` and asserts on observable results — exit codes in the `<MARKER>-…=rc` lines, the fstab that was written, the packages actually installed, the bootloader entry that exists — never on internals. That is `scripts/vmtest/qemu.sh drive` plus a `guest-*.sh`. |
| **Contract test** | Checks that assumptions about **the system tooling you shell out to** actually hold — precisely what the mocks encode instead of test. Does `pacman -Qgq` still print that shape? Does `pacman -S --needed` exit 0 when nothing is needed? Does `sgdisk` renumber partitions the way the code assumes? Does `arch-chroot` inherit the mounts you think? A mock is a written-down guess about another program's behaviour; the contract test is the measurement, and it can only be taken in the guest. |
| **Mutation testing** (in-guest: by hand) | Revert the fix, re-run the guest script, confirm it goes red, restore. `mutmut`/`cosmic-ray` automate this for the pure decision logic; against a machine you do it manually. **A check that has never failed has not been tested.** |
| **State-invariant test** | Asserts a relationship **between two stores** that no unit test owns — and this is the family of bug this repo keeps shipping. A manifest/generation must agree with the system it describes; a `sync` must produce output that `dasik check` accepts; a declared group removed from the config must actually be dispossessed; an initramfs must be written under the filename the bootloader looks for. Each side is individually correct; the pair is what breaks. |
| **Test pollution / isolation leak** | A test writing to real state. Here it is not flakiness, it is a destroyed machine: `apply` outside a guest touches the host's disks. Also more quietly — reusing a qcow2 an earlier run mutated means the next run is testing a machine the previous test broke. Start from a **fresh** qcow2. |

### Rules that came out of real bugs, not theory

- **Prove every new check can fail before you trust it green.** Revert the fix, re-run the guest,
  watch the `<MARKER>-…=rc` line go non-zero, restore. This applies to unit tests written after the
  fact *and* to guest scripts. A green you have never seen turn red is not evidence — and the whole
  reason the guest exists is that the suite was green for every bug in the table above.
- **Never assert on a count you cannot predict.** "More than 5 packages installed", "the fstab has 4
  lines", "the drop-in directory has 2 files" — every one of those passes against a genuinely broken
  build as soon as the config or the mirror changes, because the magnitude depends on the input, not
  on the bug. Assert the **invariant**: the second `apply` is a no-op (idempotency is what
  `is_needed()` exists for); `sync` output survives `check`; the fstab names the UUID that
  `blkid` reports; the initramfs path is the one the bootloader entry references.
- **A manifest, generation or state marker must die with the data it describes.** A generation kept
  after the system it describes was re-partitioned makes the next `plan` reason about a machine that
  no longer exists — no crash, no log, and the diff looks plausible.
- **Never let a check touch the host.** `apply` runs **only** inside the guest, against the guest's
  own `/dev/vda`. Anything machine-global a test touches gets restored in a teardown that runs even
  when the test fails.
- **Say plainly which verbs ran for real.** In the verdict, separate what executed in the guest from
  what was asserted with mocks. A guest run that only exercised `plan` is not evidence about `apply`.

## Safety — this tool is destructive

- It **partitions disks, formats filesystems, and runs `pacman`** against `/mnt`. Treat any code path that reaches `execute()` as capable of wiping a disk.
- Disk actions are gated by a `format` flag in config — keep destructive steps behind explicit opt-in flags; never make formatting the default.
- When testing or demoing, use configs with destructive flags off, or run validation-only paths. Never run real installs against the dev machine's own disks.

## Debugging — keep the loop from running away

What a bug costs is not the fix. It is how many times you go around
`build → deploy → reach the state → observe` before you know what to fix, times what one lap costs.
Every rule below carries the number it came from; the ones this repo has not measured are marked
`<!-- pendiente de medir -->` until someone does.

- **Measure before you ablate.** Ablation costs one lap per hypothesis and answers yes/no;
  instrumentation costs one lap total and answers *what is actually happening*. **Measured: 28
  ablations over 1 h 42 min moved nothing; one 13-min batch of probes changed the question and the
  bug fell on the next round.** The rule that came out of it: **if a pipeline completes every phase
  with non-empty output, the output exists** — stop asking "why doesn't it appear" and ask "where
  does it appear". Here that pipeline is `load/parse → validation → transform → write the result`.
- **Budget the lap, then attack the dominant term.** Time the four phases once and write the real
  seconds in; one dominates and the rest are noise. **If a bug needs more than three reproductions,
  write the shortcut before the fourth** — here that means
  a fixture that lands the tree/state already built, a VM snapshot, or a dev subcommand that skips
  the earlier steps.
  Commit it as `<scripts/repro-<bug>.sh>` and name it in `docs/FINDINGS.md` (create it from the starter kit if this repo has none yet).

  | Lap phase | Command here | Measured |
  | --- | --- | --- |
  | build / install | `<pip install -e . · none>` | `<n s>` |
  | deploy | `<copy to the VM · none>` | `<n s>` |
  | reach the state | `<sample config · VM at the starting state>` | `<n s>` |
  | observe | `<stdout · resulting files · journalctl>` | `<n s>` |

- **A review finding is not a reproduction.** Whoever reviewed read the code; they did not run it.
  Reproduce it yourself before sending anyone to fix it, and **if the implementer says they cannot
  reproduce it, believe the implementer** — one of them has the thing running. **Measured: 1 h 25 min
  chasing a bug that did not exist.**
- **A test that refuses to go red is data, not a failure.** The fourth failed attempt to pin down
  that non-existent bug is what uncovered the real one, pointing the opposite way. "I cannot make
  this fail" is a result and it gets reported; a green test papered over it throws the signal away.
- **Before demanding a red, ask whether the mechanism can produce one.** If another layer masks the
  effect there will be no red however hard you push, and the time goes into the test instead of the
  bug. **Measured: over 1 h on two structurally impossible reds.**
- **Assertions that are inert by construction** — none of these shows up as a failure, a warning or
  a coverage drop. **Every assertion is watched failing once**, and expected values are written by
  hand:

  | Inert by | What it looks like here |
  | --- | --- |
  | `assert` under `-O` | `python -O` / `PYTHONOPTIMIZE` strips them from the bytecode: the test passes checking nothing |
  | an unawaited coroutine | leaves a `RuntimeWarning: coroutine was never awaited` and the test stays green |
  | snapshots with `--snapshot-update` | `syrupy` / `pytest-snapshot` record the current output as the reference |
  | `MagicMock` | returns another `MagicMock` for any attribute: everything is truthy and everything looks called |
  | expectation computed alike | the expected value is recomputed with the same function under test |

- **Verify the resource limit reaches the process doing the work.** A job wrapped in a memory scope
  can hand the work to a daemon or worker pool living outside it, and the tool still reports the
  limit as applied — over a process that is idle. Check the **worker's** cgroup
  (`cat /proc/<worker-pid>/cgroup`), not the scope's.
- **Environment claims get measured or they don't get made.** "That heap sounds low" produced a
  recommendation that was simply wrong; measuring it — three runs per setting, not one — gave a
  **0.4% difference, below the run-to-run variance**. No performance tuning lands without a
  before/after over more than one run.
- **Locate which layer owns a rule before deciding which side gives.** A rule that lives in one
  layer and isn't shared by the others fails where the assumption breaks, not where it is written,
  which is why the fix keeps landing in the innocent layer.
- **Replacing a component can remove capabilities in silence.** When you swap one API for another,
  enumerate what the old one did that the new one does not, and say it in the PR — nothing will fail
  to compile. An optional parameter that defaults to off is a capability that only exists if the
  caller remembers it.

## Agent orchestration — parallel where it's free, batched where it's yours

Delegating to agents moves the bottleneck to **scheduling**: what waits on what, what each agent
re-derives, and which decisions quietly stop being yours. Same convention — every rule carries its
measured number.

- **Review is not on the critical path.** Reviewing task N and starting N+1 are independent when
  they touch different files. Serialized, review is **10-15% of the wall clock** and blocks
  everything behind it; in parallel it is free. **On receiving an implementation report, dispatch
  its review and the next implementation in the same turn.** This is the one exception to
  *"at most 1 agent at a time"*: the cap counts **implementation** agents — a review agent reads and
  reports, it writes nothing, so it cannot race the implementer. **The exclusive resource here is:**
  the VM or target directory, and any real database or block device
  — at most one agent touching it.
- **Keep one shared facts file.** Every fresh agent re-derives the same things: the real selector,
  which fake exists, what that helper accepts. Keep `docs/FACTS.md`, have each agent append to it
  when it finishes, and hand it to the next one in its dispatch. Only **facts verified against the
  repo or the running system**, with how they were verified. It is not the gotchas log: that holds
  what is *not* deducible from the code and outlives the branch; this holds what is perfectly
  deducible and merely expensive to look up, and it may die with the branch.
- **Plans carry contracts, not literal code.** The agent **trusts** the code in the plan; code you
  never compiled is an error wearing authority. **Measured: 4 wrong blocks, 15-40 min of detour
  each.** Write exact names, exact signatures and "mirror the shape of `<X>`" — claims the agent can
  check against the repo — and reserve literal code for what you have run.
- **Batch the discretionary decisions.** Work that appears along the way — a capability being
  dropped, a missing script, an adjacent bug — added **5-6 h of 15**. Each was justified; deciding
  them on the fly is what takes them away from you. Accumulate and ask **once per batch, with the
  estimated cost**. In **"modo desatendido"** the batch goes in the PR body instead, with its costs.
- **What never gets cut.** Review was **1.5 h of 15** and found a `create()` silently discarding
  fields, a 404 caused by SQL deduplication, a silent merge that corrupted data, a
  delete-and-recreate with no transaction, and several inert assertions. **Cutting review does not
  give time back; it defers it to production.** Cut reproduction (write the shortcut) and
  serialization (dispatch review in parallel) instead.

### Day one — the numbers that fill the blanks

1. **The lap** — time `build → deploy → reach the state → observe` once and write the seconds into
   the table above. The dominant phase gets the shortcut script; the rest stay unoptimized.
2. **The exclusive resource** — confirm the one named above is really the only one.
3. **The inert assertions** — break one assertion on purpose and run the suite; anything still green
   is inert. Then prune the table above to what this stack can actually produce.

## Design principles — SOLID, applied with judgement

SOLID is a list of **symptoms to look for**, not a pattern to apply. Every one of the five exists to
keep a change local: the useful question is *how many files does the next plausible change touch, and
how many of them do you have to understand first?* Applied by rote it produces the opposite — an
interface per class, a factory for one product, an eight-file feature — so here it is bounded by YAGNI
and by the *Reuse before you write* rule in [Working rules](#working-rules).

| Principle | Checkable smell | Usual fix |
| --- | --- | --- |
| **S — Single responsibility**: one reason to change | the description needs "and"; the file changes in PRs about unrelated features; a test mocks things unrelated to what it asserts; a component both fetches and lays out | split along the reason to change — IO, decision, presentation |
| **O — Open/closed**: extend without editing | adding a case edits a growing `switch`/`if` chain in several places; one boolean prop per variant | a variants map, strategy, slot or registry — introduced at the second real case, not the first |
| **L — Liskov substitution**: subtypes keep the contract | an override throws "not supported"; callers check the concrete type before calling; a variant drops the base's disabled, focus or semantics | narrow the base contract, or stop inheriting and compose |
| **I — Interface segregation**: clients see only what they use | a fake implements methods the test never calls; a whole entity is passed to read two fields; a `Service` with fifteen methods | split by client need; pass the fields, not the bag |
| **D — Dependency inversion**: policy does not import mechanism | domain or UI code imports `fetch`, the ORM, Retrofit, `Date.now()` or `fs` directly; a unit test needs a network or a database | depend on a port the caller owns (interface, function, hook); wire the adapter at the edge |

### Where the seams go, per stack

| Stack | Seams |
| --- | --- |
| Python | pure functions for decisions; IO at the edges (CLI entry point, adapters); a `Protocol` only when a second implementation or a test fake needs it |
| Shell | one function per job; side effects (`rm`, package managers, network) isolated in named functions a dry-run flag can skip |

dasik's own architecture already draws this seam: `plan()`/`is_needed()` are the pure decision (diff
config against real system state, touch nothing), `apply()`/`execute()` are the IO at the edge (via
`Command.execute`, never raw `subprocess`), and `import_state()` is the inverse IO edge for `sync`. A
new action that mixes the diff logic and the shell-out in one method has lost that seam.

### Where SOLID stops

- **No interface, abstract class or factory without one of:** a second real implementation, an IO
  boundary (network, database, filesystem, clock, randomness, OS), or a test that cannot be written
  without the seam. "We might swap it later" is not on the list.
- **Reuse first beats speculative extension points:** add the parameter to the existing thing before
  inventing a plugin system for it.
- **Speculative abstraction is a review finding**, exactly like a violation: an interface with one
  implementation and no IO behind it gets inlined.
- **Refactor toward SOLID when a change hurts**, in the PR that felt the pain — not as a drive-by
  rewrite of code nobody is changing.
- **Repos without their own executable code** (packaging, fonts, LaTeX, configuration data,
  byte-matching decompilation) state the exemption in one line under *Working rules*. Doesn't apply
  here — `dasik/` is ~450 files of executable Python plus the shell scripts under `scripts/` and
  `.githooks/`.

## Working rules

- **Use superpowers skills whenever they apply** — invoke via `Skill` before acting; process skills before implementation skills.
- **New dependencies: ask first, then install** — adding a package is allowed when the task
  genuinely needs one, but ask before installing (which package, why, what it replaces) and wait
  for the go-ahead. Runtime deps are intentionally minimal (`pydantic`, `colorama`), so check what
  is already there first.
- **Reuse before you write** — search `dasik/lib/` before adding a helper, model or action (`rg -n "^(def|class) " dasik/`). A new action is assembled from what exists — `Command`/`command_worker` for shelling out, the `models/` pydantic types, the errors module, the existing `is_needed`/`verify`/`import_state` shapes — never from a private copy of them. Two actions that both parse the same system output are one helper waiting to be extracted; do it at the third copy, in the same PR, migrating the call sites. A second copy of a state-detection routine is how `plan` and `sync` start disagreeing about the same machine.
- **SOLID where it pays, not by rote** — split actions and models by reason to change, extend
  through the `setup_actions()` registry rather than a growing `if`/`elif` chain, keep every
  action's `plan()`/`apply()`/`import_state()` honest with `AbstractAction`'s contract, keep config
  models and action constructors narrow, and push IO (`subprocess`, `/mnt`, the filesystem) behind
  `Command.execute` and the pydantic models boundary. No abstraction without a second
  implementation, an IO boundary or a test seam. See
  [Design principles](#design-principles--solid-applied-with-judgement).
- **TDD by default** for new logic (`models/`, `json_parser/`, `actions/` `is_needed`/`verify`, `command_worker/`). Don't merge logic without tests.
- **Don't lower the coverage gate** — exclude untestable modules in config with a written justification instead.
- **Preserve idempotency** — any new action must implement a real `is_needed()` that reads system state. A re-run of the same JSON must be a no-op.
- **Every feature must be detectable by `plan`** — missing ⇒ planned, present ⇒ silent, owned-but-undeclared ⇒ removed. Assert all of them; a quiet plan is not evidence. See [Every feature must be detectable by `plan`/`apply`](#every-feature-must-be-detectable-by-planapply).
- **…and capturable by `sync`** — every feature needs an `import_state` that reads it back as its own block, and `sync` → `plan` must be a no-op. A feature delivered only by an expand toggle has no owner on the way back until you add one. See […and capturable by `sync`](#and-capturable-by-sync).
- **…and exercised through EVERY verb before you call it done** — `check`, `plan`, `apply`, `sync`, `generations`, `rollback`, as round trips (`sync` → `check` → `plan` silent; `plan` → `apply` → `plan` silent), *including with the config block removed*. A green unit suite is not this. `apply`/`rollback` never run for real — scratch root or mocked `Command.execute`, and say which verbs were real. See […and exercised through EVERY verb](#and-exercised-through-every-verb-before-it-is-called-done).
- **Keep sections optional** — config has many optional blocks (disks, kvm, cups, wireguard, …), not just disks. New top-level fields should be `Optional`/defaulted in `JsonModel`.
- **The legacy handler is gone** — the monolithic `actions_handler.py` and the no-verb `dasik <config>` fallback were removed (PR #151). Put all behavior in the v2/v3 registry/action path (`setup_actions()` + `plan()/apply()/import_state()`).
- **Entry point is on v3** — `__main__`'s verbs (`plan`/`apply`/`sync`/`generations`/`rollback`) use the reconciler. A bare `dasik <config>` (no verb) is rejected with a pointer to `plan`/`apply`.
- **Never run `execute()` against real hardware** — partitioning/`pacman`/`arch-chroot` are destructive. Mock `Command.execute` in tests.
- **Every change to `dasik/` is tested in a VM before it is called done** — see [Every change gets a VM](#every-change-gets-a-vm). Not "when it seems risky": every change. The green suite is not the evidence.
- **Instrument before you ablate, budget the lap, and dispatch review in parallel** — a pipeline that completes with non-empty output produced output; more than three reproductions means you owe a shortcut script; a review finding is not a reproduction; and the review of task N runs alongside the implementation of N+1. See **Debugging** and **Agent orchestration** above.

## Git & GitHub

- **Commits and branches OK** — create commits and new branches whenever it makes sense, without asking first.
- **Never push** *(default)* — no `git push` under any circumstance, and absolutely never `git push --force` / `--force-with-lease`. Leave pushing to the user. **Exception:** when **"modo desatendido"** is active, you may push the feature branches you create (never `main`/protected branches, never force) so PRs are ready for review.
- **Never merge — no permission** — you do NOT have permission to merge anything into any branch, nor to merge any pull request. No `git merge`, no fast-forward integration, no `gh pr merge`. Leave every merge (branches and PRs alike) to the user. This holds in every mode, **including "modo desatendido"**.
- **GitHub via `gh`** — if the `gh` CLI is available, you may open pull requests, issues, and similar (comments, labels, etc.). These don't require pushing on your part beyond what `gh` itself does for an already-pushed branch.
- **Every PR must include a manual test plan** — when opening a PR, add a **How to test manually** section describing the exact steps to exercise the change by hand. For dasik, list the concrete `dasik <verb> config/<file>.json` invocation(s) to run, any flags (`-v`), the sample config to use (destructive flags **off**), and the expected result (parsing succeeds, `is_needed()` planning is correct, a re-run is a no-op). Include setup (which config, whether `/mnt` must be mounted) and edge/error cases (invalid JSON, missing binary, already-satisfied state) to check.

# dasik — Agent Guide

Declarative Arch Linux installer. Goal: behave like Nix/NixOS — describe the target system in one JSON file, run `dasik config.json`, and get that system. Running the **same** JSON again changes nothing (idempotent).

This file documents the `dasik/` package at the repo root — the active reimplementation (formerly `new/`, promoted to root in commit `3a17d00`). Ignore `archinstall/` (reference dumps) and the legacy scripts described in the repo-root `README.md`.

## Rules by topic — what always binds, and where the detail lives

This file fits in the 32 KiB Codex reads by default (`wc -c AGENTS.md` ≤ 32768; when it grows, move
detail to `docs/agents/`, never raise the limit). The detail of each topic was moved verbatim to
`docs/agents/` on 2026-10-04. **The lines below bind even if you never open the document; open it
before working on that topic.** A rule is edited in its document, not here and there at once —
except for its one-line summary in this list.

- **`resources/`** → [docs/agents/resources.md](docs/agents/resources.md). Never `Glob resources/**`,
  never read it in bulk, never let `/graphify` ingest it; targeted reads of the arch-wiki are fine.
- **Feature checklist (detectable, capturable, every verb)** →
  [docs/agents/feature-checklist.md](docs/agents/feature-checklist.md), before finishing any feature.
  `plan` sees it in both directions; `sync` reads it back and `sync` → `check` → `plan` ends silent;
  all of `check`, `plan`, `apply`, `sync`, `generations`, `rollback` driven, also with the block removed.
- **Every change gets a VM, and what the checks are called** →
  [docs/agents/vm-and-real-environment.md](docs/agents/vm-and-real-environment.md). Any change under
  `dasik/` is driven in a QEMU guest before it is done; `apply` only ever runs inside the guest;
  every new check is seen failing once; never assert on a count you cannot predict; say which verbs
  ran for real.
- **Quality beyond coverage** →
  [docs/agents/quality-beyond-coverage.md](docs/agents/quality-beyond-coverage.md). Mutation and
  Hypothesis first; every new top-level field is modeled in pydantic; a `plan` dry run against the
  sample configs; the AI never defines the acceptance criteria.
- **Agentic PR verification (mandatory)** → [docs/agents/pr-verification.md](docs/agents/pr-verification.md).
  Every PR gets the verdict of a build-and-smoke pass of the CLI as a PR comment; it never merges.
- **Debugging** → [docs/agents/debugging.md](docs/agents/debugging.md), before chasing a bug. Measure
  before ablating; more than three reproductions → a shortcut script before the fourth; a review
  finding is not a reproduction; environment claims get measured or they don't get made.
- **Agent orchestration** → [docs/agents/agent-orchestration.md](docs/agents/agent-orchestration.md).
  Review in parallel with the next implementation; shared facts file; plans carry contracts, not
  literal code; discretionary decisions batched; review is never cut.
- **Design principles (SOLID)** → [docs/agents/design-principles.md](docs/agents/design-principles.md).
  No abstraction without a second implementation, an IO boundary or a test seam.
- **Codex and Claude Code** → [docs/agents/agent-compatibility.md](docs/agents/agent-compatibility.md).
  Rules are edited in `AGENTS.md` (or its `docs/agents/` document), never in `CLAUDE.md`.

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
  **Pace in this mode** (2026-10-04): intermediate tasks run only the tests of what they touched
  (`pytest tests/lib/actions/test_<x>.py`); commits pile up locally and the branch is pushed **once,
  at the end** — the push that runs the full `pre-push` hook (pytest + coverage, mypy, bandit,
  mutation), preceded by the final full pytest and guest pass. Each intermediate push paid the whole
  hook (minutes) to report nothing the next one would not.

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

## Adding a new config option (the common task)

To mirror "add an option to a NixOS module":

1. **Model** — add a pydantic model in `dasik/lib/models/<thing>_model.py`; wire it into `JsonModel` (`dasik/lib/models/json_model.py`) as an `Optional[...]` field (use `Optional`/`default_factory` so it stays optional — the whole point is many optional sections).
2. **Action** — create `dasik/lib/actions/<thing>_action.py` subclassing `AbstractAction`; implement `name`, `plan`, `apply`, `import_state` (and `verify` if it needs a post-check). Do NOT write `is_needed`/`execute`: they are inherited and delegate. Shell out via `Command.execute(...)` (`dasik/lib/command_worker/`), passing `run_as_chroot=True` for changes inside the target.
3. **Register** — add a `register_action(...)` call in `setup_actions()` at the correct phase, `is_optional=True` for optional sections.
4. **Config sample** — add/extend a JSON under `config/` so the option is exercised.
5. **Detectability** — prove `plan` sees it *and* `sync` reads it back, in BOTH directions (see [Feature checklist](docs/agents/feature-checklist.md)).

## Running commands

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

### The pyramid per feature — one journey test and one guest script, the rest one layer down

**Rule since 2026-10-04**, ported from the Android client, where E2E ate days of agent time. A new
feature gets **one journey test through the CLI** — `main([...])` against a `tmp_path` fake root with
the system commands mocked, in the style of `tests/cli/test_verbs_integration.py`, run as the round
trips of *Every feature must be …exercised through EVERY verb* (`plan → apply → plan` ends silent;
`sync → check → plan` ends silent) — and **one guest script** (`config/vm-<feature>.json` +
`scripts/vmtest/guest-<feature>.sh`), the real end-to-end that *Every change gets a VM* already
demands. Everything else — every `is_needed()` / `verify()` branch, every model validation, every
edge value — goes in unit tests of the pure decision under `tests/lib/`, which cost milliseconds and
touch no disk.

- **When one more guest script is right:** what a monkeypatched `Command.execute` cannot answer —
  the real output format of `pacman` / `sgdisk` / `hwclock`, `arch-chroot` mount semantics, a
  bootloader entry or initramfs name that must exist, anything that only a booted machine shows. The
  script says in its header why it is not a unit test. One guest script per feature, not per edge case.
- **A bug still gets its failing test first**, at the lowest layer that reproduces it; a bug only the
  guest could see gets its guest check seen red first.
- **Existing tests are not migrated for this rule.** It applies to new work and to what a change touches.

### Running the suites — the whole suite once at the end, only the reds in between

- **While working:** only the tests of what you touched — `pytest tests/lib/actions/test_<x>.py`, or
  `pytest -k <expression>` (the `-k is_needed` form above).
- **The full run happens once, at the end of the branch, alone:** `pytest --cov=dasik`, then the
  guest run from a **fresh** qcow2 — in the background while you write the PR. The `pre-push` hook
  (pytest + coverage, mypy, bandit, mutation) is that final pass; push and PR only after it is green.
- **Red pass → only the reds** (`pytest --lf`) until they are green or proven red on the base commit
  too; then **one** full confirmation pass, the one that catches a fix breaking another test.
- **Three reds in a row on one test → stop** and read the evidence (the assertion diff, the
  `<MARKER>-…=rc` lines, the planned actions) before a fourth change.
- **No fixed sleeps** — wait on the state (for the guest, on the marker line). **Every heavy command**
  (the full suite, `scripts/mutation.sh`, the guest run) runs under `timeout --kill-after=60s <limit>`.

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

## Safety — this tool is destructive

- It **partitions disks, formats filesystems, and runs `pacman`** against `/mnt`. Treat any code path that reaches `execute()` as capable of wiping a disk.
- Disk actions are gated by a `format` flag in config — keep destructive steps behind explicit opt-in flags; never make formatting the default.
- When testing or demoing, use configs with destructive flags off, or run validation-only paths. Never run real installs against the dev machine's own disks.

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
  [Design principles](docs/agents/design-principles.md#design-principles--solid-applied-with-judgement).
- **TDD by default** for new logic (`models/`, `json_parser/`, `actions/` `is_needed`/`verify`, `command_worker/`). Don't merge logic without tests.
- **Don't lower the coverage gate** — exclude untestable modules in config with a written justification instead.
- **Preserve idempotency** — any new action must implement a real `is_needed()` that reads system state. A re-run of the same JSON must be a no-op.
- **Every feature must be detectable by `plan`** — missing ⇒ planned, present ⇒ silent, owned-but-undeclared ⇒ removed. Assert all of them; a quiet plan is not evidence. See [Every feature must be detectable by `plan`/`apply`](docs/agents/feature-checklist.md#every-feature-must-be-detectable-by-planapply).
- **…and capturable by `sync`** — every feature needs an `import_state` that reads it back as its own block, and `sync` → `plan` must be a no-op. A feature delivered only by an expand toggle has no owner on the way back until you add one. See […and capturable by `sync`](docs/agents/feature-checklist.md#and-capturable-by-sync).
- **…and exercised through EVERY verb before you call it done** — `check`, `plan`, `apply`, `sync`, `generations`, `rollback`, as round trips (`sync` → `check` → `plan` silent; `plan` → `apply` → `plan` silent), *including with the config block removed*. A green unit suite is not this. `apply`/`rollback` never run for real — scratch root or mocked `Command.execute`, and say which verbs were real. See […and exercised through EVERY verb](docs/agents/feature-checklist.md#and-exercised-through-every-verb-before-it-is-called-done).
- **Keep sections optional** — config has many optional blocks (disks, kvm, cups, wireguard, …), not just disks. New top-level fields should be `Optional`/defaulted in `JsonModel`.
- **The legacy handler is gone** — the monolithic `actions_handler.py` and the no-verb `dasik <config>` fallback were removed (PR #151). Put all behavior in the v2/v3 registry/action path (`setup_actions()` + `plan()/apply()/import_state()`).
- **Entry point is on v3** — `__main__`'s verbs (`plan`/`apply`/`sync`/`generations`/`rollback`) use the reconciler. A bare `dasik <config>` (no verb) is rejected with a pointer to `plan`/`apply`.
- **Never run `execute()` against real hardware** — partitioning/`pacman`/`arch-chroot` are destructive. Mock `Command.execute` in tests.
- **Every change to `dasik/` is tested in a VM before it is called done** — see [Every change gets a VM](docs/agents/vm-and-real-environment.md#every-change-gets-a-vm). Not "when it seems risky": every change. The green suite is not the evidence.
- **Instrument before you ablate, budget the lap, and dispatch review in parallel** — a pipeline that completes with non-empty output produced output; more than three reproductions means you owe a shortcut script; a review finding is not a reproduction; and the review of task N runs alongside the implementation of N+1. See **Debugging** and **Agent orchestration** above.

## Git & GitHub

- **Commits and branches OK** — create commits and new branches whenever it makes sense, without asking first.
- **Never push** *(default)* — no `git push` under any circumstance, and absolutely never `git push --force` / `--force-with-lease`. Leave pushing to the user. **Exception:** when **"modo desatendido"** is active, you may push the feature branches you create (never `main`/protected branches, never force) so PRs are ready for review.
- **Never merge — no permission** — you do NOT have permission to merge anything into any branch, nor to merge any pull request. No `git merge`, no fast-forward integration, no `gh pr merge`. Leave every merge (branches and PRs alike) to the user. This holds in every mode, **including "modo desatendido"**.
- **GitHub via `gh`** — if the `gh` CLI is available, you may open pull requests, issues, and similar (comments, labels, etc.). These don't require pushing on your part beyond what `gh` itself does for an already-pushed branch.
- **Every PR must include a manual test plan** — when opening a PR, add a **How to test manually** section describing the exact steps to exercise the change by hand. For dasik, list the concrete `dasik <verb> config/<file>.json` invocation(s) to run, any flags (`-v`), the sample config to use (destructive flags **off**), and the expected result (parsing succeeds, `is_needed()` planning is correct, a re-run is a no-op). Include setup (which config, whether `/mnt` must be mounted) and edge/error cases (invalid JSON, missing binary, already-satisfied state) to check.

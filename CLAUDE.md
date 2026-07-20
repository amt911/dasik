# dasik — Claude Guide

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

## ⚡ superpowers — for substantial work

Prefer **superpowers** skills over ad-hoc approaches **for substantial work** — a feature, a debugging session, new logic, a review. Invoke via the `Skill` tool when a skill clearly matches. Do **not** invoke skills for trivial edits (a one-line fix, a doc/log/wording tweak, a rename), and **not** before clarifying a question with the user.

- **Process skills first** — `brainstorming` before creative/feature work, `systematic-debugging` before fixing bugs, `test-driven-development` before writing implementation.
- **Then implementation skills** — domain-specific skills guide execution.
- **Verify before claiming done** — `verification-before-completion` / `requesting-code-review` before merging.

**Exception — TDD is never skipped for being "trivial".** Any *new logic* in `models/`, `json_parser/`, `actions/` (`is_needed`/`verify`), or `command_worker/` requires the full TDD cycle even when the change is small. "Trivial" above means edits with **no new logic** (formatting, wording, renames), not small logic changes.

User instructions always take precedence over skills; skills override default behavior.

### Mode switch

- **"lite mode"** — fully disables superpowers: no skill is invoked, not even the applicability check, until **"normal mode"** is said.
- **"normal mode"** (default) — standard superpowers behavior, plus: when delegating coding work, dispatch at most 1 agent at a time, and never use a model above Sonnet (no Opus).
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
pip install -e .[dev]   # also installs pytest + pytest-cov

# run against a config
dasik config/install-megamix.json          # console-script entry point
python -m dasik config/install-megamix.json # equivalent module form

# flags
dasik config.json -v        # verbose
dasik config.json --dry-run # NOTE: parsed but NOT implemented yet (see __main__.py TODO)

# test / quality
pytest                       # unit tests (~430, all passing)
pytest --cov=dasik           # coverage (gate: 80%; needs the [dev] extra)
pytest -k is_needed          # filter by name
mypy dasik                   # static type checking (a .mypy_cache is present)
```

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
- `is_needed() -> bool` — **the idempotency check.** Inspect real system state under `/mnt`; return `False` when already in the desired state. This is what makes re-runs no-ops.
- `execute() -> None` — apply changes; only called when `is_needed()` is `True`.
- `verify() -> bool` — optional post-check (default `True`).

`do_action()` and the `_before_check`/`after_check`/`KEY_NAME` members are **vestigial** shims (the legacy handler that used them is gone — PR #151); a couple of actions still carry them but nothing calls them. New code uses `plan()/apply()/import_state()` (and the `is_needed()/execute()/verify()` executor shims).

Actions are registered in `actions_handler_v2.setup_actions()` with `register_action(action_class, config_key, is_optional, required_fields, depends_on)`. `config_key='__root__'` means the action reads root-level config fields (e.g. `DropFilesAction`, `MkinitcpioAction`). `ActionExecutor` walks the registry in order; **ordering matters** (disk/base first, boot last) and two orderings are load-bearing:

- `PacmanHooksAction` runs in phase 1, **between the disk actions and pacstrap**: it writes the mkinitcpio neutralizer hooks, which must exist before the *first* pacman transaction or mkinitcpio clobbers dracut's initramfs (forensic report F-10).
- `SnapperAction` runs **before** `PackagesAction`: snap-pac's hooks snapshot each transaction, so the config must already exist. It installs `snapper`/`snap-pac` itself when missing (F-13).

### Adding a new config option (the common task)

To mirror "add an option to a NixOS module":

1. **Model** — add a pydantic model in `dasik/lib/models/<thing>_model.py`; wire it into `JsonModel` (`dasik/lib/models/json_model.py`) as an `Optional[...]` field (use `Optional`/`default_factory` so it stays optional — the whole point is many optional sections).
2. **Action** — create `dasik/lib/actions/<thing>_action.py` subclassing `AbstractAction`; implement `name`, `is_needed`, `execute`, `verify`. Shell out via `Command.execute(...)` (`dasik/lib/command_worker/`), passing `run_as_chroot=True` for changes inside the target.
3. **Register** — add a `register_action(...)` call in `setup_actions()` at the correct phase, `is_optional=True` for optional sections.
4. **Config sample** — add/extend a JSON under `config/` so the option is exercised.

### Running commands

Use `Command.execute(cmd, args, run_as_chroot=False)` (`dasik/lib/command_worker/command_worker.py`) rather than calling `subprocess` directly — it locates the binary (raising `CommandNotFoundException`) and optionally prefixes `arch-chroot /mnt`. Custom exceptions live in `dasik/lib/exceptions/exceptions.py`.

## Tests and quality

A pytest suite exists (~430 tests under `tests/`, mirroring `dasik/lib/`). These rules govern both existing tests and new ones.

- **pytest** for unit tests, **pytest-cov** for coverage (a `dev` extra in `pyproject.toml` — `pip install -e .[dev]`; not always installed, so `--cov` may be unavailable until you do). Config: `pyproject.toml` (`[tool.pytest.ini_options]` + `[tool.coverage.*]`).
- **pytest monkeypatch / unittest.mock** to stub system access (`Command.execute`, `pathlib.Path`, `subprocess`). Never touch a real disk in a test.
- File convention: `test_*.py` under a top-level `tests/` directory mirroring `dasik/lib/` layout.
- **Coverage gate: 80%** (statements/branches, `fail_under = 80`). Don't lower the gate — exclude untestable modules in config with a written justification instead.

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
- **Smoke / dry-run validation** — there is no live-ISO in CI and installs are destructive, so the "does it actually boot" check is: run `dasik <config>` against the real sample configs under `config/` with all destructive flags **off** (or the future `--dry-run`, once implemented) and confirm parsing + `is_needed()` planning complete without exceptions. Never run a real install against the dev machine's disks.
- **Dependency auditing** — AI invents non-existent packages ("slopsquatting") and pulls vulnerable versions. Runtime deps are intentionally two (`pydantic`, `colorama`); use `pip-audit`, and verify every new dependency actually exists and is the one you think it is before adding it.

**Process rule (worth more than any tool): don't let the AI define the acceptance criteria.** You write or review the important test cases yourself — at least the key asserts and the requirement's edge cases (does a re-run really no-op? does the destructive flag really gate?) — and have the AI implement against them. That breaks the loop where the same misunderstanding lives in both the test and the code. Mutation testing is the automated backstop; the judgment about *what the system should do* stays yours.

Priority by immediate payoff: **mutation + property-based testing first** on the idempotency logic, then **keep mypy clean and a couple of dry-run smoke checks**.

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
- **Hard limits.** The verdict awaits your close; the agent never merges. Scope `--allowedTools`;
  use `--dangerously-skip-permissions` only in a controlled local env — never against real
  hardware/disks (see *Safety*, below).

## Safety — this tool is destructive

- It **partitions disks, formats filesystems, and runs `pacman`** against `/mnt`. Treat any code path that reaches `execute()` as capable of wiping a disk.
- Disk actions are gated by a `format` flag in config — keep destructive steps behind explicit opt-in flags; never make formatting the default.
- When testing or demoing, use configs with destructive flags off, or run validation-only paths. Never run real installs against the dev machine's own disks.

## Working rules

- **Use superpowers skills whenever they apply** — invoke via `Skill` before acting; process skills before implementation skills.
- **Don't install packages without asking** — runtime deps are intentionally minimal (`pydantic`, `colorama`); the stack is intentional.
- **TDD by default** for new logic (`models/`, `json_parser/`, `actions/` `is_needed`/`verify`, `command_worker/`). Don't merge logic without tests.
- **Don't lower the coverage gate** — exclude untestable modules in config with a written justification instead.
- **Preserve idempotency** — any new action must implement a real `is_needed()` that reads system state. A re-run of the same JSON must be a no-op.
- **Keep sections optional** — config has many optional blocks (disks, kvm, cups, wireguard, …), not just disks. New top-level fields should be `Optional`/defaulted in `JsonModel`.
- **The legacy handler is gone** — the monolithic `actions_handler.py` and the no-verb `dasik <config>` fallback were removed (PR #151). Put all behavior in the v2/v3 registry/action path (`setup_actions()` + `plan()/apply()/import_state()`).
- **Entry point is on v3** — `__main__`'s verbs (`plan`/`apply`/`sync`/`generations`/`rollback`) use the reconciler. A bare `dasik <config>` (no verb) is rejected with a pointer to `plan`/`apply`.
- **Never run `execute()` against real hardware** — partitioning/`pacman`/`arch-chroot` are destructive. Mock `Command.execute` in tests.

## Git & GitHub

- **Commits and branches OK** — create commits and new branches whenever it makes sense, without asking first.
- **Never push** *(default)* — no `git push` under any circumstance, and absolutely never `git push --force` / `--force-with-lease`. Leave pushing to the user. **Exception:** when **"modo desatendido"** is active, you may push the feature branches you create (never `main`/protected branches, never force) so PRs are ready for review.
- **Never merge — no permission** — you do NOT have permission to merge anything into any branch, nor to merge any pull request. No `git merge`, no fast-forward integration, no `gh pr merge`. Leave every merge (branches and PRs alike) to the user. This holds in every mode, **including "modo desatendido"**.
- **GitHub via `gh`** — if the `gh` CLI is available, you may open pull requests, issues, and similar (comments, labels, etc.). These don't require pushing on your part beyond what `gh` itself does for an already-pushed branch.
- **Every PR must include a manual test plan** — when opening a PR, add a **How to test manually** section describing the exact steps to exercise the change by hand. For dasik, list the concrete `dasik config/<file>.json` invocation(s) to run, any flags (`-v`, `--dry-run`), the sample config to use (destructive flags **off**), and the expected result (parsing succeeds, `is_needed()` planning is correct, a re-run is a no-op). Include setup (which config, whether `/mnt` must be mounted) and edge/error cases (invalid JSON, missing binary, already-satisfied state) to check.

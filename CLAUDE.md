# dasik — Claude Guide

Declarative Arch Linux installer. Goal: behave like Nix/NixOS — describe the target system in one JSON file, run `dasik config.json`, and get that system. Running the **same** JSON again changes nothing (idempotent).

## Always use superpowers (user directive)

For ALL non-trivial work in this repo, use the superpowers skills — no exceptions:

- New feature / change → `brainstorming` → `writing-plans` → `executing-plans` → `finishing-a-development-branch`.
- Any bug / test failure / unexpected behavior → `systematic-debugging` (find root cause before any fix; don't pile fixes).
- New logic in `models/`, `json_parser/`, `actions/`, `command_worker/`, `expand/` → strict TDD (red → green → refactor).
- One slice per session, each ending in a PR. Never `git push` (user pushes; I open PRs with `gh`).

This file documents the `dasik/` package at the repo root — the active reimplementation (formerly `new/`, promoted to root in commit `3a17d00`). Ignore `archinstall/` (reference dumps) and the legacy scripts described in the repo-root `README.md`.

## Resources (local reference — bind-mounted, not committed)

`resources/` holds two read-only references plus the script that mounts them. They are **not tracked** (`git ls-files resources/` is empty) — recreate them on a new machine with [`resources/bind-mount.sh`](resources/bind-mount.sh):

| Path | What it is | Use it for |
| --- | --- | --- |
| `resources/archlinux-script-installer/` | The **old** personal install scripts (bind-mount of `~/repos/archlinux-script-installer`, its own git repo). | The imperative original that dasik reimplements declaratively. Same target system: LUKS encryption (+ pendrive unlock), ext4 or btrfs+subvolumes, snapper snapshots, GRUB/systemd-boot, KDE Plasma, NVIDIA passthrough. Read a step here to see how it was done before porting it to an idempotent Action — its own `TODO` already asks for "incremental changes on already installed systems", i.e. dasik's idempotency goal. |
| `resources/arch-wiki/` | Arch Wiki **offline HTML** mirror (bind-mount of `/usr/share/doc/arch-wiki/html/en`, from the `arch-wiki-docs` package). ~2,500 pages named by title: `Btrfs.html`, `Dm-crypt.html`, `Mkinitcpio.html`, `Systemd-boot.html`, … | Authoritative reference for the exact procedure an Action must reproduce (mkinitcpio hook order, dm-crypt cmdline flags, btrfs subvolume layout, …). gitignored via the `arch-wiki/` pattern. |

**Don't graphify `resources/`** — together they are >12k files and would swamp the graph. Graph scope is the `dasik/` package only (~52 files); these dirs are reference, not source.

**Verify Arch specifics against the wiki mirror (user directive).** Before changing any package name, repo, or Arch command in the codebase (e.g. the hardcoded package lists in `dasik/lib/expand/toggles.py`, mkinitcpio hooks, dm-crypt flags, bootloader install steps), check `resources/arch-wiki/` (and the old `resources/archlinux-script-installer/`) instead of guessing. Arch is rolling — package names rot. Example: `mesa-vdpau` was **removed** in mesa 25.3.0 (VDPAU dropped from the open-source drivers; no replacement — VA-API via `libva-mesa-driver` is the modern path), per `resources/arch-wiki/Hardware_video_acceleration.html`. A wrong/removed package makes the whole `pacman -S` abort with "target not found".

## Start here

Run `/graphify` before each session. The persistent graph at `graphify-out/graph.json` summarizes architecture, dependencies, and cross-cutting concepts without re-reading the repo each time.

### ⚡ graphify — use every session

```
/graphify            # first run (builds graph from scratch)
/graphify --update   # incremental update (only re-extracts changed files)
/graphify query "<question>"    # architecture questions instead of opening multiple files
/graphify explain "<name>"      # locate a concept or symbol
/graphify path "A" "B"          # dependency path between two modules
```

Outputs in `graphify-out/`: `graph.json` (source of truth), `GRAPH_REPORT.md`, `graph.html` (interactive view). Run `/graphify --update` at end of session if you touched docs.

## Stack

- **Python** ≥ 3.10 — CLI tool, packaged via `setuptools` (`pyproject.toml`).
- **pydantic** — config schema + validation (`dasik/lib/models/`).
- **colorama** — colored terminal output.
- **System tooling** — wraps real commands (`arch-chroot`, `pacman`, `sgdisk`/partitioning, `ln`, `hwclock`, …) via `subprocess`. No Python bindings; it shells out.

## Commands

```bash
# install (editable, for development)
pip install -e .

# run against a config
dasik config/install-megamix.json          # console-script entry point
python -m dasik config/install-megamix.json # equivalent module form

# flags
dasik config.json -v        # verbose
dasik config.json --dry-run # NOTE: parsed but NOT implemented yet (see __main__.py TODO)
```

A pytest suite **does** exist now (`tests/` mirrors `dasik/lib/`, configured in `pyproject.toml`; ~430 tests, all passing — run `pytest`). Note that `INTEGRATION-COMPLETE.md` and `docs/HOW-TO-TEST.md` still describe a `tests/test_disk_integration.py` that does **not** exist — treat those two docs as aspirational. See [Tests and quality](#tests-and-quality).

## How it works

```
config.json
  → JsonParser            (dasik/lib/json_parser/) — opens file, validates with pydantic JsonModel,
                           returns a plain dict via .debug()
  → ActionsHandler        (dispatches each config section to an Action)
  → Action.is_needed()    idempotency check: inspect current system state
  → Action.execute()      apply changes (only if needed)
  → Action.verify()       confirm the change landed
```

Every change runs against the **mounted install target at `/mnt`**, typically via `arch-chroot /mnt`. Actions read `/mnt/etc/...` to decide `is_needed()`. This is install-from-live-ISO tooling, not a config manager for the running host.

### Two handlers exist — know which is which

| File | Style | Idempotent? | Actions covered |
| --- | --- | --- | --- |
| `actions_handler.py` | Legacy, monolithic; one hard-coded `_handle_*` method per section, calls `action.do_action()` | **No** | disks, base install, timezone, locale, network |
| `actions_handler_v2.py` | Registry + `ActionExecutor`; calls `is_needed()/execute()/verify()` | **Yes** | all ~20 actions (`setup_actions()`) |

⚠️ **`dasik/__main__.py` currently imports the LEGACY `actions_handler`**, so the entry point does *not* yet use the idempotent architecture or most actions. Wiring `__main__` to `actions_handler_v2` (its `ActionsHandler` shim, or `setup_actions()` + `execute_installation()`) is the natural next step. Confirm intent before changing this.

### The Action model (v2 — the target architecture)

`AbstractAction` (`dasik/lib/actions/abstract_action.py`) is the contract for every action:

- `name` (property) — human-readable label.
- `is_needed() -> bool` — **the idempotency check.** Inspect real system state under `/mnt`; return `False` when already in the desired state. This is what makes re-runs no-ops.
- `execute() -> None` — apply changes; only called when `is_needed()` is `True`.
- `verify() -> bool` — optional post-check (default `True`).

`do_action()` and the `_before_check`/`after_check`/`KEY_NAME` members are **deprecated** shims for the legacy handler. New code uses `is_needed()/execute()/verify()`.

Actions are registered in `actions_handler_v2.setup_actions()` with `register_action(action_class, config_key, is_optional, required_fields, depends_on)`. `config_key='__root__'` means the action reads root-level config fields (e.g. `DropFilesAction`, `MkinitcpioAction`). `ActionExecutor` walks the registry in order; ordering matters (disk/base first, boot last).

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
- **Coverage gate: 80%** (statements/branches). Don't lower the gate — exclude untestable modules in config with a written justification instead.

```bash
pytest                       # unit tests
pytest --cov=dasik           # coverage
pytest -k is_needed          # filter by name
```

### What to test per folder

| Folder | What | Status |
| --- | --- | --- |
| `dasik/lib/models/` | pydantic models — accept valid configs, reject invalid. Deterministic, no mocks | ✅ Covered |
| `dasik/lib/json_parser/` | `JsonParser` against fixture JSON files; assert parsed dict + bad-file handling | ⚠️ Pending (no tests yet) |
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

- **Don't install packages without asking** — runtime deps are intentionally minimal (`pydantic`, `colorama`).
- **Preserve idempotency** — any new action must implement a real `is_needed()` that reads system state. A re-run of the same JSON must be a no-op.
- **Keep sections optional** — config has many optional blocks (disks, kvm, cups, wireguard, …), not just disks. New top-level fields should be `Optional`/defaulted in `JsonModel`.
- **Don't expand the legacy handler** — put new behavior in the v2 registry/action path, not in `actions_handler.py`'s `_handle_*` methods.
- **Mind the entry-point gap** — remember `__main__` still uses the legacy handler; flag it when relevant.

## Git & GitHub

- **Commits and branches OK** — create commits and new branches whenever it makes sense, without asking first.
- **Never push** — no `git push` under any circumstance, and absolutely never `git push --force` / `--force-with-lease`. Leave pushing to the user.
- **GitHub via `gh`** — if the `gh` CLI is available, you may open pull requests, issues, comments, labels, etc. (no pushing on your part beyond what `gh` does for an already-pushed branch).

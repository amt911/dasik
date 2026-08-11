# Development

Working on dasik itself: the architecture, how to add a config option, the
quality gates, and how to test destructive code without destroying anything.

---

## Architecture

```text
dasik/
├── __main__.py                 CLI: verbs, flags, exit codes
└── lib/
    ├── models/                 pydantic schema — the config boundary
    ├── json_parser/            includes.py: $include/$include_text/$include_line/$concat
    ├── expand/toggles.py       feature block → packages/units/files
    ├── validation/preflight.py cross-field coherence
    ├── actions/                one action per domain (plan/apply/import_state)
    │   └── initramfs/          mkinitcpio + dracut backends
    ├── reconciler/             set math, plan building, apply orchestration
    ├── state/                  manifest, generations, config writer
    ├── target/                 target root + arch-chroot handling
    └── command_worker/         Command.execute — the only way to shell out
```

Every action implements the same three methods, and that is the whole contract:

| Method | Contract |
| --- | --- |
| `plan(managed)` | compare declared state against **real** system state, return `Change`s |
| `apply(changes)` | carry out exactly those changes |
| `import_state(managed)` | return the config fragment describing reality |

Actions are registered in `actions_handler_v2.setup_actions()` with
`register_action(action_class, config_key, is_optional, …)`. `config_key
='__root__'` means the action reads root-level fields. **Order matters** —
see [Workflows](Workflows.md#execution-order).

Never call `subprocess` directly: `Command.execute(cmd, args,
run_as_chroot=True)` locates the binary, raises `CommandNotFoundException`, and
applies the chroot prefix.

---

## Adding a config option

The five steps, in order:

1. **Model** — add a pydantic model in `dasik/lib/models/<thing>_model.py` and
   wire it into `JsonModel` as `Optional[...]`. Optional is the rule: dasik is
   made of optional blocks.
2. **Action** — `dasik/lib/actions/<thing>_action.py`. Implement `plan`,
   `apply`, `import_state` (and `name`). Read real state — a file under the
   target, a `pacman -Qq`, a `luksDump` — never a cached belief.
3. **Register** — a `register_action(...)` call at the correct phase,
   `is_optional=True`.
4. **Sample config** — extend something under `config/` so the option is
   exercised, and validate it with `dasik check`.
5. **Prove both directions** — `plan` sees it *and* `sync` reads it back.

### The two rules that are easy to get wrong

**Detectable by `plan`.** Assert all of: missing ⇒ planned; present ⇒ silent;
declared off but owned ⇒ REMOVE; unowned and undeclared ⇒ left alone. A quiet
plan is not evidence of anything — "already converged" and "dasik ignores this
block" look the same. Matrix: `tests/lib/test_feature_detectability.py`.

**Capturable by `sync`.** A feature delivered purely by an expand toggle has no
owner on the way back until you give it one (that is why `cpu`, `reflector` and
`plymouth` exist as capture-only actions). Assert: machine has it ⇒ the block is
captured; machine lacks it ⇒ nothing invented; and the captured config validates
and re-plans to nothing. Matrix:
`tests/lib/test_feature_sync_capture.py`.

**Exercise every verb before calling it done** — `check`, `plan`, `apply`,
`sync`, `generations`, `rollback`, as round trips, *including with the config
block removed* (the reconciler then hands the action its **empty** config, which
is not the same as "the empty value"). `apply`/`rollback` never run for real:
mock `Command.execute` or use a scratch root.

---

## Quality gates

Four gates, run by `.githooks/pre-push` and by CI:

```bash
pytest --cov=dasik      # 1,700+ tests, coverage gate 80%
mypy dasik              # clean
bandit -r dasik         # SAST — this tool shells out constantly
scripts/mutation.sh     # mutation testing, set_math tier
```

Enable the hook once per clone:

```bash
git config core.hooksPath .githooks
pip install -e '.[dev,mut]'    # the hook refuses to run if a tool is missing
```

`--no-verify` bypasses it. CI still gates.

### Why mutation testing

Coverage says a line **ran**; mutation testing says a test would have
**noticed** if that line were wrong. It flips `>` to `>=`, drops a line, swaps a
set operator, and checks that some test fails. A surviving mutant is code that
is covered but not verified — exactly the defect shape you get when the same
misunderstanding is written into both the test and the implementation.

The target is the idempotency core: `is_needed`/`plan` decisions and the
reconciler's set math. A flipped comparison there breaks idempotency or wipes a
disk.

### TDD is required for new logic

Red → green → refactor, for anything in `models/`, `json_parser/`, `actions/`
(`plan`/`is_needed`/`verify`) and `command_worker/`. "Small" does not exempt it;
"no new logic" (formatting, wording, a rename) does.

Not required for `apply()` bodies that only shell out to destructive tooling —
cover the *decision* instead and assert `Command.execute` was called with the
right arguments, via mock.

**Never run `execute()` against real hardware.**

---

## Testing in a VM

The destructive paths cannot be unit-tested and must never run on your machine.
`scripts/vmtest/` drives a real Arch ISO in QEMU:

```bash
scripts/vmtest/qemu.sh install      # full install into a disposable disk image
scripts/vmtest/qemu.sh day2         # convergence on the installed guest
scripts/vmtest/qemu.sh boot-unlock  # LUKS passphrase unlock at boot
scripts/vmtest/qemu.sh lifecycle    # sync / generations / rollback
```

`docs/vm-testing.md` and `docs/testing-without-a-vm.md` in the repository cover
the lighter alternatives (loopback devices, `systemd-nspawn`) and the recovery
tricks — including the archiso cowspace fix.

---

## Contributing

- **Branch and commit freely.** Do not push to `main`; open a PR.
- **Every PR needs a "How to test manually" section**: the exact
  `dasik <verb> config/<file>.json` invocations, which sample config, whether
  `/mnt` must be mounted, and the expected result (parses, plans correctly,
  re-run is a no-op). Include the edge cases — invalid JSON, missing binary,
  already-satisfied state.
- **Every PR gets an end-to-end verification verdict** posted as a PR comment: a
  scratch venv, `pip install -e '.[dev]'`, the non-destructive verbs against a
  real tracked config, and the entry points. Never `apply`/`rollback` for real.
  Say plainly which verbs were exercised for real and which were asserted.
- **Do not lower the coverage gate.** Exclude an untestable module in config,
  with a written justification.
- **Do not add dependencies casually.** Runtime deps are intentionally two.

---

## Editing this wiki

The pages are versioned in the repository under `docs/wiki/` and published to
the GitHub wiki from there — the repo is the source of truth, so a doc change
goes through review like any other change.

```bash
$EDITOR docs/wiki/Configuration.md
scripts/publish-wiki.sh --dry-run     # show what would be published
scripts/publish-wiki.sh               # clone the wiki repo, sync, commit, push
```

The script maps `docs/wiki/README.md` → `Home.md` and strips the `.md` suffix
from inter-page links, which is how GitHub wiki resolves them. Keep links
written as `[Configuration](Configuration.md)` in the source so they work when
browsing the repository too.

# Mutation testing

> Coverage measures how much code *runs*, not whether it's *correct*.
> — `CLAUDE.md` § Quality beyond coverage

This is especially treacherous with AI-written code: the model tends to write
the test *and* the implementation in one move, so a misread requirement gets
encoded identically in both — and the test passes happily. For dasik the stakes
are literal: a covered-but-wrong `is_needed()` or a flipped set operator in the
reconciliation math breaks idempotency or wipes a disk.

Mutation testing is the automated backstop. [mutmut](https://mutmut.readthedocs.io/)
injects one deliberate bug at a time into the code under test (`>` → `>=`, drop
a line, swap `&` for `|`, `domain` → `None`) and re-runs the suite:

- **killed** 🎉 — some test failed. Good: the behaviour is *verified*, not just
  covered.
- **survived** 🙁 — every test still passed with the bug in place. That code is
  **covered but not verified** — exactly the AI-shaped defect coverage misses.

## What we mutate

The pure reconciliation core — the highest-value target per `CLAUDE.md`:

| Tier | Files | When | Wired into CI |
| --- | --- | --- | --- |
| 1 | `dasik/lib/state/set_math.py` + `dasik/lib/actions/scalar_action.py` | every run; fast (~80 mutants, ~1 s) | ✅ advisory `mutation` job |
| 2 | `+ dasik/lib/reconciler/reconciler.py` | on demand, when touching the reconciler | ❌ (many mutants, slow) |

The two tier-1 files are the pure idempotency cores every domain routes through:
`set_math.compute_changes` is the whole `D`/`M`/`A`/`F` → `Change` set-math (a
flipped comparison there turns a no-op re-run destructive), and
`ScalarV3Action.plan` is the shared single-value reconcile behind timezone,
initramfs, and every scalar domain. Both are mutation-clean modulo the two
documented equivalents below.

## Run it

```bash
pip install -e .[mut]        # installs mutmut (separate extra; keeps `dev` lean)

scripts/mutation.sh          # tier 1: set_math.py  → exits non-zero if any survive
scripts/mutation.sh --reconciler   # tier 2: also reconciler.py (slow)
scripts/mutation.sh --results      # print the last run's survivors
mutmut show <mutant-name>    # see the exact diff a survivor introduced
```

Config is in `pyproject.toml` under `[tool.mutmut]` (`source_paths`,
`only_mutate`, `pytest_add_cli_args_test_selection`). mutmut copies the package
into `./mutants/` (gitignored) and runs the selected tests there.

## Killing a survivor

A survivor is a request to strengthen a test — **not** to change the source.

1. `mutmut show <name>` — read the one-line diff. What behaviour changed?
2. Ask: *which assert should have caught this?* Usually a field or branch no
   test looks at.
3. Add or tighten the assert in the matching `tests/…` file.
4. Re-run `scripts/mutation.sh` — the mutant should now be **killed**.

### Worked example (this repo's first run)

The initial tier-1 pass left **2 survivors**, both the same shape:

```
-   changes.append(Change(domain, op_remove, item, reason="no longer declared"))
+   changes.append(Change(None,   op_remove, item, reason="no longer declared"))
```

The removal-block tests asserted `op`, `item`, and `reason` but never
`Change.domain`, so blanking the domain on destructive REMOVE changes went
unnoticed — a real bug (plan rendering groups by domain). Fixed by adding
`test_all_changes_carry_their_domain_label`, which exercises the install,
owned-removal, and forced-removal blocks in one scenario and asserts
`c.domain == "systemd"` on every emitted change. Result: **48/48 killed**.

## Equivalent mutants

Occasionally a mutant is *semantically equivalent* — the change can't alter
observable behaviour. Those cannot be killed by any test. Don't contort a test to
chase them: document why, and move on.

`scripts/mutation.sh` knows the current equivalents by a **stable diff
signature** (not the volatile mutant number) and reports them as
`(equivalent, expected)`, so a run with only equivalents still exits 0.

Current equivalents (both in `scalar_action.py`):

- `ScalarV3Action.is_needed` and `ScalarV3Action.verify` call
  `self.plan(managed=[])`, but scalar `plan()` **ignores** its `managed`
  argument (a scalar has no set to own). Mutating `managed=[]` → `managed=None`
  therefore changes nothing — irreducibly equivalent. Signature:
  `plan(managed=None)`.

`set_math.py` has **no** equivalents — all its mutants are real and killed.

## CI

The `mutation` job in `.github/workflows/ci.yml` runs tier 1 on every PR and is
**advisory** (never blocks merge) — the pytest coverage gate stays the hard
gate. It surfaces survivors as a signal to strengthen tests, matching the
existing advisory-job pattern (mypy, pip-audit).

# Mutation testing

> Coverage measures how much code *runs*, not whether it's *correct*.
> — `AGENTS.md` § Quality beyond coverage

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

The pure reconciliation core — the highest-value target per `AGENTS.md`:

| Tier | Files | When | Wired into CI |
| --- | --- | --- | --- |
| 1 | `state/set_math.py` + `actions/scalar_action.py` + `actions/composite_action.py` + `actions/pacman_repos_state.py` | every run; fast (~500 mutants, well under a minute) | ✅ advisory `mutation` job |
| 2 | `+ dasik/lib/reconciler/reconciler.py` | on demand, when touching the reconciler | ❌ (many mutants, slow) |

The tier-1 files are the pure idempotency cores every domain routes through:
`set_math.compute_changes` (the whole `D`/`M`/`A`/`F` → `Change` set-math behind
packages, users, systemd, files, kernel-cmdline — a flipped comparison turns a
no-op re-run destructive); `ScalarV3Action.plan` (the single-value reconcile
behind timezone/initramfs); `CompositeV3Action.plan` (the multi-field
reconcile behind locale/network/pacman); and `pacman_repos_state.py` (the pure
`pacman.conf`-section and `gpg --with-colons` reader/renderer behind
`pacman.repositories`/`pacman.keys` — a flipped comparison there silently
trusts an unverified key, or rewrites the wrong section of `pacman.conf`). All
are mutation-clean modulo the documented equivalents below.

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

`scripts/mutation.sh` knows the current equivalents by a **list of stable diff
signatures** (`_EQUIVALENT_SIGNATURES`, not the volatile mutant number) and
reports a matching survivor as `(equivalent, expected)`, so a run with only
equivalents still exits 0. A survivor's `mutmut show <name>` diff only needs
to *contain* one of the signatures — one signature can cover more than one
mutant when the exact same reasoning applies verbatim to two functions (see
the `last_type` pair below).

In `scalar_action.py`:

- `ScalarV3Action.is_needed` and `ScalarV3Action.verify` call
  `self.plan(managed=[])`, but scalar `plan()` **ignores** its `managed`
  argument (a scalar has no set to own). Mutating `managed=[]` → `managed=None`
  therefore changes nothing — irreducibly equivalent. Signature:
  `plan(managed=None)`.

`set_math.py` has **no** equivalents — all its mutants are real and killed.

In `pacman_repos_state.py` (added for `pacman.repositories`/`pacman.keys`,
2026-09):

- `options_block`: `end = len(lines)` mutated to `end = None`. Python slicing
  makes `lines[start:None]` and `lines[start:]` (equivalently, `lines[start:
  len(lines)]`) identical for any list — there is no input that can tell
  `None` and `len(lines)` apart as a slice's upper bound. Signature:
  `end = None`.
- `section_of`: `_field(model, "servers", [])` mutated so the default becomes
  `None` instead of `[]` (two distinct mutants — an explicit `None` and a
  removed argument). Either way the line reads
  `tuple(_field(model, "servers", ...) or [])`: the trailing `or []` turns
  any falsy default (`None` **or** `[]`) back into `[]`, so the choice of
  default is never observable. Signatures: `_field(model, "servers", None) or
  []` and `_field(model, "servers", ) or []`.
- `primary_fingerprints` and `trusted_fingerprints`: the `None` sentinel for
  "the previous record was not `pub`" (`last_type`) mutated to `""`, both at
  its initial declaration and at its blank-line reset. `last_type` is
  compared **only** via `last_type == "pub"` in both functions — never `is
  None` — so any non-`"pub"` placeholder is indistinguishable from any other.
  One signature per site covers both functions, since the mutated line is
  textually identical in each: `last_type: Optional[str] = ""` (declaration)
  and `last_type = ""` (reset).
- `packaged_trusted`: `stripped.split(":", 1)[0]` mutated to drop the `1` or
  change it to `2`. `str.split(sep, n)[0]` is the substring before the
  *first* separator for any `n >= 1` (or unlimited) — the element at index
  `0` never depends on how many further splits happen, so no `n` other than
  `0` can change it. Signatures: `stripped.split(":", )` and
  `stripped.split(":", 2)`.

All ten of the equivalents above were confirmed by generating mutmut's own
mutated function bodies (`mutants/dasik/lib/actions/pacman_repos_state.py`,
one variant per mutant behind a `MUTANT_UNDER_TEST`-driven trampoline) and
calling them directly against a battery of hand-picked inputs — not assumed
from reading the diff. Every *other* survivor found the same way turned out
to be reachable through a concrete input and got a killing test instead (see
`tests/lib/actions/test_pacman_repos_conf.py` and
`test_pacman_repos_keyring.py`); none of the ten above ever produced a
different result for any input tried.

## CI

The `mutation` job in `.github/workflows/ci.yml` runs tier 1 on every PR and is a
**merge gate**: a real (non-equivalent) surviving mutant fails CI, alongside the
pytest coverage gate, mypy and bandit. `scripts/mutation.sh` exits non-zero only
on a real survivor and 0 on a mutation-clean run (equivalents are reported but
don't fail).

## Pre-push hook

`.githooks/pre-push` runs the same gates locally **before** a push — pytest +
coverage, mypy, bandit, and the set_math mutation tier — so a push can't land
something CI would reject. Enable it once per clone:

```bash
git config core.hooksPath .githooks
```

It uses the repo `.venv` if present. Bypass in an emergency with
`git push --no-verify` (discouraged — CI still gates). If a tool is missing it
tells you which extra to install (`pip install -e '.[dev,mut]'`).

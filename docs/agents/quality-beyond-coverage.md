# Quality beyond coverage

> Moved verbatim out of `AGENTS.md` on 2026-10-04 so that file fits the 32 KiB Codex reads
> by default. Its rules still bind: `AGENTS.md` lists the hard ones inline and says when to
> read this file. Edit the rule here, not a copy of it.

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

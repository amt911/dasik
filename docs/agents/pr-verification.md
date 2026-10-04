# Agentic PR verification

> Moved verbatim out of `AGENTS.md` on 2026-10-04 so that file fits the 32 KiB Codex reads
> by default. Its rules still bind: `AGENTS.md` lists the hard ones inline and says when to
> read this file. Edit the rule here, not a copy of it.

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
  [`docs/testing-without-a-vm.md`](../testing-without-a-vm.md). **Never run `apply`/`rollback` for real** —
  they partition disks and run `pacman`; assert intent via exit code/output/mocked
  `Command.execute`, never against real hardware. Attach the captured output to the verdict
  comment.
- **Two layers.** The pytest suite (Coverage gate ≥80%, `pytest --cov=dasik`) stays the hard merge
  gate; the agentic pass is advisory and never vetoes a merge on its own — but running it and
  posting the verdict comment is mandatory.
- **The verdict reads structure too.** Besides driving the CLI, it names what the diff does to the
  [Design principles](design-principles.md#design-principles--solid-applied-with-judgement): a new violation (an action's
  `plan()`/`is_needed()` importing `subprocess` directly instead of going through `Command.execute`,
  one more branch in a growing `if`/`elif` chain that `setup_actions()`'s registry should absorb
  instead) or a new speculative abstraction. Findings, not a veto — like the rest of the pass.
- **Hard limits.** The verdict awaits your close; the agent never merges. Scope `--allowedTools`;
  use `--dangerously-skip-permissions` only in a controlled local env — never against real
  hardware/disks (see [Safety](../../AGENTS.md#safety--this-tool-is-destructive)).

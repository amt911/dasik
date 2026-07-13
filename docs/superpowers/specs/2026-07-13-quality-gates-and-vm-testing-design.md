# Design: Quality gates + functional VM testing

Date: 2026-07-13
Status: Approved (brainstorming)
Author: paired session (Andrés + Claude, modo desatendido)

## Motivation

`CLAUDE.md` names several quality practices as "highest priority" that are not
yet implemented, and the user wants each new piece to be **functional and
independently testable** (ideally exercisable in a VM). This spec turns the
outstanding gaps into three sequenced, independently-mergeable PRs. Each PR is
functional on its own and ships its own tests / proof-of-function.

### What already exists (do not rebuild)

- CI (`.github/workflows/ci.yml`): pytest+coverage gate (≥80%, blocking),
  mypy (advisory), pip-audit (advisory), semgrep `p/python` (blocking).
- `__main__.py` already wires **all** verbs (`plan`/`apply`/`sync`/
  `generations`/`rollback`) to the v3 `Reconciler`. `plan` is the read-only
  dry-run. The legacy `ActionsHandler` is only the deprecated no-verb fallback.
  → CLAUDE.md's "entry-point gap" and "`--dry-run` not implemented" notes are
  **stale**; no work needed there.
- ~430 unit tests across 50 files under `tests/` mirroring `dasik/lib/`.

### What is missing (this spec)

1. Mutation testing (CLAUDE.md §Quality: "highest priority").
2. Property-based testing with Hypothesis (CLAUDE.md §Quality: "highest priority").
3. A way to functionally test the installer — a VM harness (user request).

## The idempotency core (test target for PR1 + PR2)

`dasik/lib/state/set_math.py::compute_changes` is the pure heart of the
reconciliation model. Given four sets per domain:

- `D` desired (config), `M` managed (manifest / owned by dasik), `A` actual
  (system), `F` forced-off.
- `INSTALL = D \ A`, `REMOVE = M \ D` (destructive), `DRIFT = A \ D \ M \ F`
  (reported, untouched), plus forced removals `(F ∩ A) \ (M \ D)`.

Primary safety property: **removal is scoped to `M`** — manually-installed
items surface as drift, never as automatic removal. This is exactly the
comparison/boundary logic where a flipped `>`/`>=`/`-`/`&` is a covered-but-wrong
bug. It is the first mutation + property target.

---

## PR1 — Mutation testing (mutmut)

**Goal:** an automated backstop that injects bugs (`>`→`>=`, drop a line, flip a
boolean) into the idempotency logic and fails if no test catches them.

**Components**

- `pyproject.toml`: new optional-dependency extra `mut = ["mutmut"]` (pinned).
  Runtime deps stay two; `dev` stays lean so the CI test job is unaffected.
- Mutation config (in `pyproject.toml` `[tool.mutmut]` or `setup.cfg`, whichever
  the pinned mutmut version reads): `paths_to_mutate` scoped to
  `dasik/lib/state/set_math.py` and the set-math region of
  `dasik/lib/reconciler/reconciler.py` first; the actions' `is_needed`/`verify`
  are a documented second tier. `tests_command` runs a fast subset.
- `scripts/mutation.sh` — thin wrapper: `mutmut run` then `mutmut results`.
- `docs/mutation-testing.md` — how to run, how to read survivors, how to add a
  test to kill one. Supersedes vague references.
- CI: an **advisory** `mutation` job (mutmut over `set_math` + reconciler
  set-math only, small enough to be fast) that prints survivors and never blocks
  merge (matches the existing advisory-job pattern).

**Proof of function (the deliverable's own test)**

Run mutmut, capture the baseline survivor set, and **kill the genuine
survivors** by strengthening test asserts. Those survivor-killing tests ship in
the PR. A survivor that is semantically-equivalent (no behavioural change) is
documented as such rather than "killed". Acceptance: the mutation score on
`set_math.py` reaches 100% killed-or-justified, demonstrating the harness finds
real gaps.

**Tool risk:** mutmut 3.x changed its CLI/config. Pin a known-good version; if
it is troublesome on this codebase, fall back to `cosmic-ray` (same extra name,
same script/doc surface). Decided during implementation via a quick smoke run.

---

## PR2 — Property-based tests (Hypothesis)

**Goal:** automated proof of the idempotency promise via generated inputs.

**Components**

- `pyproject.toml`: add `hypothesis` to the `dev` extra.
- `tests/lib/state/test_set_math_properties.py`:
  - **No-op invariant:** for any domain and any set `S`, `compute_changes(
    desired=S, managed=S, actual=S)` → `changes == []` and `drift == []`.
    (Re-run of an already-converged domain changes nothing.)
  - **Removal scoped to M:** generated `A` items that are in neither `D` nor `M`
    (and not forced) never appear as a REMOVE change — they appear only in
    `drift`.
  - **Convergence:** applying the computed changes to `A` (install adds, remove
    subtracts) and recomputing yields empty changes — one apply converges.
  - **Partition:** every element of `A ∪ D ∪ M` lands in exactly one of
    {installed, removed, drift, untouched}; no element is both installed and
    removed.
  - **Forced precondition `D ∩ F = ∅`** respected; forced-off present items are
    removed, forced items excluded from drift.
- `tests/lib/state/test_config_roundtrip.py`: a config dict that passes
  `JsonModel` validation, when written by `ConfigWriter` and re-read, round-trips
  for the covered fields.
- `tests/lib/reconciler/test_reconciler_idempotent_property.py`: with a mocked
  `Target` reporting `actual == desired`, `build_plan()` returns an empty plan
  (property over generated small configs).

**Testable:** pure `pytest`, runs in existing CI, no VM. Hypothesis shrinks
failures to a minimal counterexample.

---

## PR3 — VM test harness (two layers, configurable RAM ≤ 8 GB)

**Goal:** functionally exercise the destructive installer safely.

**Layer A — light (loopback image, no boot)**

- `scripts/vmtest/loopback-smoke.sh`: create a sparse image
  (`qemu-img create`/`truncate`), attach via `losetup` (or `qemu-nbd`), run
  `dasik plan` then `dasik apply --yes --target <mnt>` against a minimal config
  whose `disks` point at the **loop device**, then assert partitions/filesystems
  landed. No kernel boot → fast. Requires root (losetup); documented.

**Layer B — full QEMU (boot Arch ISO, install, reboot, assert)**

- `scripts/vmtest/qemu-install.sh`: fresh `qcow2`, boot the Arch ISO in
  QEMU+KVM, drive `dasik apply` inside the guest against a minimal config,
  reboot into the installed system, assert it boots and key state (hostname,
  a user, a package) is present.
- **Configurable** via env + flags, with safe defaults and the 8 GB cap:
  - `DASIK_VM_RAM` MB, default `2048`, **hard-capped at `8192`** (script errors
    above the cap).
  - `DASIK_VM_CPUS` default `2`; `DASIK_VM_DISK` size default `16G`;
    `DASIK_VM_ISO` path to the Arch ISO (required for Layer B; script prints how
    to fetch it if missing).

**Safety guardrails (non-negotiable)**

- Both layers operate only on the loop image / qcow2 they create. A guard
  refuses to run if the resolved target device is not a `/dev/loop*` or a
  qcow2/nbd — never a physical disk. The config used sets destructive `format`
  flags **only** against the virtual device.
- Scripts print the exact device they will write and abort on anything
  resembling a real disk (`/dev/sd*`, `/dev/nvme*`, `/dev/vd*` that is not the
  guest's own virtual disk).

**Docs / CI**

- `docs/vm-testing.md`: prerequisites (qemu, KVM, root for loopback, ISO), how
  to run each layer, the env knobs, and the safety model. Supersedes the
  aspirational claims in `HOW-TO-TEST.md` / `INTEGRATION-COMPLETE.md` (note them
  as superseded).
- CI: an **opt-in** job (`workflow_dispatch`, and/or a `vm` label) — GitHub
  hosted runners lack nested KVM, so this is documented as a local-run harness
  on a KVM-capable host. The loopback layer may additionally run in a
  privileged self-hosted context if one ever exists; not assumed.

---

## Sequencing & delivery

- One branch + PR per item: `feat/mutation-testing` → `feat/property-tests` →
  `feat/vm-test-harness`. Each merged before the next starts.
- Every PR: pytest+coverage green locally, then the mandatory agentic PR
  verification pass posted as a `gh pr comment` before merge (CLAUDE.md
  §Agentic PR verification).
- Branches are **not deleted** after merge (kept on remote) so a PR can be
  recreated if integration goes wrong. Never commit to / push `main` directly;
  never force-push; never `git merge` locally — integration is via `gh pr merge`
  of a green PR only.

## Non-goals

- No new runtime dependencies (`pydantic`, `colorama` stay the only two).
- No lowering of the 80% coverage gate.
- No changes to installer behaviour — this spec is tests + tooling + docs only,
  except the VM harness scripts (new, non-shipped) and any survivor-killing
  asserts.

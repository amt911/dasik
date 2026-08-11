# Dasik User Wiki Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a task-oriented user wiki that documents every current CLI verb and JSON configuration parameter, config splitting/secrets, and the convergence/state workflows of dasik.

**Architecture:** Keep the existing `docs/config-reference.md` as a compatibility/reference page, but add a focused `docs/wiki/` information architecture. Derive claims from CLI/parser/models/preflight/state code rather than historical docs, and link the root README into the new hub.

**Tech Stack:** Markdown documentation; Python/Pydantic source as schema truth; argparse CLI; GitHub Markdown relative links.

## Global Constraints

- Documentation only: do not change runtime behavior.
- Document `main` at branch creation time; do not include unmerged PR features.
- `archinstall/` is legacy/reference and must not define the documented JSON format.
- Never run real `apply` or `rollback` against hardware for verification.
- Preserve existing `docs/config-reference.md` links.
- A PR must contain a concrete **How to test manually** section.

---

### Task 1: Wiki landing page and CLI reference

**Files:**
- Create: `docs/wiki/README.md`
- Create: `docs/wiki/cli.md`

**Interfaces:**
- Consumes: `dasik/__main__.py`, `README.md`, target preflight behavior.
- Produces: stable entry point and verb reference linked by the remaining pages.

- [ ] **Step 1:** Write a landing page organized by user task: validate, preview, install/manage, capture, recover, split config.
- [ ] **Step 2:** Document all global CLI flags and all seven verbs with positional arguments, options, defaults, target roots, safety classification and examples.
- [ ] **Step 3:** Document logging defaults and the removed bare `dasik <config>` invocation.
- [ ] **Step 4:** Cross-check every CLI spelling/default against `_build_parser()` and `_KNOWN_VERBS`.
- [ ] **Step 5:** Commit the two pages.

### Task 2: Exhaustive JSON configuration reference

**Files:**
- Create: `docs/wiki/configuration.md`

**Interfaces:**
- Consumes: every model in `dasik/lib/models/`, `dasik/lib/expand/`, `dasik/lib/validation/preflight.py`, disk and package semantics.
- Produces: exhaustive field/nested-field lookup for JSON authors.

- [ ] **Step 1:** Add top-level field table with type/default/purpose.
- [ ] **Step 2:** Add nested field tables for disks/partitions/Btrfs, users, packages/policy/sources, systemd/pacman, local files, zram, sudo/cpu/reflector and all feature blocks.
- [ ] **Step 3:** Add model validation constraints (enums, regex-like restrictions, `rest` ordering, LUKS requirements, systemd overlap, package-source SHA/URL/subdir rules, file paths/modes, CPU mode restrictions, sudo rule restrictions).
- [ ] **Step 4:** Add derived behavior from expand toggles and cross-field preflight warnings/errors that materially affect users.
- [ ] **Step 5:** State clearly what `sync` can/cannot round-trip and point to workflow docs for the mental model.
- [ ] **Step 6:** Commit the page.

### Task 3: Config split and secrets guide

**Files:**
- Create: `docs/wiki/config-splitting.md`

**Interfaces:**
- Consumes: `dasik/lib/json_parser/includes.py`, tracked split examples.
- Produces: authoritative guide to `$include`, `$include_text`, `$include_line`, `$concat`.

- [ ] **Step 1:** Document all four directives with input/output examples.
- [ ] **Step 2:** Document relative-path, no-absolute-path, no-`..`, single-key, nested-resolution and cycle rules.
- [ ] **Step 3:** Explain `$include_line` for hashes/passphrases and why `$include_text` is wrong for line secrets.
- [ ] **Step 4:** Explain that `check`/`plan`/`apply` assemble before validation and `sync` refuses assembled configs to avoid flattening them.
- [ ] **Step 5:** Link `config/split-example/`, `config/test-config-split/` and `config/laptop-p14s-split/`.
- [ ] **Step 6:** Commit the page.

### Task 4: Workflows, idempotency, sync and generations

**Files:**
- Create: `docs/wiki/workflows.md`

**Interfaces:**
- Consumes: `Reconciler`, state/generation stores, CLI flows, expansion/subtraction semantics.
- Produces: user mental model for safe everyday operation.

- [ ] **Step 1:** Document install-from-ISO (`--target /mnt`) versus day-2 host management (`--target /`).
- [ ] **Step 2:** Explain `check → plan → apply`, ownership/idempotency, derived toggle contributions and why a second apply should be a no-op.
- [ ] **Step 3:** Explain `sync` as system → config, backups, include refusal and contribution subtraction.
- [ ] **Step 4:** Explain complete and partial generations, `generations`, default/explicit rollback behavior and partial-generation refusal.
- [ ] **Step 5:** Add practical safe recipes and failure recovery guidance without encouraging real destructive tests.
- [ ] **Step 6:** Commit the page.

### Task 5: Integrate with existing docs and verify

**Files:**
- Modify: `README.md`
- Modify: `docs/config-reference.md`

**Interfaces:**
- Consumes: pages from Tasks 1–4.
- Produces: discoverable, non-duplicative documentation entry points.

- [ ] **Step 1:** Add a prominent README wiki link and task links while keeping the existing capture/VM guide.
- [ ] **Step 2:** Add a wiki pointer to `docs/config-reference.md`; correct the claim that unknown modeled-section keys are rejected, because current Pydantic models use the default extra-key behavior rather than `extra='forbid'`.
- [ ] **Step 3:** Correct split wording where necessary so `$include_line` is included as a first-class directive.
- [ ] **Step 4:** Fetch every changed file from `agent/dasik-wiki` and inspect for complete content and valid relative links.
- [ ] **Step 5:** Compare `main...agent/dasik-wiki`; expected changed paths are documentation only.
- [ ] **Step 6:** Open a draft PR against `main` with scope, accuracy notes and a concrete manual test plan.

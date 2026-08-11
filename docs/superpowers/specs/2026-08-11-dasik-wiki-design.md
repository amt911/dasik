# Dasik User Wiki Design

## Goal

Create a user-facing documentation hub for the current `main` implementation of dasik that makes the CLI, JSON configuration surface, config splitting, and state/generation workflows discoverable without reading source code.

## Scope and source of truth

This is a documentation-only change. It documents the implementation that exists on `main` at the branch point; it deliberately does not document unmerged features from other branches or PRs.

The source-of-truth order is:

1. `dasik/__main__.py` for verbs, positional arguments, flags, defaults, logging and target behavior.
2. `dasik/lib/models/` for JSON fields, types, defaults and model-level validation.
3. `dasik/lib/json_parser/includes.py` for `$include`, `$include_text`, `$include_line` and `$concat`.
4. `dasik/lib/expand/` for feature-toggle side effects and derived packages/units/files.
5. `dasik/lib/validation/preflight.py` for cross-field errors and warnings.
6. Actions/state code for `plan`, `apply`, `sync`, generations, partial generations and rollback semantics.
7. Tracked configs under `config/` for realistic examples.

`archinstall/` is explicitly out of scope because `CLAUDE.md` identifies it as reference/legacy material rather than the active implementation.

## Information architecture

Create `docs/wiki/` with these pages:

- `README.md` — landing page and task-oriented navigation.
- `cli.md` — every current verb (`plan`, `apply`, `sync`, `generations`, `rollback`, `check`, `hash-password`), global flags, per-verb parameters, defaults, destructive/read-only classification and common invocation patterns.
- `configuration.md` — every accepted top-level JSON field and every modeled nested field, including types, defaults, allowed enum values, validation constraints, derived behavior and `sync` notes.
- `config-splitting.md` — the four include directives, path/cycle rules, nesting, secret handling, examples, and the important fact that `sync` refuses split configs because it would flatten them.
- `workflows.md` — install-from-ISO vs day-2 management, `check → plan → apply`, `sync`, manifests/generations, partial generations, rollback and idempotency/ownership mental models.

Keep `docs/config-reference.md` for backward-compatible links. Add a pointer to the wiki and correct any wording that conflicts with current behavior rather than deleting the established reference.

Update the root `README.md` so new users encounter the wiki before the older deep-dive guides.

## Accuracy rules

- Document actual defaults, not intended defaults.
- Make destructive behavior prominent around `apply`, disk wiping/formatting and rollback.
- Distinguish fields users declare directly from resources dasik derives through expansion (packages, systemd units, files, kernel parameters).
- Describe `sync` as system → config and `plan`/`apply` as config → system.
- Explain that `plan`/`apply` default to `/mnt`, while `sync`/`generations`/`rollback` default to `/`.
- Explain that split directives are resolved before Pydantic validation and preflight.
- Do not promise rejection of arbitrary extra JSON keys unless the model actually configures `extra='forbid'`; the current Pydantic models do not.
- Mention cross-field preflight cases that are safety-critical or commonly surprising (missing group/provider, display-manager provider, sudo provider, conflicting CPU policy, crypttab risks, UEFI requirement for installs).

## Verification

Because this is documentation-only, verification is:

1. Fetch every added/modified page from the feature branch and inspect it for truncation.
2. Compare `main...agent/dasik-wiki` and confirm only documentation files changed.
3. Check all relative links introduced by the wiki against repository paths.
4. Verify CLI verb/flag tables against `dasik/__main__.py` and JSON tables against `JsonModel` plus all nested models.
5. Include a manual test plan in the PR using `dasik --help`, `dasik check`, split-config validation, and a non-destructive `plan` example.

No real `apply` or `rollback` will be run as part of this documentation PR.
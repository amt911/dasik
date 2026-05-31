# Design: migrate `systemd` to the v3 contract (+ explicit disables)

Date: 2026-05-31
Status: approved (design), pending implementation plan

## Context

After Plan 5 (sync/generations/rollback), `packages` is the **only** domain that
participates in the v3 `plan`/`apply`/`sync` round-trip (`Reconciler` drives v3
actions via `plan(managed)` / `apply(changes)` / `import_state(managed)`). All other
actions remain legacy `is_needed`/`execute`. This slice adds a **second** v3 domain —
`systemd` — establishing the multi-domain pattern, and adds a new capability requested
during brainstorming: an explicit **disable list** so a unit can be turned off even when
it is enabled and even when dasik never enabled it.

`set_math.compute_changes` already accepts `op_install`/`op_remove` overrides
(`Op.ENABLE`/`Op.DISABLE` exist), so the core was built for this. `PackagesAction` is the
reference v3 action.

## Decisions (from brainstorming)

- **Domain:** `systemd` first. `users`/`files` deferred (attribute/content complexity →
  their own specs).
- **`actual()` (A):** *all* enabled unit files (`systemctl list-unit-files
  --state=enabled`) — symmetric with `packages` (`pacman -Qqe`). Drift is therefore all
  enabled units neither declared nor owned; `sync` captures them (the user accepted that
  `sync` will be verbose for systemd).
- **Explicit disables:** new `disable_units` config list. Modeled via **Enfoque B** —
  extend `compute_changes` with a `forced` parameter (reusable later for files `DELETE` /
  users), rather than keeping the logic local to the action.

## 1. Config model — `SystemdModel`

Add `disable_units`:

```jsonc
"systemd": {
  "enable_units":   ["NetworkManager.service", "fstrim.timer"],
  "enable_sockets": ["cups.socket"],
  "disable_units":  ["bluetooth.service"]
}
```

- `disable_units: List[str] = Field(default_factory=list)`.
- **Validator:** reject when `(enable_units ∪ enable_sockets) ∩ disable_units ≠ ∅`
  (a unit cannot be declared both enabled and disabled) → pydantic `ValueError`.

The `enable_units`/`enable_sockets` split is **kept** (back-compat with the existing
`config/install-megamix.json` sample and readability). Internally the action flattens
`D_on = enable_units + enable_sockets`.

## 2. `set_math.compute_changes` — `forced` parameter

```python
def compute_changes(
    domain, *, desired, managed, actual,
    op_install=Op.INSTALL, op_remove=Op.REMOVE,
    forced=(),                      # NEW: ensure-removed/disabled set, regardless of M
) -> tuple[list[Change], list[str]]:
```

Semantics (D = desired, M = managed, A = actual, F = forced):

| Block | Set | Op | reason |
| --- | --- | --- | --- |
| install/enable | `D \ A` | `op_install` | — |
| remove-owned | `M \ D` | `op_remove` | `"no longer declared"` |
| remove-forced | `(F ∩ A) \ (M \ D)` | `op_remove` | `"explicitly disabled"` |
| drift | `A \ D \ M \ F` | — | reported only |

- Removal block = owned ∪ forced, **deduped** (forced excludes anything already emitted as
  owned), each block sorted by item.
- Precondition `D ∩ F = ∅` (guaranteed by the model validator; `compute_changes`
  documents it, does not re-validate).
- `forced=()` default ⇒ `PackagesAction` and every existing caller are unchanged.

## 3. `SystemdAction` v3 methods

- `actual() -> set[str]`: `Command.execute("systemctl",
  ["list-unit-files", "--state=enabled", "--no-legend"], target=ctx.target)`, parse the
  first column (unit name) of each line. Returns `set()` when `context`/`target` is None
  (legacy call-sites), mirroring `PackagesAction`.
- `D_on = enable_units + enable_sockets`, `D_off = disable_units`.
- `plan(managed)`:
  ```python
  changes, _drift = compute_changes(
      "systemd", desired=self._d_on, managed=managed, actual=self.actual(),
      op_install=Op.ENABLE, op_remove=Op.DISABLE, forced=self._d_off,
  )
  return changes
  ```
- `apply(changes)`: ENABLE → `systemctl enable <unit>`; DISABLE → `systemctl disable
  <unit>`. Enables before disables (additive first, mirrors packages). No-op on empty;
  no-op when `context.target` is None.
- `managed_keys() -> {"systemd": D_on}` — M tracks units dasik **enabled**; forced
  disables are *not* owned (dasik never re-enables them).
- `import_state(managed) -> {"systemd": {...}}` for `sync`:
  - survivors: declared entries kept (intent is never dropped, even if not currently
    enabled), minus owned-and-vanished (`M \ A`);
  - drift (`A \ D_on \ M \ D_off`) appended, **routed by suffix**: `*.socket` →
    `enable_sockets`, everything else → `enable_units`;
  - `disable_units` preserved verbatim (pure intent, not derived from A).
- Legacy `is_needed`/`execute`/`verify` are **kept** for the old `ActionExecutor` path and
  extended to also honor `disable_units`, so both paths behave consistently.
- Registry: already `register_action(SystemdAction, config_key="systemd",
  is_optional=True)` — no change needed.

## 4. Testing (TDD, 80% gate)

- `set_math` (`forced`): forced disable of a non-owned unit; dedupe when a unit is both
  `M \ D` and forced; drift excludes forced; **regression** — no `forced` ⇒ identical to
  today (packages path).
- `SystemdAction` v3: `actual()` parsing (+ empty/None-target); `plan()` enable/disable/
  forced/drift permutations; `apply()` routes enable vs disable and ordering;
  `managed_keys()`; `import_state()` drift-routing, survivors, vanished-owned.
- `SystemdModel`: accepts valid config; rejects enable∩disable overlap.

## Out of scope (future slices)

- `users` / `files` (drop_files) → v3 (attribute- and content-addressed; distinct model).
- Multi-domain actions (`Reconciler._domain_for` still raises on >1 domain).
- Disabling via `systemctl mask` (only `disable` here).

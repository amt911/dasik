# Design: scalar v3 domains — base pattern + `timezone`

Date: 2026-05-31
Status: approved (design), pending implementation plan

## Context

The v3 round-trip (`plan`/`apply`/`sync`) covers four **set** domains: `packages`,
`systemd`, `users`, `files`. `Reconciler.build_plan` skips non-v3 actions
(`if not cls.is_v3(): continue`), so every other action — `timezone`, `locale`, `network`,
`pacman`, `mkinitcpio`, `kernel_cmdline`, … — is invisible to `dasik plan/apply/sync`.

These are **not sets**. A timezone is one composite value (`region/city`). Set-math models a
value change as INSTALL(new) + REMOVE(old) — two changes for what is conceptually one
`MODIFY`. So scalar domains need a different v3 shape.

This slice establishes a reusable **scalar v3 pattern** and migrates `timezone` as the
exemplar. `locale`/`network`/`pacman`/`mkinitcpio` follow in later slices on the same base;
`kernel_cmdline` is a *set* of params and will migrate packages-style, not here.

## Decisions (from brainstorming)

- **Scope:** `ScalarV3Action` base + `timezone` only.
- **Semantics:** a scalar domain emits at most one `Op.MODIFY` (set/replace the value). No
  CREATE/DELETE — you never "uninstall" a timezone. `MODIFY` is non-destructive (no `y/N`
  gate). `actual()` returns a one-element set `{value}` (or `set()` when unset) so the
  reconciler's `sorted(action.actual())` plumbing works.
- **Pattern (Approach A):** a base class implements the generic v3 methods over four hooks;
  subclasses provide only the domain-specific glue. DRY across the remaining scalars.

## 1. `ScalarV3Action` base — `dasik/lib/actions/scalar_action.py`

```python
class ScalarV3Action(AbstractAction):
    """Base for v3 domains whose state is a single value (not a set)."""
    _DOMAIN: str = ""               # subclass sets this

    # --- subclass hooks ---
    def _desired_value(self): ...       # -> str | None  (from config)
    def _actual_value(self): ...        # -> str | None  (from system, target-aware)
    def _set_value(self): ...           # apply the desired value (shell-out)
    def _import_fragment(self, value):  # -> dict  (value -> config fragment)
        ...

    # --- generic v3 contract ---
    def actual(self) -> set:
        v = self._actual_value()
        return {v} if v else set()

    def plan(self, managed):
        d = self._desired_value()
        if d and d != self._actual_value():
            return [Change(self._DOMAIN, Op.MODIFY, d, reason="set")]
        return []

    def apply(self, changes) -> None:
        target = getattr(self.context, "target", None) if self.context else None
        if changes and target is not None:
            self._set_value()

    def managed_keys(self) -> dict:
        d = self._desired_value()
        return {self._DOMAIN: [d] if d else []}

    def import_state(self, managed=None) -> dict:
        v = self._actual_value() or self._desired_value()
        return self._import_fragment(v) if v else {}
```

The four hooks raise `NotImplementedError` by default (documented), so a subclass that
forgets one fails loudly. `is_v3()` returns True automatically (the base overrides `plan`).

## 2. `set_math` / `Op` — unchanged

`Op.MODIFY` already exists and is not in `_DESTRUCTIVE_OPS`.

## 3. `TimezoneAction` → subclass `ScalarV3Action`

- `_DOMAIN = "timezone"`.
- Constructor keeps reading `region`/`city` from its config dict.
- `_desired_value()` → `f"{region}/{city}"`.
- `_actual_value()` → read the `/etc/localtime` symlink (target-aware via
  `target.path("/etc/localtime")`); parse `…/zoneinfo/<Region>/<City>` → `"Region/City"`;
  return `None` when missing / not a symlink / unparseable.
- `_set_value()` → `Command.execute("ln", ["-sf",
  f"/usr/share/zoneinfo/{value}", "/etc/localtime"], target=target)` then
  `Command.execute("hwclock", ["--systohc"], target=target)`.
- `_import_fragment(value)` → `{"timezone": {"region": r, "city": c}}` from `value.split("/", 1)`.
- Legacy `is_needed`/`execute`/`verify` are kept for the old `ActionExecutor` path, refactored
  to reuse the new target-aware helpers (so both paths agree).
- Registration unchanged (`config_key="timezone"`, optional).

### Target-awareness note

The current `TimezoneAction` hardcodes `/mnt/etc/localtime`. The v3 helpers resolve through
`context.target` (so day-2 `/` and install `/mnt` both work). When no target is present
(legacy direct construction), fall back to `/mnt` to preserve existing behaviour.

## 4. Testing (TDD, 80% gate)

- `ScalarV3Action` (via a fake subclass with in-memory value):
  - `actual()` returns `{value}` / `set()`.
  - `plan()` → one `MODIFY` when desired≠actual; `[]` when equal; `[]` when no desired.
  - `apply()` calls `_set_value()` only when there are changes **and** a target; no-op otherwise.
  - `managed_keys()` → `{domain: [value]}` / `{domain: []}`.
  - `import_state()` → `_import_fragment(actual or desired)`.
- `TimezoneAction`:
  - `_desired_value()` joins region/city.
  - `_actual_value()` parses a mocked symlink; `None` on missing/non-symlink.
  - `_set_value()` issues the `ln -sf` + `hwclock` calls with the right args/target.
  - `_import_fragment()` splits back to `{region, city}`.
  - `is_v3()` is True; `plan()` empty when the link already matches.

## Out of scope (future slices)

- `locale` / `network` / `pacman` / `mkinitcpio` on the same base (mixed list+scalar shapes).
- `kernel_cmdline` (a param *set* → packages-style migration).
- Rolling a scalar back to a previously-recorded value (generations already snapshot config).

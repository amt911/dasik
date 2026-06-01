# Design: composite v3 base + migrate `locale`

Date: 2026-05-31
Status: approved (design), pending implementation plan

## Context

The v3 domains so far are flat **sets** (`packages`/`systemd`/`users`/`files`/
`kernel_cmdline`) or single **scalars** (`timezone`, `initramfs` — on `ScalarV3Action`).
The remaining config sections — `locale`, `network`, `pacman` — are **composite**: several
related fields that move together. `LocaleAction` is `{selected_locales: list,
desired_locale: str, desired_tty_layout: str}` spread across three files
(`locale.gen`, `locale.conf`, `vconsole.conf`). It is still legacy (`is_needed`/`execute`)
so the reconciler skips it.

A composite is neither a name-set (set-math would model a value change as INSTALL+REMOVE)
nor a single scalar. Its idempotency question is "does the whole record match?" — one
`MODIFY` when any field drifts, apply rewrites everything. That is exactly the
`ScalarV3Action` shape applied to a **canonically-serialized** state value.

This slice adds a thin `CompositeV3Action` base (dict-shaped state over `ScalarV3Action`)
and migrates `locale` onto it. `pacman` and `network` follow on the same base in the next
slice.

## Decisions (from brainstorming)

- Composite = single serialized state value → reuse `ScalarV3Action` (one `Op.MODIFY` on
  mismatch; no set-math). A thin `CompositeV3Action` makes subclasses work on dicts.
- `plan()` is overridden in the base to emit a `MODIFY` whose item lists the **changed
  fields** (clean render), not the raw serialized blob.
- `sync` round-trips the live composite (`import_state` reads the system).
- Empty-config bootstrap is inherited: `ScalarV3Action.empty_config()` returns `{}` (added
  in the #75 fix), which is the right shape for a dict-based composite.

## 1. `CompositeV3Action` — `dasik/lib/actions/composite_action.py`

```python
class CompositeV3Action(ScalarV3Action):
    """v3 contract for multi-field (composite) domains.

    State is a dict; equality is compared via a canonical JSON serialization so
    a converged composite yields no change (idempotent). Emits a single MODIFY
    listing the changed fields. Subclasses implement the dict/IO hooks.
    """
    # subclass hooks
    def _desired_state(self) -> dict: ...
    def _actual_state(self):  # -> dict | None  (None when unconfigured)
        ...
    # _set_value() and _import_fragment() are still subclass-provided

    @staticmethod
    def _serialize(state: dict) -> str:
        return json.dumps(state, sort_keys=True)

    # bridge to ScalarV3Action's value-based machinery
    def _desired_value(self):
        return self._serialize(self._desired_state())

    def _actual_value(self):
        s = self._actual_state()
        return self._serialize(s) if s is not None else None

    def plan(self, managed):
        desired = self._desired_state()
        actual = self._actual_state()
        if actual == desired:
            return []
        changed = sorted(desired) if actual is None else sorted(
            k for k in desired if desired.get(k) != actual.get(k))
        item = ",".join(changed) or self._DOMAIN
        return [Change(self._DOMAIN, Op.MODIFY, item, reason="config")]
```

`actual()` / `managed_keys()` / `import_state()` / `is_needed` / `execute` / `verify` /
`empty_config()` are inherited from `ScalarV3Action` (value-based). `is_v3()` is True
(`plan` overridden). The manifest records the serialized state under the domain key.

## 2. `LocaleAction(CompositeV3Action)`

- `_DOMAIN = "locales"`; registered `config_key="locales"` (unchanged) → becomes v3.
- Constructor reads the locales sub-dict with `.get` defaults (tolerates the `{}` bootstrap).
- Target-aware paths via `context.target` (`target.path("/etc/locale.gen")` …) with the
  legacy `/mnt` fallback when there is no target.
- `_desired_state()` → `{"selected_locales": sorted(selected), "desired_locale": locale,
  "desired_tty_layout": tty}`.
- `_actual_state()` → read the three files:
  - `selected_locales`: the uncommented `^[a-z]+_… …` lines of `locale.gen`, sorted;
  - `desired_locale`: the `LANG=` value from `locale.conf`;
  - `desired_tty_layout`: the `KEYMAP=` value from `vconsole.conf`.
  Returns `None` when `locale.conf` or `vconsole.conf` is missing (unconfigured → plan
  fires).
- `_set_value()` → comment all entries, uncomment `selected_locales` in `locale.gen`, write
  `LANG=` to `locale.conf` and `KEYMAP=` to `vconsole.conf`, run `locale-gen` (target-aware,
  `/mnt` fallback). Marked `# pragma: no cover` (writes /etc + runs locale-gen).
- `_import_fragment(_value)` → `{"locales": self._actual_state() or self._desired_state()}`
  (capture live state for `sync`).
- Drop the action's own `is_needed`/`execute`/`verify` (the base provides them via `plan`).

## 3. Testing (TDD, 80% gate)

- `CompositeV3Action` (via a fake dict-state subclass):
  - `plan()` → empty when states equal; one `MODIFY` listing changed keys when they differ;
    all keys listed when `actual` is `None`.
  - inherited `actual()` wraps the serialized value; `managed_keys()` carries it; `is_v3()`
    True; `empty_config()` is `{}`.
- `LocaleAction`:
  - `_desired_state()` shape (sorted locales).
  - `_actual_state()` parses the three files (mocked); `None` when `locale.conf`/`vconsole`
    absent.
  - `plan()` MODIFY when LANG/KEYMAP/locales differ; empty when converged.
  - `_import_fragment()` returns the live state under `"locales"`.
  - `is_v3()` True.

## Out of scope (future slices)

- `pacman` (boolean options) and `network` (hostname + hosts) on the same `CompositeV3Action`
  base — next slice.
- Per-field MODIFY granularity in `apply` (apply rewrites the whole composite; fine here).

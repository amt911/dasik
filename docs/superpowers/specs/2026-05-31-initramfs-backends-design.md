# Design: initramfs generator backends (mkinitcpio + dracut) on the scalar v3 base

Date: 2026-05-31
Status: approved (design), pending implementation plan

## Context

`MkinitcpioAction` configures `/etc/mkinitcpio.conf` HOOKS (derived from the disk config:
encryption → `systemd`/`sd-vconsole`/`sd-encrypt`, btrfs → `btrfs` hook, keyboard-before-
autodetect) and runs `mkinitcpio -P`. It is **legacy** (`is_needed`/`execute`), so
`dasik plan/apply/sync` skips it. It is also **hardcoded to mkinitcpio** — Arch also ships
`dracut` (issue #53) and others.

This slice does two things at once:
1. Migrate initramfs configuration onto the v3 contract (via the `ScalarV3Action` base from
   Plan 9) so `apply` covers it. The desired initramfs config is a single derived value, so
   it fits the scalar shape.
2. Generalize over the **generator**: a pluggable `InitramfsBackend` (mkinitcpio, dracut,
   extensible) chosen by a new `initramfs` config field.

The bootloader-generations feature explored earlier is **deferred** — it is a multi-component
subsystem (per-generation kernel/initramfs capture + boot-time apply hook + optional btrfs
fast-path) whose core (an early-boot agent) cannot be meaningfully tested in this mock-only
repo. Recorded as a roadmap item.

## Decisions (from brainstorming)

- Backend-pluggable (`mkinitcpio` + `dracut` this slice, interface extensible to others).
- Single derived value per backend (serialized) → reuse `ScalarV3Action`.
- Selector: new root field `initramfs: str = "mkinitcpio"` (Arch default).
- `sync` does not round-trip initramfs (it is derived from the disk config, not a declared
  section) → `_import_fragment` returns `{}`.

## 1. Config model

`JsonModel` gains a root field:

```python
initramfs: str = "mkinitcpio"   # "mkinitcpio" | "dracut"
```

No validator beyond the factory raising on an unknown generator (so a typo fails loudly at
action construction, with a clear message).

## 2. Backends — `dasik/lib/actions/initramfs/`

```python
class InitramfsBackend:
    """Compute + apply the initramfs configuration for one generator."""
    def __init__(self, config: dict, target): ...
    def desired_value(self) -> str: ...          # serialized desired config
    def actual_value(self) -> str | None: ...    # current on-disk config, or None
    def apply(self) -> None: ...                  # write config + regenerate
```

- **`MkinitcpioBackend`** (ports the existing `MkinitcpioAction` logic):
  - `desired_value()` → the computed `HOOKS=(...)` line content (space-joined hooks) from the
    existing `_detect_encryption`/`_detect_root_fs`/`_compute_desired_hooks` logic.
  - `actual_value()` → the current `HOOKS=(...)` content from `/etc/mkinitcpio.conf`
    (target-aware); `None` if the file or HOOKS line is absent.
  - `apply()` → rewrite the `HOOKS=` line (old one commented) + `mkinitcpio -P`.
- **`DracutBackend`** (new):
  - `desired_value()` → the desired `/etc/dracut.conf.d/dasik.conf` content. From the disk
    config: encryption → `add_dracutmodules+=" crypt "`, btrfs root →
    `add_dracutmodules+=" btrfs "`; always a stable, sorted, deterministic body.
  - `actual_value()` → current `/etc/dracut.conf.d/dasik.conf` content (target-aware);
    `None` if absent.
  - `apply()` → write `dasik.conf` + `dracut --regenerate-all --force`.
- **Factory** `make_backend(name, config, target)`: `{"mkinitcpio": MkinitcpioBackend,
  "dracut": DracutBackend}`; raises `ValueError` on an unknown name.

Target-awareness: backends resolve paths via `target.path(...)` and run commands with
`Command.execute(..., target=target)`, falling back to `/mnt` / `run_as_chroot=True` when no
target (legacy call-site), consistent with the other migrated actions.

## 3. `InitramfsAction(ScalarV3Action)` — `dasik/lib/actions/initramfs_action.py`

- `_DOMAIN = "initramfs"`.
- `__init__(config, context)`: `config` is the root dict; reads `config.get("initramfs",
  "mkinitcpio")` and builds the backend via the factory with the root config + target.
- Hooks delegate to the backend:
  - `_desired_value()` → `backend.desired_value()`
  - `_actual_value()` → `backend.actual_value()`
  - `_set_value()` → `backend.apply()`
  - `_import_fragment(value)` → `{}` (derived; nothing to sync back).
- `is_needed`/`execute`/`verify` come from `ScalarV3Action`.
- **Registration:** in `setup_actions()`, replace the `MkinitcpioAction` registration with
  `InitramfsAction` (`config_key="__root__"`, optional). `MkinitcpioAction` is removed; its
  logic now lives in `MkinitcpioBackend`.

## 4. Testing (TDD, 80% gate)

- `MkinitcpioBackend`: HOOKS derivation (encryption subs, btrfs hook, keyboard-before-
  autodetect, dedup — ported from the existing mkinitcpio tests); `actual_value()` parses
  HOOKS / `None` when absent; `apply()` rewrites the line + issues `mkinitcpio -P` with the
  right args/target.
- `DracutBackend`: `desired_value()` includes `crypt` when encrypted, `btrfs` when btrfs
  root, deterministic ordering; `actual_value()` reads the conf / `None`; `apply()` writes
  `dasik.conf` + `dracut --regenerate-all --force`.
- `make_backend`: returns the right class; raises on unknown name.
- `InitramfsAction`: selects backend by `initramfs` field; delegates the four hooks;
  `is_v3()` True; `plan()` one `MODIFY` when desired≠actual, `[]` when equal;
  `_import_fragment` → `{}`.

## Out of scope (future slices)

- Other generators (booster, …) — the interface is extensible.
- `dracut` driver/module knobs beyond crypt/btrfs derivation.
- bootloader generation entries (deferred multi-slice subsystem: per-generation kernel
  capture + boot-time apply hook + btrfs fast-path).
- `locale`/`network`/`pacman` (composite, need a multi-field base, not the scalar one).

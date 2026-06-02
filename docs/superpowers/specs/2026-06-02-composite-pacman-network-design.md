# Composite migration: pacman + network → CompositeV3Action

Date: 2026-06-02
Status: approved
Scope: final roadmap slice — migrate the two remaining dict-shaped legacy
actions (`PacmanAction`, `NetworkAction`) onto the v3 `CompositeV3Action`
contract so they participate in `plan` / `apply` / `sync` and are target-aware.

## Problem

`PacmanAction` and `NetworkAction` are still legacy: they implement only
`is_needed` / `execute` / `verify` and do **not** override `plan()`, so
`AbstractAction.is_v3()` returns `False`. The reconciler skips non-v3 actions
(`reconciler.py`: `if not cls.is_v3(): continue` in both `build_plan` and
`sync_to_config`). Consequences:

- Neither action appears in `plan` / `apply` / `sync` — they are blind spots in
  the declarative reconcile, only reachable via the legacy executor path.
- Both use hardcoded `/mnt/...` paths, so they break under `--target`.
- `PacmanAction.is_needed` is **enable-only**: a flag set `false` is never
  disabled and `[multilib]` is never re-commented — not truly declarative.

Every other dict-shaped domain (locales, timezone, initramfs) already rides
`ScalarV3Action` / `CompositeV3Action`. These two are the last holdouts.

## Goals

- Migrate both actions to `CompositeV3Action`, reusing the proven base
  (`LocaleAction` is the reference implementation).
- Make both target-aware via `_p(canonical)` (`target.path` with `/mnt`
  fallback), matching `LocaleAction`.
- pacman: **bidirectional** enforcement over the four known flags.
- network: keep `type` validation, exclude `type` from the comparison record.
- No model changes; no `network.type` disk detection; AUR untouched.

## Non-goals

- Changing `PacmanModel` / `NetworkModel` (both already exist and suffice).
- Detecting `network.type` from systemd / disk state.
- Managing pacman options beyond the four dasik already knows
  (`Parallel`, `Color`, `VerbosePkgLists`, `multilib`).

## Design

### PacmanAction → CompositeV3Action

- `_DOMAIN = "pacman"`; registration unchanged (`config_key='pacman'`).
- Target-aware: replace the `PACMAN_CONF = "/mnt/etc/pacman.conf"` constant with
  `_p("/etc/pacman.conf")`.
- State record (config-facing keys):
  `{"Parallel": bool, "Color": bool, "VerbosePkgLists": bool, "multilib": bool}`.
- `_desired_state()` — from parsed config; defaults `Parallel=True`,
  `Color=True`, `VerbosePkgLists=False`, `multilib=False` (unchanged defaults).
- `_actual_state()` — parse pacman.conf: is each option uncommented? is the
  `[multilib]` block active (header + `Include` both uncommented)? Return `None`
  when the file is missing → triggers a full MODIFY.
- `_set_value()` — **bidirectional**: uncomment when the flag is `True`, comment
  when `False`; toggle the `[multilib]` block + its `Include` line accordingly.
  Internal name mapping: config `Parallel` ↔ conf token `ParallelDownloads`.
- `_import_fragment()` →
  `{"pacman": {"options": {"Parallel": .., "Color": .., "VerbosePkgLists": ..}, "multilib": ..}}`
  from `_actual_state()` (or `_desired_state()` when actual is `None`).
- `plan()` (inherited from `CompositeV3Action`) emits one `MODIFY` listing the
  changed flag names; re-run converges in both directions.

### NetworkAction → CompositeV3Action

- `_DOMAIN = "network"`; registration unchanged (`config_key='__root__'`,
  reads root-level `hostname` plus the `network` section).
- Target-aware `_p()` for `/etc/hostname` and `/etc/hosts`.
- State record (config-facing): `{"hostname": str, "default_hosts": bool}`.
  `type` is **excluded** — it has no on-disk representation this action writes.
- `_desired_state()` — `{"hostname": self.hostname, "default_hosts": self.add_default_hosts}`.
- `_actual_state()` — read `/etc/hostname` (stripped); detect the default
  loopback block in `/etc/hosts` → bool. Return `None` when the hostname file is
  missing → full MODIFY.
- `_set_value()` — clear existing loopback lines, write hostname, append the
  default hosts block when `add_default_hosts`; **validate `type`** (raise
  `NetworkTypeNotFoundException` if not `NetworkManager` / `systemd-networkd`).
  Target-aware.
- `_import_fragment()` → **two root keys** (it spans root + section):
  `{"hostname": <actual|desired>, "network": {"type": self.type, "add_default_hosts": <actual|desired>}}`.
  `type` is passthrough from the declared config — no disk detection.
- `managed_keys()` → single domain `"network"` (the composite serializes
  hostname + default_hosts into one record).

#### Nothing-declared guard

`NetworkAction` is registered under `__root__`, so the reconciler always
constructs it with the whole config and always calls `plan()` (unlike a
section-keyed action, which is skipped when its slice is absent). For a minimal
config with no `hostname` (e.g. the package-only verb-integration fixtures):

- `plan()` must return `[]` (nothing to do),
- `apply()` must be a no-op,
- `type` must **not** be validated (would raise on `type == ""`).

Guard: when `not self.hostname`, `_desired_state()` signals no-op so `plan()` is
empty and `_set_value()` returns early without validating `type`. Real configs
always carry `hostname` (mandatory in `JsonModel`), so this only affects
minimal/test fixtures.

## Edge cases

- **pacman section removed after apply** — `build_plan` bootstraps
  `empty_config() == {}` when owned-managed entries exist, so the record reverts
  to coded defaults (scalars/composites have no DELETE). Documented behavior.
- **sync bootstrap** — `sync_to_config` does not skip absent slices, so an
  undeclared `pacman` section captures the real pacman.conf options into config
  (same pattern as packages reality capture).
- **network nothing-declared** — handled by the guard above.

## Testing (TDD)

Mirror `test_locale_action_v3.py`:

- `tests/lib/actions/test_pacman_action_v3.py` — `actual()` / `plan()` /
  `import_state` over a fake pacman.conf fixture; bidirectional `_set_value`
  round-trip (true→false and false→true converge); multilib toggle;
  missing-file → MODIFY.
- `tests/lib/actions/test_network_action_v3.py` — hostname drift; default_hosts
  present/absent; two-key `_import_fragment` shape; `type` passthrough;
  nothing-declared guard (empty hostname → empty plan, no raise); invalid `type`
  raises on apply.
- Port/replace any existing legacy `test_pacman_action.py` /
  `test_network_action.py` to the v3 contract.
- Verb integration (`tests/cli/test_verbs_integration.py`) — confirm the
  idempotent test still records no generation 2 (both actions converge) and the
  guard prevents a network raise. Add mock-table entries only if a new shell
  command appears (none expected: both are file-only; network `type` validation
  is in-Python).

## Verification

- Full suite green.
- Coverage ≥ 80% (gate unchanged).

# dasik — Declarative Convergence & Bidirectional Sync (Design)

- **Date:** 2026-05-27
- **Status:** Approved (brainstorm); pending implementation plan
- **Topic:** Turn dasik from a one-shot `/mnt` installer into a NixOS-like declarative
  manager where the config file is the single source of truth, with a persisted
  state manifest, generations, and two-way sync between system and config.

---

## 1. Goal & context

### Today
- dasik is install-from-live-ISO tooling. Every action targets the mounted
  install target at `/mnt` via `arch-chroot /mnt` (the path is effectively
  hardcoded across actions).
- The v2 architecture (`actions_handler_v2.setup_actions()` + `ActionExecutor`)
  registers ~20 actions implementing `is_needed()/execute()/verify()`, but the
  entry point (`dasik/__main__.py`) wires the **legacy** `actions_handler`, and
  even then instantiates and discards it — so `dasik config.json` performs no
  real work.
- Actions are **additive**: they install/enable what the config declares but never
  remove what the config no longer declares. There is no record of what dasik
  "owns", and no way to capture out-of-band changes (e.g. `pacman -S`) back into
  the config.

### Target (this design)
dasik becomes a **bidirectional declarative manager**, NixOS-like:

- **Config JSON = single source of truth** (≈ `configuration.nix`).
- **`apply`** converges the system to the config: installs/enables what is missing
  **and removes what dasik previously applied but the config no longer declares**.
- **`sync`** captures **drift** (things changed by hand, e.g. `pacman -Qqe`) back
  into the config file. Pointed at an existing, already-installed Arch system,
  `sync` bootstraps a config file from a running machine.
- A persisted **state manifest** records what dasik manages/owns (≈ the active
  generation's record). Removal is scoped to what dasik owns, so `apply` never
  nukes packages the user installed manually — those surface as drift for `sync`.
- Every successful `apply` records a **generation** (snapshot of config + manifest),
  the foundation for rollback.
- The engine is **root-parameterized** (`--target / | /mnt`): same actions run
  against the live host (day-2) or the installer target (install-time).

### Non-goals (deferred — future TODO, not this slice)
- Bootloader-level generation entries (selecting a generation at GRUB/sd-boot).
- Disk convergence (repartitioning to match config). Disks stay gated behind the
  explicit `format` flag and are **never** converged on drift.
- Version pinning / lockfile (reproducible installs across machines/time).
- Pure / content-addressed store (would mean replacing pacman; out of scope).

---

## 2. Core reconciliation model (the heart)

Per domain, dasik works with three sets:

| Set | Meaning | Source |
| --- | --- | --- |
| **D** (Desired) | what the config declares | config JSON |
| **M** (Managed) | what dasik applied / owns | state manifest |
| **A** (Actual) | what is really on the system | live query (`pacman -Qqe`, enabled units, users, file hashes) |

### `apply` semantics

```
INSTALL = D \ A        declared, absent          → add / enable / create
REMOVE  = M \ D        owned, no longer declared → remove / disable / delete   (DESTRUCTIVE)
DRIFT   = A \ D \ M    present, neither declared nor owned → LEFT UNTOUCHED, reported
after apply:  M ← D    (manifest now records the declared set as owned)
```

**Key property:** removal is scoped to `M \ D` (only things dasik itself applied).
Manually-installed items (`A \ D \ M`) are **not** removed; they are reported as
drift and are candidates for `sync`. This directly satisfies the requirement
"if I install something with pacman, give me a way to add it to the file / sync it"
without the danger of a fully-authoritative wipe.

### `sync` semantics (system → config)

```
config ← config ∪ DRIFT        capture hand-made additions into the file
config ← config \ (M \ A)      drop owned items the user removed by hand
M ← A                          manifest now matches reality
```

`sync` is conservative: items declared but not yet present (`D \ A`, not owned) are
**kept** in the config — sync never drops mere intent, only owned items (`M \ A`)
that actually vanished from the system.

**Bootstrap bonus:** on a system **not** originally built by dasik, the manifest is
empty (`M = ∅`), so the first `sync` captures all of `A` (e.g. every explicitly
installed package) into the config — turning a live install into a declarative file.

### First-apply safety
On the first ever `apply`, `M = ∅` ⇒ `REMOVE = ∅` (nothing owned yet) ⇒ only
installs happen; all pre-existing undeclared items are DRIFT and untouched.

### Per-domain definition of D / M / A

| Domain | D (config) | A (actual) | M (manifest) | Destructive op |
| --- | --- | --- | --- | --- |
| **packages** | `packages[]` (incl. `aur-` prefix) | `pacman -Qqe` (explicitly installed) | recorded package set | `pacman -Rns` |
| **systemd units** | `systemd.enable_*` | enabled units (`systemctl is-enabled`) | recorded unit set | `systemctl disable` |
| **files** (`udev_rules`, `modprobe_conf`, `profile_d`, `etc_environment`, …) | declared file contents | files on disk + their hashes | recorded `path → hash` map | delete file |
| **users** | `users[]` | non-system users (uid ≥ 1000) | recorded usernames | `userdel` (guarded) |

Disks are **excluded** from this model (no convergence; `format`-gated only).

---

## 3. Components

Each unit has one purpose, a defined interface, and explicit dependencies.

### 3.1 `Target`
- **Does:** encapsulates the root the operation runs against (`/` or `/mnt`) and how
  to run commands there.
- **Interface:** `Target(root: str)`; `Target.is_chroot -> bool` (True when `root != "/"`);
  used by `Command`. For `root == "/"` commands run directly (no `arch-chroot`);
  otherwise `arch-chroot <root> …`.
- **Depends on:** nothing. Injected into `ActionContext` and read by `Command`.
- **Replaces:** the hardcoded `/mnt` / `arch-chroot /mnt` scattered in actions.

### 3.2 `StateStore`
- **Does:** read/write the persisted manifest at `<target>/var/lib/dasik/state.json`.
- **Interface:** `load() -> Manifest`, `save(Manifest)`. `Manifest` holds per-domain
  managed sets, the applied config hash, the current generation number, timestamp.
- **Depends on:** `Target` (path resolution).

### 3.3 `GenerationStore`
- **Does:** record/list/restore generations under `<target>/var/lib/dasik/generations/<N>/`.
- **Interface:** `new(config, manifest) -> int`, `list() -> [GenInfo]`,
  `restore(n) -> (config, manifest)`. Maintains a `current` symlink → active generation.
- **Depends on:** `Target`, `StateStore`.

### 3.4 `Change` / `Plan`
- **Does:** represent and aggregate proposed changes; render the human-readable diff;
  flag which changes are destructive (need confirmation).
- **`Change`** = dataclass(`domain: str`, `op: Op`, `item: str`, `reason: str`,
  `destructive: bool`) where
  `Op ∈ {INSTALL, REMOVE, MODIFY, ENABLE, DISABLE, CREATE, DELETE}`.
- **`Plan`** = ordered list of `Change` + helpers: `is_empty()`, `destructive() -> [Change]`,
  `render() -> str`.
- **Depends on:** nothing.

### 3.5 Action contract v3 (`AbstractAction`)
- **Does:** per-domain knowledge of D/A, how to plan, apply, and import state.
- **Interface (root-aware):**

  | Member | Purpose |
  | --- | --- |
  | `name` (property) | human label (unchanged) |
  | `actual() -> set/state` | read system reality (A), via `Command` + `Target` |
  | `plan(managed) -> list[Change]` | compute changes from D (config), M (`managed`), A. `is_needed()` becomes `bool(plan(...))` |
  | `apply(plan) -> None` | execute changes, **including removals** (was `execute()`) |
  | `import_state() -> dict` | config fragment derived from A (for `sync`) |
  | `managed_keys() -> dict` | what this action contributes to the manifest (the new M) |
  | `verify() -> bool` | optional post-check (no changes) |

- **Backward-compat:** `is_needed()` / `execute()` remain as thin shims over
  `plan()` / `apply()` so any not-yet-migrated action keeps working. All domain
  actions in scope are migrated in this slice.
- **Depends on:** `Command`, `Target`, `ActionContext`, `Change`.

### 3.6 `Reconciler` (extends today's `ActionExecutor`)
- **Does:** orchestrate. Load config + manifest + reality → build the aggregate
  `Plan` across actions (registry order) → drive `plan` / `apply` / `sync`.
- **Interface:** `build_plan() -> Plan`, `apply(plan, *, assume_yes=False)`,
  `sync() -> updated_config`.
- **Depends on:** `ActionRegistry`, actions, `StateStore`, `GenerationStore`, `Plan`.

### 3.7 `ConfigWriter`
- **Does:** write a config fragment back to the config file for `sync`.
- **Interface:** `merge(existing_config, fragments) -> new_config`; serialize to JSON.
- **Behavior:** append DRIFT additions to the relevant domain arrays, drop vanished
  owned entries, preserve element order where possible. `metadata` and unknown keys
  are passed through untouched.
- **Limitation:** JSON has no comments; logical grouping/comments in hand-written
  configs are lost on rewrite (documented; acceptable for slice 1).
- **Depends on:** the pydantic models (for shape) / plain dict round-trip.

### 3.8 CLI (`__main__`)
- **Does:** dispatch verbs; wire the real v2 path (closes the entry-point gap).
- **Depends on:** `Reconciler`, `JsonParser`, `setup_actions()`.

---

## 4. CLI verbs & workflow

```
dasik plan  <config> [--target /]                 # show diff, make no changes
dasik apply <config> [--target / | /mnt] [--yes]  # converge; confirm destructive; record generation
dasik sync  <config> [--target /]                 # reality → write back into the config file
dasik generations                                 # list recorded generations
dasik rollback [N]                                # re-apply generation N (basic)
```

- `dasik <config>` with **no verb** → deprecated alias for `apply` (back-compat),
  prints a deprecation notice.
- `plan` is the real implementation of the long-broken `--dry-run`.

### `apply` flow
1. Parse config (`JsonParser`), resolve `Target`.
2. Load manifest (`StateStore`); query reality per action.
3. `Reconciler.build_plan()` → aggregate `Plan`.
4. If `Plan.is_empty()` → "system already matches config", exit 0.
5. Print plan. If it contains destructive changes → require interactive
   confirmation unless `--yes`.
6. Execute actions in registry order (disk/base first, boot last); each `apply(plan)`.
7. Update manifest (`M ← D`), record a new generation.
8. `verify()`.

### `sync` flow
1. Parse config, resolve `Target`, load manifest.
2. For each action, `import_state()` → fragment; compute DRIFT and vanished-owned.
3. `ConfigWriter.merge()` → new config; write file.
4. Update manifest (`M ← A`). (No system mutation — only the config file changes.)

### `rollback` (basic, slice 1)
- `restore(N)` from `GenerationStore`, then run `apply` against that generation's
  config. Bootloader boot-entry selection is **future**.

---

## 5. Safety

The user opted into **all domains**, so destructive operations are real. Guards:

- **Plan-gated:** destructive changes (`REMOVE/DISABLE/DELETE`/`userdel`) never run
  without a printed plan + interactive confirmation, unless `--yes`.
- **Manifest-scoped removal:** only `M \ D` is eligible for removal — the primary
  safety net against wiping user-installed items.
- **Protected sets** (never removed, configurable):
  - packages: `base`, the running kernel package(s), and an essential allowlist.
  - users: the current/login user, all uid < 1000 system users.
  - units: an essential-units allowlist.
- **Live-host warning:** `--target /` with any destructive change prints a prominent
  warning (you are mutating the running system).
- **Disks:** never converged; partition/format stays behind the explicit `format` flag.

---

## 6. Storage layout & schemas

```
<target>/var/lib/dasik/
├── state.json              # current manifest
└── generations/
    ├── 1/{config.json, state.json}
    ├── 2/{config.json, state.json}
    └── current -> 2        # symlink to the active generation
```

`state.json` (illustrative):

```json
{
  "version": 1,
  "generation": 2,
  "applied_at": "2026-05-27T21:00:00Z",
  "config_hash": "sha256:…",
  "managed": {
    "packages": ["git", "htop", "vlc", "aur-yay"],
    "units": ["NetworkManager.service", "cups.socket"],
    "files": { "/etc/udev/rules.d/99-dasik.rules": "sha256:…" },
    "users": ["alice", "bob"]
  }
}
```

For `--target /mnt` (install-time), the manifest is written under `/mnt/var/lib/dasik/`
and becomes `/var/lib/dasik/` on first boot — so day-2 `apply` on `/` continues from
where the install left off.

---

## 7. Scope

### In (slice 1)
- `Target` parameterization; remove hardcoded `/mnt`.
- Action contract v3 (`actual`/`plan`/`apply`/`import_state`/`managed_keys`), with
  compat shims; migrate packages, systemd, files, users (+ keep existing actions working).
- `StateStore` + manifest; `GenerationStore` + generation recording; basic `rollback`.
- `Reconciler`, `Plan`/`Change`, `ConfigWriter`.
- CLI verbs `plan` / `apply` / `sync` / `generations` / `rollback`; wire `__main__`.
- Safety: protected sets, confirmation gating, live-host warnings.

### Out (future TODO)
- Bootloader generation entries; disk convergence; pinning/lockfile; pure store.

---

## 8. Testing strategy (TDD; per CLAUDE.md)

- **Reconciler set-math** (D/M/A → INSTALL/REMOVE/DRIFT): pure logic, exhaustive unit
  tests incl. first-apply (`M=∅`) and bootstrap (`D=∅`) edges. Highest value.
- **Action `plan()` / `import_state()`** per domain: monkeypatch `Command.execute`
  and the manifest; assert the produced `Change` set / config fragment.
- **`StateStore` + `ConfigWriter`** round-trips: write→read, merge idempotence,
  passthrough of `metadata`/unknown keys.
- **`apply()` destructive bodies:** mock `Command.execute`; assert it is called with
  the right args (`pacman -Rns …`, `userdel …`). **Never** run real pacman/userdel.
- **Coverage gate 80%**; exclude pure shell-out bodies in config with written
  justification, covered indirectly via `plan` + mocked `Command`.

---

## 9. Decisions made (resolved during brainstorm)

- **Removal semantics:** owned-only (`M \ D`); drift is preserved and surfaced for
  `sync`, not auto-removed. (User confirmed they want hand-made changes captured,
  not wiped.)
- **Target:** both `/` (day-2) and `/mnt` (install) via `--target`; engine parameterized.
- **Domains:** all of packages/services/files/users now; disks excluded; users guarded.
- **Architecture:** state-file / manifest from the start (ownership tracking now,
  generations foundation now).
- **Spec language:** English (matches CLAUDE.md and code identifiers).

## 10. Open questions (for the planning step)

- Config layout stays a single JSON for slice 1; splitting into per-domain files
  (`*.nix`-style) is a later option once `sync` writeback is proven.
- Exact protected-package allowlist contents (kernel detection, `base` group
  expansion) — settle during implementation against the Arch wiki reference.

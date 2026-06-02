# Minimal functional dasik: NixOS-style expansion + full-install in v3

Date: 2026-06-02
Status: approved
Scope: decomposition spec for the remaining roadmap — bring every config section
and the destructive bootstrap (disk + base install) under the v3 verb pipeline
(`plan`/`apply`/`sync`), reaching a minimal functional declarative installer.

This is a multi-slice decomposition. Each slice below gets its own
implementation plan (`writing-plans`) and is delivered as described in
"Delivery". This document is the shared design for all slices.

## Problem

Today the v3 verb pipeline (Reconciler) only reconciles the 10 actions that are
already v3 (packages, pacman, network, systemd, users, locale, timezone,
initramfs, kernel_cmdline, files). Ten actions are still legacy and are
**skipped** by the Reconciler, so they never run via `apply`/`plan`/`sync`:

- Feature toggles: `bluetooth`, `cups`, `kvm`, `hardware_acceleration`,
  `trim`, `wireguard`, `firewall`, `microsoft_fonts`.
- Destructive bootstrap: `disks` (partitioning), base install (pacstrap).

`dasik apply` therefore converges most config but does not install from scratch
and ignores the feature toggles. The goal ("behave like Nix/NixOS") needs both.

## Goals

- Every config section participates in `plan`/`apply`/`sync`, idempotently.
- `dasik apply config.json` can drive a full install (disks → base → config →
  boot), with destructive steps gated behind explicit flags.
- Feature toggles follow the **NixOS module model**: a toggle is sugar that
  *expands* into the shared `packages` / `systemd` / `files` domains, rather than
  owning an overlapping domain.
- Retire the legacy no-verb path and the per-section `_handle_*` handler.

## Non-goals

- AUR changes (existing behavior kept).
- New config options beyond what the toggles already accept.
- A GUI / TUI.

## Architecture

Two mechanisms, chosen per feature by whether it maps to packages/units/files.

### a) Expansion layer (`dasik/lib/expand/`)

Pure functions, one per toggle:

```
expand_bluetooth(cfg)  -> {"packages": [...], "units": [...], "sockets": [...], "files": [...]}
expand_cups(cfg)       -> {...}
expand_kvm(cfg)        -> {...}
expand_hwaccel(cfg, drivers) -> {...}
expand_trim(cfg)       -> {...}
expand_wireguard(cfg)  -> {...}
expand_firewall(cfg)   -> {...}
```

A single `expand_config(config: dict) -> dict` runs every applicable toggle and
**merges** the results into the base domains:

- `packages`     += union of toggle packages
- `systemd.enable_units`   += union of toggle units
- `systemd.enable_sockets` += union of toggle sockets
- `files`        += union of toggle files (e.g. `/etc/wireguard/wg0.conf`,
  firewalld zone XML under `/etc/firewalld/`)

The Reconciler consumes this **derived** config. The original toggle sections
stay in the user's config file untouched — they are the source; expansion is a
view recomputed every run (exactly like NixOS module evaluation).

After expansion, the existing v3 `PackagesAction` / `SystemdAction` /
`DropFilesAction` do all the reconcile work — plan, apply, sync, REMOVE
(`enable:false` removes the package/unit via set-math), and idempotency — for
free. No new action classes for these seven toggles; the legacy action classes
and their tests are deleted and replaced by expansion-function tests.

#### sync without duplication (the subtraction rule)

`packages` reality is `pacman -Qqe`, which would include `bluez`. To stop sync
from folding `bluez` into the `packages` config (it belongs to the `bluetooth`
toggle), the base-domain reality capture **subtracts** the set expanded by the
currently-active toggles:

> A resource contributed by an active toggle is attributed to that toggle, not
> to the base domain. On sync, exclude `expand_config(config)`'s contributed
> packages/units/sockets/files from the captured base-domain reality.

So `bluez` stays implied by `"bluetooth": {"enable": true}` and the config file
stays clean. The expansion set is already available (same functions), so the
capture just removes it.

### b) Dedicated v3 domains

For features that are not packages/units/files:

- `microsoft_fonts` — `actual()` = are the Windows fonts present under
  `/usr/share/fonts/...`? `plan()` = install when `source_iso` is declared and
  fonts are missing; `apply()` = mount the ISO, copy the fonts, unmount. Gated
  on `source_iso`. Idempotent via the fonts-present check.
- `disks` — `actual()` = read the current partition table (sgdisk/lsblk);
  `plan()` = create partitions/filesystems when missing; `apply()` =
  **destructive**, gated by `wipe_disk` / per-partition `format`. `is_needed`
  must be strict so a converged disk is a no-op.
- base install — `actual()` = is the base system present under `/mnt`
  (e.g. `/mnt/usr/bin/pacman` or a populated `/mnt/etc`)? `plan()` = install
  when absent; `apply()` = `pacstrap` + `genfstab`.

### apply ordering (full install)

`disks → base → [config: expand + packages/systemd/files/users/locale/...] →
boot (initramfs, kernel_cmdline, bootloader)`. Destructive steps (disks, base)
run first and only when their gates allow; they are no-ops on an
already-installed target.

## Slices

Ordered: safe/testable first, destructive last. Each leaves the repo green
(suite + coverage ≥ 80%) and functional.

| # | Slice | Contents | Risk |
|---|-------|----------|------|
| 1 | Expansion infra + simple toggles | `dasik/lib/expand/`, `expand_config`, Reconciler hook, sync-subtraction. Toggles: bluetooth, cups, trim, kvm. Delete their legacy classes/tests. | Low |
| 2 | File-emitting toggles | wireguard (pkg+unit+`/etc/wireguard/wg0.conf`), firewall (pkg+unit+firewalld zone XML), hwaccel (pkgs from `drivers`). Reuses slice-1 infra + files domain. Delete legacy classes/tests. | Low |
| 3 | microsoft_fonts v3 domain | Dedicated v3 domain (mount ISO + copy), idempotent fonts-present check, gated on `source_iso`. | Medium |
| 4 | disks v3 domain | actual=read partitions, plan=create when missing, apply=destructive gated by `wipe_disk`/`format`. Fully mocked tests. | High |
| 5 | base install v3 domain | pacstrap base + genfstab; actual="base present in /mnt?", plan=install when absent. | High |
| 6 | Wire full apply + retire legacy | apply order disks→base→config→boot; remove the no-verb `ActionsHandler` path and `_handle_*` methods. | Medium |

Dependencies: 1 → 2. Slices 3, 4, 5 are independent of each other once 1 lands.
6 is last (needs 4 + 5 in v3).

## Testing

- Expansion functions: pure, deterministic — assert the emitted
  packages/units/sockets/files for each toggle (enabled and disabled).
- sync-subtraction: a config with an active toggle + that package present in
  reality → the package is NOT written into `packages` on sync.
- Dedicated domains: `actual`/`plan` decision logic with mocked `Command.execute`
  and a fake target tree; never run destructive `execute()` for real.
- Per CLAUDE.md: cover the *decision* (`is_needed`/`plan`); `execute()`/`apply()`
  bodies that only shell out to destructive tooling are asserted via mocked
  `Command.execute`, not run.
- Coverage gate stays 80%. Verb-integration suite stays green.

## Delivery

`git push` is the user's; PRs are opened with `gh` after the user pushes.

- **PR A** = slices 1 + 2 + 3 (config toggles + msfonts — safe, testable).
- **PR B** = slice 4 (disks) — separate for careful review (destructive).
- **PR C** = slice 5 (base install) — separate (destructive).
- **PR D** = slice 6 (wiring + legacy retirement).

Each slice: full TDD, suite green, coverage gate, then PR.

## Open risks

- Destructive slices (4, 5) cannot be tested on real hardware; correctness rests
  on strict `is_needed`/`plan` gating + mocked-command assertions. The dev
  machine's disks must never be targeted.
- The expansion subtraction must be applied consistently in sync for every base
  domain (packages, systemd units/sockets, files) or reality capture will
  duplicate toggle-owned resources into config.

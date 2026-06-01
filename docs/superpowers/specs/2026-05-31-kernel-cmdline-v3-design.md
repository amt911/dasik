# Design: migrate `kernel_cmdline` to the v3 contract (set-math + portable LUKS UUID)

Date: 2026-05-31
Status: approved (design), pending implementation plan

## Context

`KernelCmdlineAction` merges explicit `kernel_cmdline` params with params auto-derived from
the disk config (encryption → `rd.luks.name=…`, btrfs → `rootflags=…`) and appends the
missing ones to GRUB / systemd-boot. It is **legacy** (`is_needed`/`execute`), so the
reconciler (`dasik plan/apply/sync`) skips it entirely today.

Kernel params are a **set** of tokens → they fit the packages-style `compute_changes`
set-math. The one blocker was the auto-derived LUKS param, which the code emitted with a
literal `<ROOT_UUID>` placeholder (never resolved) — in set-math that never equals reality,
so it would churn forever and break idempotency.

## Decisions (from brainstorming)

- v3 domain `kernel_cmdline`: desired = **explicit params ∪ auto-derived params with the LUKS
  UUID resolved to the real value**. Concrete tokens ⇒ idempotent ⇒ fit set-math.
- **Portability** is the headline goal — the same config must work on many machines with
  different UUIDs *and* different device paths:
  - The config never stores a UUID or a `/dev/...` path for the cmdline; it declares intent
    (`encrypt`, `luks_name`, btrfs subvol).
  - dasik resolves the real LUKS partition UUID at plan time via the **open LUKS mapping**,
    not via a device path or partition index:
    `cryptsetup status <luks_name>` → backing `device:` → `blkid -s UUID -o value <device>`.
  - This is device-portable (works regardless of sda/nvme/vda) and robust to extra
    partitions on the disk (it anchors on `luks_name`, not position). It also needs no change
    to the disk action (whose GPT names are all "primary").
- `import_state` (sync) returns **only the explicit params** — never the resolved UUID — so
  `sync` keeps the config portable.
- Auto-derived params not yet resolvable (LUKS not open at plan time) are **omitted** from
  desired (no churn); they resolve once the mapping is open.
- Legacy `is_needed`/`execute`/`verify` are kept for the old `ActionExecutor` path.

## 1. LUKS UUID resolution (device-portable)

New helpers on the action (run on the **host** — device-mapper/blkid are host-level — so
`Command.execute(..., target=None`-style direct, no chroot):

```python
def _luks_backing_device(self, luks_name: str) -> Optional[str]:
    # `cryptsetup status <luks_name>` → parse the "  device:  /dev/XXX" line
def _resolve_luks_uuid(self, luks_name: str) -> Optional[str]:
    dev = self._luks_backing_device(luks_name)
    # `blkid -s UUID -o value <dev>` → UUID, or None
```

`_derive_from_disks` builds, per encrypted root partition:
- `rd.luks.name={uuid}=cryptroot` (only if `uuid` resolved; else omit the token),
- `root=/dev/mapper/{luks_name} rw`,
and per btrfs root: `rootflags={opts},subvol={sv}` (already concrete, no UUID).

## 2. `KernelCmdlineAction` v3 methods

Domain `"kernel_cmdline"`. Registered `config_key="__root__"` (already).

- `_explicit()` → `config.get("kernel_cmdline", [])`.
- `_derived()` → the resolved auto-derived tokens (above).
- `_desired_tokens()` → `_explicit()` + `_derived()` (order preserved, de-duplicated).
- `actual() -> set[str]`: current cmdline tokens — GRUB `GRUB_CMDLINE_LINUX="…"` or the first
  systemd-boot entry's `options …` line (target-aware via `target.path`). Empty set when
  unreadable.
- `plan(managed)`: `compute_changes("kernel_cmdline", desired=self._desired_tokens(),
  managed=managed, actual=self.actual())` → INSTALL = D\A, REMOVE = M\D. REMOVE is scoped to
  what dasik owns, so distro/auto params it doesn't manage stay as drift, untouched.
- `apply(changes)`: compute the new token set = `(actual ∪ installs) − removes`; rewrite the
  GRUB `GRUB_CMDLINE_LINUX` line / each systemd-boot entry `options` line; regenerate
  (`grub-mkconfig -o /boot/grub/grub.cfg` for GRUB; sd-boot needs no regen beyond the file
  write). Target-aware. No-op without target.
- `managed_keys() -> {"kernel_cmdline": self._desired_tokens()}` (owns explicit + derived).
- `import_state(managed) -> {"kernel_cmdline": [explicit survivors]}` — explicit only; drop
  owned-and-vanished explicit tokens (`M ∩ explicit \ actual`); never emit resolved UUID
  tokens (portability). No drift capture (a shared cmdline has no ownership marker).

## 3. Testing (TDD, 80% gate)

- `_luks_backing_device`: parses the `device:` line from a mocked `cryptsetup status`;
  `None` when the command fails.
- `_resolve_luks_uuid`: `blkid` mock → UUID; `None` when backing device or blkid fails.
- `_derive_from_disks`: includes `rd.luks.name=<real-uuid>=cryptroot` when resolved; omits it
  when unresolved; btrfs `rootflags` present for btrfs root.
- `actual()`: parses GRUB and sd-boot cmdlines; empty when unreadable/no-target.
- `plan()`: INSTALL missing explicit, REMOVE owned-not-declared, empty when converged with a
  resolved UUID (idempotency proof).
- `apply()`: writes the merged token line + `grub-mkconfig` (GRUB) / entry rewrite (sd-boot);
  removal strips tokens; no-op without target.
- `managed_keys()`; `import_state()` returns explicit only (no UUID leak), drops vanished.
- `is_v3()` True; legacy `is_needed`/`execute` tests still pass.

## Out of scope (future slices)

- Device-agnostic **disk selection** for partitioning (`DiskLayout.device` still names a path)
  — the cmdline UUID is now device-portable, but the disk layout is not yet.
- Auto-derived params for non-LUKS scenarios beyond btrfs rootflags.
- Installing the bootloader (assumed present, as today).
- `locale`/`network`/`pacman` (composite domains) and other scalar migrations.

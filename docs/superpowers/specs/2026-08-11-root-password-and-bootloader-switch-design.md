# Root password + bootloader switching — design

Date: 2026-08-11
Branch: `feat/issue-173-plymouth-luks-keyfile`

Two independent changes, both on the v3 action path.

## 1. Root password in the config

### Current state

A root password is *already* expressible as an entry in `users`:

```json
"users": [{ "username": "root", "hashed_password": "$y$j9T$…" }]
```

`UsersAction` special-cases it: `actual()` is scoped to `uid >= 1000` so root never
appears there, `_declared_non_root()` keeps root out of the CREATE/DELETE set-math,
`plan()` emits a single `MODIFY root` when the declared hash differs from
`/etc/shadow`, and `apply()` runs only `usermod -p <hash> root` (never `useradd`,
never `-s`/`-G`).

Two gaps make it unusable in practice:

1. **It is undocumented.** `docs/config-reference.md` never mentions that `root` is
   a legal `username`.
2. **`sync` cannot capture it.** `import_state()` passes a declared root entry
   through verbatim as intent and never looks at `/etc/shadow`, so a captured
   config describes a root password that may not be the machine's. An undeclared
   root password is lost entirely. That violates the repo's
   "…and capturable by `sync`" rule.

Additionally, `shell` and `groups` on a root entry are accepted by the model and
then **silently ignored** by `apply()` — the exact ambiguity this codebase treats
as a bug.

### Design

**Model.** A `JsonModel` validator rejects a `users` entry with
`username == "root"` that sets `shell` or `groups` to anything other than the
default, with a message stating root's shell and groups are not managed by dasik.
Failing loudly beats ignoring silently.

**Action — `UsersAction.import_state()`.** Root is captured from reality:

- The root field of `/etc/shadow` starts with `$` → emit
  `{"username": "root", "hashed_password": <the real hash>}`. This refreshes a
  declared root entry and captures an undeclared one.
- The field is locked or empty (`!`, `*`, `!$6$…`, `""`) → emit nothing, and
  **drop** a declared root entry from the captured config. `sync` reports
  reality; a declared-but-not-real password is a divergence, not a fact.
- The captured root entry carries `username` + `hashed_password` only — no
  `shell`, no `groups` — mirroring exactly what `apply()` manages.

`plan()` and `apply()` need no change.

### Tests

- Model: a root entry with `shell` or `groups` is rejected; a bare root entry validates.
- `import_state`: real hash captured (declared and undeclared); locked/empty root
  captured as nothing and clears a declared entry; no `shell`/`groups` keys emitted.
- Detectability matrix (`tests/lib/test_feature_detectability.py`): hash differs ⇒
  `MODIFY root` planned; hash matches ⇒ plan silent.
- Sync matrix (`tests/lib/test_feature_sync_capture.py`): machine has it ⇒ captured;
  machine lacks it ⇒ nothing invented; the captured config validates and re-plans
  to nothing.

## 2. Switching bootloader cleans up the old one

### Current state

`BootloaderAction.actual()` probes only the marker of the **configured** loader:

```python
if self._installed():          # marker of self.bootloader ONLY
    found.add(self.bootloader)
```

So a machine holding a stale GRUB while the config declares `sd-boot` looks
converged for the stale loader — it is simply invisible. No `REMOVE` is ever
planned, and the switch leaves behind, depending on direction:

- `/boot/grub/`, `/boot/EFI/GRUB/`, the `GRUB` NVRAM boot entry; or
- `/boot/EFI/systemd/`, `/boot/loader/` (`loader.conf`, `entries/*.conf`,
  `random-seed`), the `Linux Boot Manager` NVRAM entry.

`managed_keys()` also returns `sorted(self.actual())` — ownership tracking
*reality* rather than intent, unlike every other domain.

### Design

**Canonicalization.** `systemd-boot` is an accepted alias of `sd-boot`. Domain
items use the canonical `sd-boot` only, so a manifest written under the alias
does not read as a switch on the next plan.

**`actual()`** probes **both** markers (`/boot/EFI/systemd/systemd-bootx64.efi`,
`/boot/grub/grub.cfg`) and returns every loader found, canonicalized, plus the
existing `fallback-entry` item.

**`plan()`** adds, on top of today's INSTALL + rescue-entry logic: for every
installed loader that is not the declared one, a
`Change(bootloader, Op.REMOVE, <loader>, reason="switched to <desired>")`, and a
`REMOVE fallback-entry` when leaving sd-boot with the entry present. `Op.REMOVE`
is destructive by definition, so the dry run marks it as such.

The stale loader is removed **whether or not the manifest owns it** — deliberately
breaking the usual leave-unowned-alone rule. Two loaders on one ESP is not a state
anyone wants, and `plan` always announces the removal before `apply` performs it.
The case that motivates it: after a `sync` from a live machine the manifest is
empty, so an ownership-gated removal would never fire.

**`managed_keys()`** returns the **desired** items, not `actual()`.

**`apply()`** performs REMOVEs **before** the INSTALL, via `_uninstall(loader)`:

- sd-boot: `bootctl remove`, then delete `/boot/loader/entries/`,
  `/boot/loader/loader.conf`, `/boot/loader/random-seed` (`bootctl remove` clears
  the EFI binaries and the NVRAM entry, not the loader entries).
- grub: remove `/boot/grub/` and `/boot/EFI/GRUB/`, then read `efibootmgr`, find
  the entry labelled `GRUB`, and `efibootmgr -b <num> -B` it.

Every path is a fixed constant joined to the target root — never derived from
config — so nothing user-controlled reaches a delete.

**NVRAM operations are best-effort** (`check=False`, warning on failure): a chroot
without `efivars` (container, VM build) must not abort an otherwise-good install.
File removal is *not* best-effort.

**`verify()`** requires the desired loader present **and** every other loader absent.

**Ordering risk (accepted).** Removing sd-boot before a `grub-install` that then
fails leaves the machine with no loader. The install runs `check=True`, so `apply`
aborts loudly, and `plan` announced the removal first. The alternative —
install-then-remove — leaves two loaders competing for the ESP mid-apply, which is
worse.

**No code needed for `systemd-boot-update.service`.** It is derived by
`expand_sdboot_update`; switching to grub stops deriving it, so `SystemdAction`
plans its DISABLE through normal set-math. Pinned by a test, not by new code.

### Tests

- `actual()` reports both loaders when both markers exist.
- grub → sd-boot: `INSTALL sd-boot` + `REMOVE grub`. sd-boot → grub:
  `INSTALL grub` + `REMOVE sd-boot` (+ `REMOVE fallback-entry`).
- Declared loader already installed and no stale one ⇒ no REMOVE.
- `systemd-boot` alias against an sd-boot marker ⇒ no spurious REMOVE.
- `managed_keys()` is the desired set.
- `apply()` ordering: uninstall runs before install.
- grub uninstall: directories gone, `efibootmgr -b <n> -B` called with the number
  parsed from `efibootmgr` output.
- sd-boot uninstall: `bootctl remove` called, `/boot/loader` contents gone.
- A failing NVRAM command does not raise.
- `verify()` false while a stale loader remains.
- Switching to grub plans `DISABLE systemd-boot-update.service`.
- Detectability + sync matrices extended; `sync` → `plan` silent.

## Out of scope

- Removing the `grub` package on a switch away from GRUB. Packages are the
  `packages` domain's business; the bootloader action reaching into it would
  fight `PackagesAction` on the next plan. Users drop `grub` from `packages`
  themselves.
- Managing root's shell or supplementary groups.

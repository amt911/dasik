# Issue #173, block B — plymouth and the pendrive LUKS keyfile

Date: 2026-08-11
Issue: [#173](https://github.com/amt911/dasik/issues/173) — "Bloque B"
Follows: block A (PR #174, merged).

## What this block delivers

The two remaining *concrete* leftovers from the old imperative installer
(`~/repos/archlinux-script-installer/.scripts/after-install-2.sh`):

| Old function | What it did | Status in dasik today |
| --- | --- | --- |
| `install_plymouth` | `yay -S plymouth`, add the `plymouth` hook to mkinitcpio, `splash` on the kernel cmdline | **absent** |
| `enable_crypt_keyfile` | create a random keyfile on a pendrive, `luksAddKey`, add the pendrive's fs module to the initramfs, `rd.luks.key=<root-uuid>=<file>:UUID=<pen-uuid>` and `rd.luks.options=keyfile-timeout=10s` | **half present, not bootable** |

Both must obey the two project invariants: **detectable by `plan`** (missing ⇒
planned, present ⇒ silent, owned-but-undeclared ⇒ REMOVE) and **capturable by
`sync`** (`sync` → `plan` is a no-op).

## Part 1 — the `plymouth` block

### Config surface

```json
"plymouth": { "theme": "bgrt" }
```

`PlymouthModel` (`dasik/lib/models/plymouth_model.py`):

* `theme: Optional[str] = None` — when set, dasik owns
  `/etc/plymouth/plymouthd.conf` (`[Daemon]\nTheme=<theme>`). When unset,
  plymouth's own default (Arch ships `bgrt`) is left alone.

`JsonModel.plymouth: Optional[PlymouthModel] = None`. No other keys: show-delay,
extra themes and `quiet` are already expressible through `files` and
`kernel_cmdline`, so adding fields for them would be duplicate spelling.

### How it converges (four owners, no new convergence path)

1. **`expand_plymouth`** (`dasik/lib/expand/toggles.py`) → `packages:
   ["plymouth"]`, plus a `files` entry for `/etc/plymouth/plymouthd.conf` when
   `theme` is set. `plymouth` is in the `extra` repo today; the old script's
   `yay` build is obsolete.
2. **`KernelCmdlineAction`** derives `splash`, exactly like it derives
   `sysrq_always_enabled=1` from the `sysrq` flag.
3. **Initramfs backends** put plymouth *into* the image — without this the
   splash never appears and the block is a lie:
   * *mkinitcpio*: insert the `plymouth` hook after `systemd`/`udev` and
     **before** `sd-encrypt`/`encrypt` (Arch wiki, Plymouth#mkinitcpio: "If you
     are using the systemd hook, it must be before plymouth… place plymouth
     before the encrypt or sd-encrypt hook").
   * *dracut*: add `plymouth` to `force_add_dracutmodules`. dracut
     auto-detects plymouth, but dasik runs it under `arch-chroot`, where
     hostonly detection is exactly what already silently dropped
     `systemd-cryptsetup` and `resume` (see `dracut.py::_force_modules`).
4. **`PlymouthAction`** — CAPTURE-ONLY, the `CpuAction`/`ReflectorAction`
   pattern: `plan()` is empty (it exists so `Reconciler.sync` visits it) and
   `import_state()` reconstructs the block.

The theme change alone must rebuild the initramfs (wiki: "Every time a theme is
changed, the initramfs must be rebuilt"). That falls out for free: the
plymouthd.conf content is part of `files`, and `InitramfsAction` runs in phase 5
after `DropFilesAction`, but the *image* freshness check
(`DracutBackend._images_current`) compares against `dasik.conf` only. So
`plymouthd.conf` is added as an input to that mtime comparison.

### Detectability (`plan`)

* Declared, plymouth not installed ⇒ `+ [packages] plymouth`,
  `+ [kernel_cmdline] splash`, `~ [initramfs]`, and `+ [files]
  /etc/plymouth/plymouthd.conf` when a theme is declared.
* Declared and converged ⇒ empty plan.
* Not declared but the manifest owns it ⇒ `- [packages] plymouth` and
  `- [kernel_cmdline] splash` (set-math, no new code).

### Capture (`sync`)

`PlymouthAction.import_state()` emits `{"plymouth": {...}}` when plymouth is
installed on the target — probed by the existence of `/usr/bin/plymouthd` under
the target root (works offline, needs no `pacman -Q` round trip). `theme` comes
from `/etc/plymouth/plymouthd.conf`; when the file has no `Theme=` the key is
omitted rather than invented.

`splash` is subtracted from the captured `kernel_cmdline` **only when plymouth
is installed** — the block then owns it. On a machine that carries `splash`
without plymouth (a bare `vt` splash, someone else's parameter), the token stays
a plain `kernel_cmdline` entry, because `sync` reports reality. This is a
deliberate refinement of the `_BLOCK_OWNED_PARAMS` rule, which subtracts
`amd_pstate`/`sysrq_always_enabled` unconditionally: those parameters have no
meaning outside their block, `splash` does.

Machine without plymouth ⇒ no `plymouth` key is invented.

## Part 2 — the pendrive LUKS keyfile

### What exists and why it does not boot

`Partition.unlock_keyfile` / `unlock_keydev` already exist,
`DiskPartitionAction._add_unlock_keyfile` runs `cryptsetup luksAddKey`, and
`KernelCmdlineAction` emits `rd.luks.key=<uuid>=<file>[:<keydev>]`. Three defects
make the result unbootable or unusable:

1. **`unlock_keydev` is emitted verbatim.** The kernel expects
   `rd.luks.key=<luks-uuid>=/path:UUID=<fs-uuid>` (Arch wiki,
   dm-crypt/System_configuration#rd.luks.key). A config holding the documented
   value — "Filesystem UUID of the device" — produces `…:1234-ABCD`, which
   systemd-cryptsetup cannot resolve.
2. **No `keyfile-timeout`.** Same wiki page: "rd.luks.key with a keyfile on
   another device by default does not fall back to asking for a password if the
   device is not available." Without the pendrive the machine hangs forever
   instead of prompting. The old script always added `keyfile-timeout=10s`.
3. **The initramfs cannot read the pendrive.** Same page: "If the type of file
   system is different than your root file system, you must include the kernel
   module for it in the initramfs." Nothing in dasik does that.

Plus: the keyfile is assumed to exist already (the old script *created* it), and
enrollment lives inside `_setup_encryption`, which only runs on a fresh format —
so an already-installed machine can never gain a pendrive, and nothing captures
the setup back on `sync`.

### Config surface

```json
{ "encrypt": true, "luks_name": "cryptroot", "luks_password": "…",
  "unlock_keyfile": "/keyfile-tuxedo", "unlock_keydev": "1234-ABCD",
  "unlock_keydev_fs": "vfat" }
```

One new field, `Partition.unlock_keydev_fs: Optional[str]` (`vfat`, `ext4`,
`btrfs`, `xfs`, `exfat`), because the initramfs needs the module by name and the
pendrive is not necessarily plugged in at plan time — the config, not a probe,
is the source of truth. `unlock_keydev` accepts a bare UUID (normalized to
`UUID=<value>`) or an explicit `UUID=`/`PARTUUID=`/`LABEL=` spec, passed through.

Semantics, made explicit because today they are ambiguous:

* **With `unlock_keydev`** — `unlock_keyfile` is a path **relative to the root of
  the key device** (`/keyfile-foo` means that file at the top of the pendrive).
* **Without `unlock_keydev`** — it is an absolute path **inside the target
  root**, and the file is embedded into the initramfs image.

### How it converges

A new **`LuksKeyfileAction`** (domain `luks_keyfile`), registered in phase 1
right after `DiskPartitionAction`, owns the key material:

* `plan()` — one item per encrypted partition that declares `unlock_keyfile`,
  keyed `<luks_name>:<path>`. The item is needed when the keyfile does not
  already unlock the volume, checked with
  `cryptsetup open --test-passphrase --key-file <local> <device>` (read-only, no
  mapping created). A missing key device is reported in the plan and fails loudly
  at apply — the honest reading, since a fresh install has the pendrive plugged
  in and a silent skip would produce a machine whose declared unlock does not
  exist.
* `apply()` — mount the key device at a temp dir when `unlock_keydev` is set,
  create the keyfile with `dd bs=512 count=4 if=/dev/random iflag=fullblock` +
  `chmod 600` when it is missing (the old script's recipe, straight from the
  wiki), `cryptsetup luksAddKey` authorised by the existing
  `luks_password`/`luks_keyfile`, unmount. Re-running is a no-op because
  `--test-passphrase` then succeeds.
* `import_state()` — nothing; capture belongs to the partition (below).

`DiskPartitionAction._setup_encryption` stops calling `_add_unlock_keyfile`:
single ownership, and the new action's idempotency check makes the fresh-install
path work identically.

`KernelCmdlineAction._derive_from_disks` gains: keydev normalization, and
`keyfile-timeout=10s` appended to `rd.luks.options` for a partition with a
keydev-backed keyfile, unless the user's `luks_options` already names a
`keyfile-timeout` (their value wins).

The initramfs backends learn the key-device filesystem:

* *mkinitcpio* — a managed `MODULES=(…)` line (new; only HOOKS was managed),
  carrying the module for each `unlock_keydev_fs`.
* *dracut* — `filesystems+=" <fs> "` in `dasik.conf`.
* Embedded case (no keydev) — mkinitcpio `FILES=(<path>)`, dracut
  `install_items+=" <path> "`. Without this the no-keydev branch writes a
  cmdline pointing at a file the initramfs does not contain, i.e. an unbootable
  machine, which is not an acceptable half-feature in a destructive installer.

`preflight` gains two checks on the expanded config: an **error** when
`unlock_keydev` is set with no `unlock_keyfile` (meaningless), and a **warning**
when `unlock_keydev` is set with no `unlock_keydev_fs` (the initramfs will
probably not be able to read the pendrive).

### Detectability (`plan`)

* Declared, nothing enrolled ⇒ `+ [luks_keyfile] <luks_name>:<path>`,
  `+ [kernel_cmdline] rd.luks.key=…` and `rd.luks.options=…keyfile-timeout=10s`,
  `~ [initramfs]`.
* Declared and enrolled ⇒ empty plan.
* Undeclared but owned ⇒ `- [kernel_cmdline] rd.luks.key=…`. The **keyslot is
  not removed**: `luksKillSlot` on the wrong slot destroys access to the volume,
  so dasik prints what it is leaving behind instead. This is the one deliberate
  asymmetry in the block and it is documented in the config reference.

### Capture (`sync`)

`DiskPartitionAction.import_state` — both paths, the declared-config reflection
and the live-layout discovery — parses the live kernel cmdline for
`rd.luks.key=<uuid>=<path>[:<keydev>]` matching the partition's LUKS UUID and
sets `unlock_keyfile`, `unlock_keydev` (verbatim spec) and, best-effort,
`unlock_keydev_fs` from `lsblk -no FSTYPE` of that UUID. `keyfile-timeout=…` is
subtracted from the captured `luks_options` when a keyfile was captured, since
dasik re-derives it — otherwise the value would be spelled twice.

Machine with no `rd.luks.key` ⇒ none of the three keys is invented.

## Testing

* TDD throughout: models, expand toggles, both initramfs backends, the cmdline
  derivation, `LuksKeyfileAction.plan`, preflight, and both `import_state`s.
  `execute()`/`apply()` bodies that only shell out are asserted through mocked
  `Command.execute`, never run.
* Both project matrices get a row per feature:
  `tests/lib/test_feature_detectability.py` (missing ⇒ planned, present ⇒
  silent, owned-but-undeclared ⇒ REMOVE) and
  `tests/lib/test_feature_sync_capture.py` (present ⇒ captured, absent ⇒ not
  invented, captured config re-plans to nothing).
* Sample config: extend the encrypted VM config so `dasik plan` exercises both.
* Gates unchanged: pytest + coverage ≥80%, mypy, bandit, `scripts/mutation.sh`.
* VM verification (`scripts/qemu.sh`, second virtual disk formatted vfat as the
  "pendrive"): splash appears, the root unlocks with the pendrive in, prompts
  for the passphrase after ~10s with it out, and a re-`plan` is a no-op. Best
  effort — the block ships with the checkbox tracked in #173 either way.

## Out of scope

`$HOME` dotfiles (config-saver's job), profiles/environments, podman/docker,
private AUR packages, the partitioning TUI — all still undefined in #173.
Removing a keyslot on un-declaration, and GPG/TPM-wrapped keyfiles, are
deliberately not attempted.

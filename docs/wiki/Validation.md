# Validation and preflight

Four layers, in order. The first three are about *shape*; the fourth is about
*coherence between blocks*, and it is the one that stops an install before the
first partition is touched.

| Layer | Catches | Runs in |
| --- | --- | --- |
| JSON parse | syntax | check, plan, apply, sync |
| include resolution | missing fragment, cycle, absolute or `..` path | check, plan, apply, sync |
| pydantic `JsonModel` | field types, enums, regexes, per-model cross-field rules | check, plan, apply, sync |
| **preflight** | coherence across blocks, on the **expanded** config | check, plan, apply — **not** sync |

`sync` deliberately skips preflight: its job is to report reality, and reality is
sometimes incoherent. That is also why you should `check` a config `sync` just
produced.

---

## Preflight severities

| Level | Meaning | Effect |
| --- | --- | --- |
| **error** | a deterministic failure provable from the config alone | aborts before any mutation |
| **warning** | a coherence smell that cannot be proven | printed, does not block |

Output:

```text
Config is not coherent — refusing to continue:
  [error] group_without_provider: user 'andres' requires group 'docker', but no declared package creates it (provided by: docker). Declare one of them or drop the group — `useradd -G` fails on a missing group.
  [warning] unknown_group: group 'plugdev' is not a base group and dasik does not know which package creates it; useradd will fail if nothing does.
```

Preflight runs on the **expanded** config, so packages and units contributed by
a [feature block](Features.md) count as declared: `"cups": {"install": true}`
satisfies the `scanner` group, because the expansion brings `sane`.

---

## The checks

### `group_without_provider`

**error.** A user declares a group that no declared package creates. `useradd
-G` fails on a missing group — *after* the disk has been wiped.

Known providers: `docker`→docker, `libvirt`/`libvirt-qemu`→libvirt,
`vboxusers`→virtualbox, `wireshark`→wireshark-cli/qt, `scanner`→sane/sane-airscan,
`plugdev`, `adbusers`→android-udev, `gamemode`, `realtime`→realtime-privileges,
`i2c`→i2c-tools.

Base groups (`wheel`, `video`, `audio`, `input`, `storage`, `render`, `kvm`, …)
exist on every Arch system and are never flagged.

### `unknown_group`

**warning.** A non-base group dasik has no provider mapping for. It cannot
prove the group will be missing, so it does not block — but `useradd` will fail
if nothing creates it.

### `unit_without_provider`

**error** for display managers, **warning** otherwise. An enabled
display-manager unit that no declared package provides means **no graphical
login at all**, and `systemctl enable` fails outright. Mapped: `sddm.service`,
`gdm.service`, `lightdm.service`, `ly.service`, `plasmalogin.service` (Plasma's
newer login manager — this is exactly the drift that broke an install on
2026-07-19).

For other known units (`docker.service`, `libvirtd.service`, `sshd.service`,
`firewalld.service`, the snapper timers, `power-profiles-daemon.service`,
`cpupower.service`, `reflector.timer`) it is only a **warning**: the provider is
often present as somebody else's dependency, so an undeclared name does not
prove the unit will be missing.

### `multiple_display_managers`

**error.** More than one display manager enabled. `display-manager.service` can
only be one of them.

### `display_manager_config_mismatch`

**warning.** `sddm_conf_d` is declared but `sddm.service` is not enabled. Those
files are read by SDDM only — Plasma Login Manager reads
`/etc/plasmalogin.conf.d`.

### `sudo_without_provider` · `wheel_without_sudo`

An explicit `sudo` block with no package providing `/usr/bin/sudo` (`sudo` or
`base-devel`) is an **error**: the fragment could not even be validated with
`visudo`. A user merely in `wheel` with no sudo package is a **warning** — a
config that installed fine yesterday must not start failing because of a
default it never asked for.

### `ppd_and_governor` · `ppd_and_tlp`

power-profiles-daemon owns the energy-performance policy. A fixed cpupower
governor will be fought over (warning); `tlp` alongside it conflicts outright
(error). Keep one.

### `crypttab_bad_option`

**error.** An `/etc/crypttab` line carrying an option that is neither a known
bare flag nor a known `key=value`. The real case: `size512`, which is neither —
the correct spelling is `size=512`.

### `crypttab_undeclared_device`

**error** when destructive, **warning** otherwise. A crypttab entry naming a
device no declared partition provides. It is an **error** when the line carries
the `swap` option, because per `crypttab(5)` that option **reformats the named
device on every boot** — pointing it at something you did not declare is a
data-loss machine.

### `keydev_without_keyfile`

**error.** `unlock_keydev` with no `unlock_keyfile`: there is no key to look
for on that device, so no `rd.luks.key` is emitted and the unlock silently
never happens.

### `keyfile_embedded_in_initramfs`

**warning.** `unlock_keyfile` with no `unlock_keydev`: the key is baked into
the initramfs, which lives on the **unencrypted ESP**. Anyone with the disk can
read it. Only defensible if your threat model is a powered-off machine whose
ESP is gone.

### `keydev_without_filesystem`

**warning.** A key device with no `unlock_keydev_fs`. Unless the root
filesystem happens to provide that module, the initramfs cannot read the device
and the boot falls back to the passphrase.

### `no_efi_firmware`

**error.** The config partitions disks, declares an EFI bootloader, and
`/sys/firmware/efi` does not exist — the installer is not booted in EFI mode.

This one is worth understanding: `bootctl install` does **not** fail on a legacy
BIOS boot. It prints "Not booted with EFI, skipping EFI variable setup", writes
the loader to the ESP, and exits 0. The install reports success and the machine
reboots straight past it into whatever the firmware finds — typically the ISO it
was installed from. Boot the ISO in UEFI mode (QEMU: OVMF; virt-manager:
*Customize before install → Overview → Firmware = UEFI*).

Only installs are affected. A day-2 run declares no `disks` and is already
booting somehow.

---

## Schema-level rules worth knowing

These come from pydantic, not preflight, and fail the config outright:

| Rule | Model |
| --- | --- |
| `encrypt: true` with no `luks_name` | `Partition` |
| more than one `rest` partition, or `rest` not last | `DiskLayout` |
| duplicate partition labels on one disk | `DiskLayout` |
| `btrfs_subvolumes` on a non-btrfs partition | `DiskLayout` |
| `device` not starting with `/dev/` | `DiskLayout` |
| a plaintext `hashed_password` | `UserModel` |
| `root` declaring a shell or groups | `UserModel` |
| a unit both enabled and disabled | `SystemdModel` |
| a multi-line sudoers rule, or an `@include`/`#include` | `SudoModel` |
| a `package_sources` key not present in `packages` | `JsonModel` |
| a `ref` that is not a full 40-char SHA, a non-HTTPS `url`, or a `url` carrying credentials | `GitPackageSourceModel` |
| a line break in an `oomd`/`systemd_*_conf` value | `systemd_conf_model` |
| `intel_pstate` with `mode: "guided"` | `CpuModel` |

Several of these are **injection boundaries**, not style rules: `luks_name`
reaches the kernel cmdline, `label` reaches `mkfs -L`, a package name reaches
pacman's argv, a reflector country becomes a config line reflector parses as
arguments. The regexes exist so a config value can never smuggle in an extra
parameter.

## What validation does *not* catch

- **Unknown keys.** The models do not set `extra="forbid"`, so a misspelled
  field validates and does nothing. If a block has no effect, check the spelling
  against [Configuration](Configuration.md).
- **Unknown driver names.** Deliberately a no-op.
- **Whether a package exists.** That is resolved at apply time against the real
  repos and the AUR ([Packages](Packages.md#how-a-name-is-resolved)).
- **Whether the machine will boot.** Only a reboot proves that. The
  [boot chain](Boot.md) page lists what dasik derives so you can check the
  entry, the hooks and the modules before rebooting.

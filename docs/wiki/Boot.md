# Boot chain

Four domains cooperate to produce a machine that actually boots, and they run
**last**, in this order:

```text
[initramfs] → [bootloader] → [kernel_cmdline]
```

plus `[pacman_hooks]`, which runs *first of all* — before the very first pacman
transaction — for reasons explained below.

Almost nothing here is declared directly. You declare *intent* (`bootloader`,
`initramfs`, `encrypt`, `plymouth`, `sysrq`, `cpu`) and dasik derives the hooks,
modules, parameters and entries. That derivation is the interesting part.

---

## Bootloader

```json
"bootloader": "sd-boot"
```

| Value | What runs | Marker used to detect it |
| --- | --- | --- |
| `sd-boot` (or `systemd-boot`, an alias) | `bootctl install` | `/boot/loader/loader.conf` |
| `grub` (default) | `grub-install --target=x86_64-efi --efi-directory=/boot --bootloader-id=GRUB` + `grub-mkconfig` | `/boot/grub/grub.cfg` |

Both are **EFI-only**. Booting the installer in legacy BIOS mode is refused by
[preflight](Validation.md#no_efi_firmware), because `bootctl install` would
print "Not booted with EFI", exit 0, and leave you rebooting into the ISO.

**Switching loaders cleans up after the old one.** dasik probes for *both*
markers, not just the declared one, so a leftover GRUB on a machine that now
declares sd-boot is removed: `bootctl remove` clears the EFI binaries and the
"Linux Boot Manager" NVRAM entry; going the other way removes `/boot/grub`,
`/boot/EFI/GRUB` and the `GRUB` NVRAM entries.

### The rescue entry

On systemd-boot dasik also writes `/boot/loader/entries/arch-fallback.conf` — a
second entry that boots the fallback initramfs. It is a domain item of its own,
so a machine that lost it plans it back:

```text
+ [bootloader] install fallback-entry  (rescue boot entry)
```

Its `initrd` is mkinitcpio's `initramfs-linux-fallback.img` when the ESP has one;
dracut builds no fallback image, so there the entry loads the same image the main
entry does — still useful, because it is a second entry you can edit at the boot
menu.

### Microcode

```json
"enable_microcode": true
```

Adds `amd-ucode` or `intel-ucode` (detected from the CPU) and — the part the
package alone does not do — puts that initrd **first** on the boot entry.

Only the image that actually exists on the ESP is listed. Listing both used to
make systemd-boot fail with `Error preparing initrd: Not found` on the absent
one, since exactly one microcode package is installed per CPU.

---

## Initramfs

```json
"initramfs": "mkinitcpio"   // or "dracut"
```

Both backends derive their configuration from the *rest* of the config —
encryption, root filesystem, hibernation, plymouth, key devices, bluetooth — and
regenerate the image when their derived input changes.

### mkinitcpio

Owns `HOOKS=` in `/etc/mkinitcpio.conf`, plus its own drop-in
`/etc/mkinitcpio.conf.d/dasik.conf`. The hook list starts from what is already
on disk (or the Arch default) and is rewritten:

| Condition | Effect on `HOOKS` |
| --- | --- |
| encryption declared | `udev`→`systemd`, `keymap`→`sd-vconsole`, `sd-encrypt` inserted after `block`, `usr`/`consolefont` dropped |
| btrfs root | `btrfs` inserted (after `systemd` when encrypted) |
| hibernation (a swap partition) | `resume` inserted **before** `filesystems` — resuming onto a mounted root eats the filesystem |
| plymouth declared | `plymouth` inserted after `systemd`/`udev` and **before** `sd-encrypt`/`encrypt` — a plymouth hook after the crypt hook never takes over the passphrase prompt, so an encrypted machine could not be unlocked at all |
| a key device with its own filesystem | its module (+ NLS charsets for FAT) added via `MODULES+=` in the drop-in |
| an embedded keyfile | `FILES+=` in the drop-in |

Everything dasik adds goes in the **drop-in**, never merged into your arrays.
That is what makes it removable: merging a `FILES=(/crypto_keyfile.bin)` into
the main conf would keep baking a LUKS key into every image forever once the
unlock is un-declared, because nothing records which entries were dasik's.

The declared plymouth theme is recorded in the drop-in as a comment. It is a
fingerprint, not a directive: a theme change must rebuild the image, and without
something changing in a file the plan would be silent and `mkinitcpio -P` would
never re-run.

### dracut

Owns `/etc/dracut.conf.d/dasik.conf` and — when encryption is declared —
`/etc/crypttab` (exclusively; the `files` domain yields it, or the two would
rewrite it on alternating applies).

Modules are **forced** rather than merely added when it matters:

| Condition | Modules |
| --- | --- |
| encrypted root | forced: `crypt`, `systemd`, `systemd-cryptsetup` (+ `btrfs` if the root is btrfs) |
| fido2 / tpm2 | their token backends |
| plain btrfs root | added: `btrfs` |
| `bluetooth.in_initramfs` | added: `bluetooth` — so a paired BT keyboard can type the passphrase |

Forcing is not paranoia. dasik runs dracut inside `arch-chroot /mnt`, where
hostonly detection does not see the target's LUKS root, so
`71systemd-cryptsetup`'s `check()` fails, the module is silently omitted, and the
machine hangs forever on `/dev/mapper/<name>`.

### The mkinitcpio neutralizer (why `pacman_hooks` runs first)

Both generators ship pacman hooks that rebuild the initramfs on kernel/systemd
updates. With dracut selected, mkinitcpio's hooks would run too and clobber
dracut's image. So dasik writes two overriding no-op hooks into
`/etc/pacman.d/hooks` (a same-named hook there overrides the one in
`/usr/share/libalpm/hooks`):

```text
90-mkinitcpio-install.hook
60-mkinitcpio-remove.hook
```

They trigger on a package name that can never exist and run `/bin/true`.
mkinitcpio stays installed; the change is reversible; and their presence is also
how `sync` recognises that dracut owns the initramfs on a machine with both
installed.

They are written in **phase 1, before pacstrap**. Contributing them to the
`files` domain (phase 4) is exactly the 2026-07-19 install failure: dracut's
hook ran, mkinitcpio's ran right after it, and `/boot/initramfs-linux.img` was
rebuilt without `sd-encrypt` — a LUKS root that nothing could open.

---

## Kernel cmdline

```json
"kernel_cmdline": ["console=ttyS0,115200", "quiet"]
```

That list is your **extra** parameters. dasik derives the rest and merges:

| Source | Derived parameters |
| --- | --- |
| encrypted partitions | `rd.luks.name=<uuid>=<mapper>`, `root=/dev/mapper/<mapper>`, `rootflags=` (subvolume + merged mount options) |
| `unlock_keyfile` (+ `unlock_keydev`) | `rd.luks.key=<uuid>=<path>:<device spec>`, plus a `keyfile-timeout` so an absent pendrive falls back to the passphrase |
| `unlock_tpm2` / `unlock_fido2` / `luks_options` | `rd.luks.options=<uuid>=…` |
| `cpu` block | `amd_pstate=<mode>` or `intel_pstate=<mode>` (`auto` detects the vendor) |
| `sysrq: true` | `sysrq_always_enabled=1` |
| `plymouth` block present | `splash` |

Merge rules: an **explicit** parameter wins over a derived one with the same
single-valued key (`root=`, `resume=`), so you can override a derivation.
`rd.luks.name`, `rd.luks.key` and `rd.luks.options` are repeatable — one
explicit entry does not drop the derived ones for other volumes.

`quiet` is deliberately **not** derived from `plymouth`: hiding the kernel's own
messages is a separate decision. Add it yourself if you want it.

### Block-owned parameters and `sync`

`amd_pstate=`, `intel_pstate=`, `sysrq_always_enabled=` and (when plymouth is
actually installed) `splash` are subtracted **by name** when `sync` captures the
boot entry — declared or not. Otherwise a captured config would carry
`amd_pstate=active` as a hand-set parameter and never grow the `cpu` block that
explains it: the same policy, spelled the way dasik cannot reason about.

What is left is what somebody really set by hand — `resume=`, `quiet`, an unlock
for a device this config does not describe — and it is kept. Dropping it is how
hibernation used to disappear from a synced machine.

---

## Plymouth

```json
"plymouth": { "theme": "bgrt" }
```

An **absent block means no splash**. An empty block (`{}`) still means "splash,
with plymouth's own default theme" — only absence turns it off. Declaring it:

- installs `plymouth` (it lives in `extra` now; the old imperative installer
  built it from the AUR);
- writes `/etc/plymouth/plymouthd.conf` when a theme is given;
- derives `splash`;
- puts the hook/module in the initramfs, in the position that lets it own the
  LUKS passphrase prompt.

---

## Hibernation

Not a toggle — it follows from a swap partition. Declare swap in `disks`, and
the initramfs backend adds the `resume` hook/module in the right position. Add
the `resume=` parameter yourself (by UUID or `/dev/mapper/...`), and size swap
at least as large as RAM.

On an encrypted machine the swap must be inside LUKS (or on the same volume) for
resume to find it after unlock. The `resume` hook is preserved through the
encrypted hook rewrite **only** when the config actually hibernates — dropping
it unconditionally, as an earlier version did, removed the very hook that
resumes the system.

---

## Verifying the boot chain without rebooting

```bash
dasik plan my-config.json --target /   # must be silent after an apply
cat /boot/loader/entries/*.conf        # sd-boot: options line, initrd order
lsinitcpio -a /boot/initramfs-linux.img   # mkinitcpio: hooks present?
lsinitrd /boot/initramfs-linux.img        # dracut: modules present?
```

A boot that hangs at `/dev/mapper/<name>` or at
`/dev/disk/by-label/root` is almost always one of the failure modes above —
[Troubleshooting](Troubleshooting.md#the-machine-hangs-at-boot-waiting-for-a-device).

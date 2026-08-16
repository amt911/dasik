# Procedures audit — August 2026

Issue [#249](https://github.com/amt911/dasik/issues/249) asks for a pass over
everything dasik automates, looking for procedures that have changed:

> se debe hacer una pasada de todos los procedimientos, para ver si hay cambios,
> como en sd boot, que ya no hace falta el hook de pacman para actualizarse, ya
> que hay servicio oficial, cosas del estilo.

Method: take the **commands dasik actually runs** (every `Command.execute` call
site) and the **files it writes**, and read each one against its Arch wiki page
in the offline mirror (`arch-wiki-docs`, August 2026). What follows is what that
turned up — the divergences first, because the rest of the document is a list of
things that are fine.

## Divergences

### 1. GRUB cannot unlock the LUKS2 volume dasik creates ([#281](https://github.com/amt911/dasik/issues/281))

`cryptsetup luksFormat --type luks2` is run with cryptsetup's defaults, which
means **Argon2id**. GRUB's own page:

> Since GRUB 2.12rc1, `grub-install` can create a core image to unlock LUKS2.
> However, it only supports PBKDF2, not Argon2. Argon2id (cryptsetup default) …
>
> *(and, newer:)* Grub 2.14rc1 supports the Argon2i and Argon2id PBKDFs.

On top of that, dasik never writes `GRUB_ENABLE_CRYPTODISK=y` — there is no
occurrence of it anywhere in the tree.

This does **not** affect dasik's own sample configs, and that is why it has gone
unnoticed: they all put an unencrypted ESP at `/boot`, so GRUB reads the kernel
and initramfs from FAT and the initramfs unlocks the root. But the disk model
happily accepts a layout where `/boot` lives **inside** the encrypted volume, and
with `bootloader: grub` that machine cannot boot — with no warning at plan time,
after the disk has been partitioned.

The fix is a preflight check, not a new feature: refuse (or loudly warn about)
`grub` + a `/boot` inside an encrypted partition, naming both the missing
`GRUB_ENABLE_CRYPTODISK` and the Argon2 limit.

### 2. LUKS TRIM is only ever a boot-time option ([#282](https://github.com/amt911/dasik/issues/282))

`Dm-crypt/Specialties` gives the LUKS2 procedure as a **persistent header flag**:

> For a LUKS2 device, TRIM support can be enabled by using the
> `--allow-discards --persistent` options when opening it. The `allow-discards`
> flag will be written into the LUKS2 header and the option will be
> automatically used whenever the LUKS2 device is opened.

dasik can express discard only as `luks_options` on the kernel command line
(`rd.luks.options=discard`), which applies to the boot-time open and to nothing
else: a volume opened by hand, from a rescue ISO, or as a second disk gets no
TRIM. The header flag is also the shape dasik prefers everywhere else — state on
the device, readable back by `sync` (`cryptsetup luksDump` already reports the
flags, and `_read_luks_tokens` already parses that output).

Low priority: the boot-time path covers the common case, and the security
trade-off of TRIM on an encrypted volume is the user's call either way.

## Notes, not defects

**The ESP at `/boot` keeps `/boot` out of your root snapshots.** The wiki no
longer presents `/boot` as *the* answer — it lists trade-offs, and one of them
lands squarely on dasik, which ships both snapper and sd-boot:

> This makes root volume snapshots (using Btrfs, …) less effective as `/boot`
> content would not be included. In case of kernel updates, returning to a
> snapshot with older kernel version would draw the system unbootable and
> require manually downgrading the kernel using external media.

Worth saying out loud on the Boot wiki page rather than changing: `/efi` +
XBOOTLDR is a real alternative, and only some boot loaders support it.

**`pacman -Sy archlinux-keyring` before pacstrap** has the shape of a partial
upgrade, which the wiki warns about — but it runs on the **live ISO**, against
the ISO's own pacman, and refreshing the keyring there is the documented fix for
an expired-key install. Correct as it stands; worth a comment so nobody
"fixes" it into `-Syu`.

## Checked and still current

Each of these was read against its page and matches what dasik does today.

| Procedure | dasik does | Verdict |
| --- | --- | --- |
| Bootstrap | `pacstrap -K /mnt …` | ✅ the `-K` form the installation guide gives |
| fstab | `genfstab -U /mnt` | ✅ |
| Time zone | `ln -sf /usr/share/zoneinfo/<Area>/<Location> /etc/localtime` | ✅ |
| Hardware clock | `hwclock --systohc` | ✅ still the guide's step, still for `/etc/adjtime` |
| Locales | `locale-gen` + `/etc/locale.conf` | ✅ |
| systemd-boot install | `bootctl install` | ✅ |
| systemd-boot updates | `systemd-boot-update.service` | ✅ **already adopted** (block A) — this is the change #249 gave as the example, and the AUR pacman hook is gone |
| Microcode | ucode `initrd` listed **before** the main initramfs, and only the one that exists | ✅ matches "the uncompressed CPIO archive with the microcode must be placed before the main initramfs" |
| mkinitcpio + LUKS | swaps `udev`→`systemd`, `keymap`→`sd-vconsole`, inserts `sd-encrypt` after `block` | ✅ `sd-encrypt` requires the systemd hook, and dasik does not leave `udev` behind |
| GRUB config | `grub-mkconfig -o /boot/grub/grub.cfg` | ✅ |
| Periodic TRIM | `fstrim.timer` | ✅ the util-linux timer, weekly, as the SSD page describes |
| btrfs discard | not set | ✅ `discard=async` is the kernel default since 6.2; setting it would be noise |
| Swap on zram | `/etc/systemd/zram-generator.conf` | ✅ |
| `/etc/hosts` | the three-line block, **now by default** | ✅ fixed in this same issue (PR #280) |
| WireGuard | the tunnel file in its backend's own format | ✅ both procedures the WireGuard page documents (§5.1 wg-quick, §5.5.1 NMConnection), implemented in PR #279 |
| sudo | a fragment in `/etc/sudoers.d`, validated with `visudo -cf` | ✅ |
| Users | `useradd -m`, `usermod -G`, `chpasswd -e` | ✅ |
| Snapper | `snapper -c <name> create-config`, snap-pac for transactions | ✅ |
| Reflector | package + `reflector.timer` + `/etc/xdg/reflector/reflector.conf` | ✅ |
| AppArmor | `lsm=` on the cmdline, which is what actually enables it | ✅ |
| faillock | `/etc/security/faillock.conf`, surviving reboot | ✅ |
| firewalld offline | `firewall-offline-cmd` for a target with no D-Bus | ✅ the documented offline path |

## What this audit did not cover

Procedures dasik does not automate (LVM, RAID, ZFS, dual-boot chainloading,
secure boot enrolment, unified kernel images). Secure Boot and UKIs are the two
most likely to become dasik's problem later — `sbctl` and `mkinitcpio --uki` are
both first-class in the wiki now, and neither has any surface in the config.

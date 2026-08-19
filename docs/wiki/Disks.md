# Disks and encryption

The `disks` block is the only part of dasik that can destroy data. Read the
[safety rules](#safety-rules) before the field tables.

```json
"disks": {
  "disks": [
    {
      "device": "/dev/nvme0n1",
      "partition_table": "gpt",
      "wipe_disk": true,
      "partitions": [ … ]
    }
  ]
}
```

The doubled key is not a typo: `disks` is an object whose single `disks` key
holds the list of devices.

---

## Safety rules

1. **Nothing is repartitioned unless you say so.** A change is planned only when
   the disk is `wipe_disk: true` **or** has no partition table at all. A
   populated disk that does not match the declared layout is *skipped* with a
   warning — dasik never silently reformats data.
   "Has no partition table" means dasik *read the disk and found none*, never
   "dasik could not tell". `parted` reports a blank disk as
   `Partition Table: unknown`, and prints that line whenever it managed to open
   the device at all — so an answer without it (no permission, no such device)
   is treated as "a table exists" and routes to the skip. Run as a normal user,
   `plan` therefore reports every disk as populated-and-skipped rather than
   offering to erase one it never managed to look at.
2. **The repartition is marked destructive in the plan**, even though its op
   reads `install`, and it names what is on the device:
   ```text
   + [disks] install /dev/nvme0n1  (wipe_disk — ERASES /dev/nvme0n1 (holds: WINDOWS, DATA))  ** DESTRUCTIVE **
   ```
   It has to pass the confirmation prompt (`--yes` skips it).
3. **`format` is not a day-2 preservation switch.** Formatting only happens
   inside a repartition, which only happens on a fresh or explicitly wiped disk
   — so every partition being created there is empty and *is* formatted. A
   captured config full of `"format": false` applied to a blank disk used to
   produce an unformatted `/boot`, a failed mount and an empty fstab. Day-2
   preservation comes from rule 1, not from this flag.
4. **`sync` never turns on a destructive flag.** A captured layout comes back
   with `wipe_disk: false` and `format: false` by construction. Making it
   installable again is a deliberate edit — [Recipes](Recipes.md#making-a-captured-disks-block-generic).

---

## Disk fields

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `device` | string | — | must start with `/dev/` |
| `partition_table` | `"gpt"` \| `"msdos"` | `"gpt"` | GPT for anything EFI |
| `wipe_disk` | bool | `false` | **DESTRUCTIVE**: `wipefs --all` + `sgdisk --zap-all` |
| `partitions` | list | — | at least one |

Per-disk schema rules: at most one partition sized `rest`, and it must be last;
labels unique within the disk; `btrfs_subvolumes` only on a btrfs partition.

## Partition fields

### Layout

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `label` | string | — | `[A-Za-z0-9_.-]{1,36}`; reaches `mkfs -L` and lsblk matching |
| `size` | string | — | `512MiB`, `1GB`, `50%`, or `rest` |
| `filesystem` | `ext4`\|`btrfs`\|`fat32`\|`swap`\|`xfs` | — | |
| `partition_type` | `esp`\|`linux`\|`linux-swap`\|`lvm` | `linux` | GPT type code |
| `mountpoint` | string \| null | `null` | `/`, `/boot`, `/home`, … |
| `mount_options` | list of strings | `[]` | also used as the base options for every subvolume |
| `format` | bool | `true` | see [safety rule 3](#safety-rules) |

Accepted size units: `B`, `KB`, `MB`, `GB`, `TB`, `KiB`, `MiB`, `GiB`, `TiB`, a
percentage `1–100%`, or `rest`.

### Encryption

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `encrypt` | bool | `false` | LUKS2 via `cryptsetup luksFormat` |
| `luks_name` | string | `null` | **required when `encrypt`**; `[A-Za-z0-9_-]+` — it becomes `/dev/mapper/<name>` and lands in the kernel cmdline |
| `luks_password` | string | `null` | passphrase, **plaintext in the config** |
| `luks_keyfile` | string | `null` | path to a key file used instead of the passphrase |
| `luks_uuid` | string | `null` | explicit UUID; unset ⇒ a deterministic one is derived |
| `luks_options` | list of strings | `[]` | extra verbatim `rd.luks.options` tokens, e.g. `token-timeout=10s` |

**The deterministic UUID matters.** The whole plan is built before anything is
applied, so on a fresh encrypted install the LUKS header does not exist yet when
the kernel parameters are computed. dasik pins the UUID up front — a stable
UUID5 of the mapper name — and formats with `--uuid=`, so one apply produces a
complete, bootable entry. Reading the UUID from the disk at plan time is what
used to leave the first apply non-bootable.

> `luks_password` is stored in clear. Keep it out of the committed config with
> `{"$include_line": "secrets/luks-passphrase"}` —
> [Config splitting](Config-splitting.md#secrets).

### Automatic unlock

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `unlock_keyfile` | string | `null` | a key file enrolled as an **additional** LUKS key → `rd.luks.key` |
| `unlock_keydev` | string | `null` | the device holding it: a bare filesystem UUID, or `UUID=`/`PARTUUID=`/`PARTLABEL=`/`LABEL=`/`/dev/…` |
| `unlock_keydev_fs` | `vfat`\|`exfat`\|`ext4`\|`btrfs`\|`xfs` | `null` | the filesystem of that device — it names the kernel module the initramfs must carry |
| `unlock_tpm2` | bool | `false` | enroll a TPM2 keyslot (passwordless) |
| `unlock_fido2` | bool \| int | `false` | FIDO2 keyslots to enroll: `true` is one key, an integer is that many — one per physical key (key present at enroll **and** boot) |

The passphrase keeps working in every case; these are extra keyslots.

```json
{
  "label": "root", "size": "rest", "filesystem": "btrfs",
  "encrypt": true, "luks_name": "cryptroot",
  "luks_password": { "$include_line": "secrets/luks" },
  "unlock_keyfile": "/crypto_keyfile.bin",
  "unlock_keydev": "1234-ABCD",
  "unlock_keydev_fs": "vfat"
}
```

That is the pendrive unlock: plug the stick in and the machine boots itself;
leave it out and it asks for the passphrase (a 10-second `keyfile-timeout`
handles the fallback).

Three things dasik does so this actually boots:

- the key device's filesystem module goes into the initramfs. FAT also needs its
  NLS charset modules, or the mount fails with `IO charset cp437 not found` —
  the commonest pendrive filesystem, unreadable, with a misleading error;
- additions live in a **drop-in** (`/etc/mkinitcpio.conf.d/dasik.conf`), never
  merged into your own arrays, so un-declaring the unlock actually removes the
  `FILES=(/keyfile)` line instead of baking the key into every future image;
- the keyslot is enrolled by an action of its own that tests the real thing —
  `cryptsetup open --test-passphrase --key-file …` — so a converged machine
  plans nothing and an already-installed machine can *gain* a pendrive.

**The keyslot is never removed.** `luksKillSlot` on the wrong slot destroys
access to the volume. Un-declaring the keyfile drops the kernel parameter and
reports the slot it is leaving behind; removing it is your call, by hand.

⚠️ **`unlock_keyfile` with no `unlock_keydev` bakes the key into the initramfs,
which lives on the unencrypted ESP.** Anyone with the disk can read it.
Preflight warns; it is only defensible if your threat model is a powered-off
machine whose ESP is gone.

### Hardware tokens (TPM2 / FIDO2)

`unlock_tpm2` and `unlock_fido2` are a domain of their own (`luks_token`), which
means they behave like everything else in dasik: **planned when the header lacks
them, silent when it has them, removed when you drop the flag.**

That was not always true. Enrolment used to happen inside the disk action,
immediately after `luksFormat` — code that only runs while a disk is being
FORMATTED. On an installed machine, adding `unlock_fido2: true` therefore did
nothing at all except add `fido2-device=auto` to the kernel command line,
pointing at a token nobody had enrolled; a failed enrolment was never retried;
and dropping the flag left the keyslot behind for good.

```
+ [luks_token] install cryptroot:tpm2  (not enrolled in the LUKS header)
- [luks_token] remove cryptroot:tpm2   (no longer declared — the keyslot is wiped
                                        (a passphrase keyslot remains))
```

**Several keys are a count.** `unlock_fido2: 2` means two keyslots, one per
physical key. The LUKS header can be *counted* and not *named* — systemd stores
a credential per key, never a label — so that is the whole vocabulary: dasik
knows how many tokens are enrolled, never which key each one is.

```
+ [luks_token] install cryptroot:fido2    (not enrolled in the LUKS header)
+ [luks_token] install cryptroot:fido2#2  (not enrolled in the LUKS header)
```

`systemd-cryptenroll --fido2-device=auto` refuses to guess between two plugged-in
keys, so they are enrolled **one at a time** and dasik asks you to swap:

```
FIDO2 key 2 of 2 for cryptroot: plug it in (and unplug the others —
systemd-cryptenroll needs exactly one), then press Enter. [s = skip the remaining keys]
```

Answer `s` and the key is not enrolled and **not recorded as enrolled**: the
apply finishes, and the next `plan` still asks for it. Declaring three keys and
finding only two in the drawer costs you a keystroke, not the install. With no
terminal to ask on (a scripted install, the VM harness) nobody is asked and
[`luks_token_policy.enroll_failure`](Config-reference) decides.

Dropping from three keys to two wipes **one** keyslot, named by number — never
`--wipe-slot=fido2`, which would take all three.

**Enrolling needs the passphrase.** `systemd-cryptenroll` authorises the new
keyslot with an existing one, so `luks_password` must be in the config for that
apply. `sync` never captures a passphrase, so a config captured from a machine
cannot enrol by itself — the plan says so instead of failing at apply time:

```
+ [luks_token] install cryptroot:tpm2  (not enrolled, and no luks_password to
   authorise it with (sync never captures the passphrase — declare it for this
   apply, or enrol by hand))
```

**Removing is guarded.** A REMOVE wipes the keyslot with
`systemd-cryptenroll --wipe-slot=`, and dasik refuses when that keyslot is the
only one in the header — wiping the last way into a volume is how a disk is
lost. It says so and keeps the slot:

```
NOTE: keeping the tpm2 keyslot on cryptroot: it is the only keyslot in the
header, and wiping it would leave the volume with no passphrase and no way in.
Add one with `cryptsetup luksAddKey` first.
```

A token dasik does **not** own (someone enrolled it by hand) is never wiped.

FIDO2 needs the key plugged in and touched at enrolment; TPM2 does not, and is
what the QEMU harness verifies (`config/vm-luks-token/`, swtpm).

### Btrfs subvolumes

| Field | Type | Default |
| --- | --- | --- |
| `name` | string | — (`@`, `@home`, …) |
| `mountpoint` | string | — (`/`, `/home`, …) |
| `mount_options` | list of strings | `["compress-force=zstd"]` |

```json
{
  "label": "root", "size": "rest", "filesystem": "btrfs",
  "mountpoint": "/",
  "mount_options": ["compress-force=zstd:3", "noatime"],
  "btrfs_subvolumes": [
    { "name": "@",        "mountpoint": "/" },
    { "name": "@home",    "mountpoint": "/home" },
    { "name": "@log",     "mountpoint": "/var/log" },
    { "name": "@pkg",     "mountpoint": "/var/cache/pacman/pkg" },
    { "name": "@.snapshots", "mountpoint": "/.snapshots" }
  ]
}
```

The partition's own `mount_options` are the **base** for every subvolume, merged
with the subvolume's own (deduplicated). So a compression policy is written once
at the partition level instead of repeated per subvolume — and `sync` hoists an
option shared by *all* subvolumes back up to the partition when it captures.

The partition is mounted even when its `mountpoint` is `null`, as long as it has
subvolumes: the subvolumes carry the mounts, and skipping it used to leave the
fstab empty. Mount order follows the shallowest effective mountpoint.

Pair this with [`snapper`](Features.md#snapper) for automatic pre/post snapshots
on every pacman transaction.

---

## What `sync` captures

`sync` **discovers the live layout** from `lsblk`, `findmnt` and `cryptsetup`,
even for a config that never declared `disks`:

| Captured | How |
| --- | --- |
| every disk with ≥ 1 partition | `lsblk`; disks with none are omitted |
| filesystem, size, mountpoint | `lsblk` + `findmnt` |
| encryption → `encrypt`, `luks_name`, `luks_uuid` | `cryptsetup` |
| `unlock_fido2` / `unlock_tpm2` | `luksDump` token types — fido2 as a COUNT (`true` for one key, `3` for three) |
| `luks_options` | `/proc/cmdline` |
| btrfs subvolumes and their options | live mounts, common options hoisted |
| labels | the real fs/partition label, else a **role label** derived from what it is: `/`→`root`, `/boot`→`boot`, `/home`→`home`, an unmounted ESP→`esp`, swap→`swap`, else `part` (deduplicated) |

Deliberately **not** captured: `wipe_disk`, `format` (always false), NTFS or
otherwise unrepresentable partitions, and still-locked LUKS volumes. A capture
is an inventory, not a lossy guess — and never an install.

---

## Common layouts

**Plain ext4, UEFI**

```json
[
  {"label":"esp","size":"1GiB","filesystem":"fat32","partition_type":"esp","mountpoint":"/boot"},
  {"label":"root","size":"rest","filesystem":"ext4","partition_type":"linux","mountpoint":"/"}
]
```

**Encrypted btrfs with subvolumes** — ESP unencrypted (it must be), everything
else inside LUKS: see the block above, plus `"initramfs": "dracut"` and
`"bootloader": "sd-boot"`.

**Swap partition for hibernation**

```json
{"label":"swap","size":"32GiB","filesystem":"swap","partition_type":"linux-swap"}
```

Hibernation also needs `resume=` on the kernel cmdline and the resume hook in
the initramfs — [Boot](Boot.md#hibernation).

**Swap re-encrypted on every boot** (and therefore unable to hibernate)

```json
{"label":"swap","size":"8GiB","filesystem":"swap","partition_type":"linux-swap",
 "swap_encryption":"random"}
```

Both swap modes, and why they exclude each other — [Swap](Swap.md).

## Related

- [Boot chain](Boot.md) — how these choices become kernel parameters and an initramfs
- [Validation](Validation.md) — the crypttab and keyfile coherence checks
- [Recipes](Recipes.md) — full working configs

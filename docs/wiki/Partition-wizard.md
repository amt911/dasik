# Partition wizard

A full-screen assistant that reads the real disks and **writes a `disks` block**.
It never partitions anything.

```bash
dasik partition-wizard --output config/mymachine/main.json
```

```
┌ dasik — partition wizard ──────────────────────────────────
│ Which disk?
│   /dev/nvme0n1  931.5G  empty
│   /dev/sda      3.6T    ntfs  MOUNTED
│ [↑↓] move   [enter] choose   [q] quit — nothing is written until the end
```

## Why it stops at the file

Partitioning is the only irreversible thing dasik does. An assistant that also
applied would fuse the exploratory half ("what disks are there?") with the
destructive one, and `plan` would stop being the last gate before a disk is
erased. So the loop stays three steps:

```bash
dasik partition-wizard --output mine.json   # compose
dasik plan mine.json                        # review — this is where the erase is announced
dasik apply mine.json                       # and only now
```

What `plan` prints for a layout the wizard composed on a populated disk:

```text
+ [disks] install /dev/vda  (wipe_disk — ERASES /dev/vda (holds: ESP, ROOT))  ** DESTRUCTIVE **
```

## Flags

| Flag | What |
| --- | --- |
| `--output FILE` | write a new config. Refuses to overwrite unless `--force` |
| `--merge-into FILE` | replace the `disks` block of an existing config, keeping everything else |
| `--force` | let `--output` replace a file that already exists |
| `--from-lsblk FILE` | read the inventory from a recorded `lsblk -J` instead of the live system |

`--from-lsblk` is how you compose a config for a machine you are not sitting at:
run `lsblk -J -b -o NAME,PATH,TYPE,FSTYPE,LABEL,SIZE,MOUNTPOINT,PTTYPE > disks.json`
there, and hand the file to the wizard here.

## The layouts

Four recipes, each one a layout this repo installs and boots in QEMU, plus a
custom path:

| Layout | What you get |
| --- | --- |
| **ESP + ext4 root** | no encryption; the simplest thing that boots |
| **ESP + encrypted (LUKS) btrfs root, with subvolumes** | no swap. `@`, `@home`, `@log`, `@pkg`, `@.snapshots`, `compress-force=zstd:3` |
| **ESP + encrypted btrfs root + encrypted swap (random key)** | the same plus swap. The swap key is new on every boot, so it **cannot hibernate** |
| **ESP + encrypted btrfs root + encrypted swap (LUKS, hibernates)** | the same, but the swap has a keyslot, so a hibernation image can be read back. Adds `resume=` |
| **Custom** | one partition at a time, validated as a set before it reaches a screen |

Every row names the whole layout, and the partitions it would create are listed
under the cursor as you move — so choosing does not depend on remembering what
each name implies:

```text
Which layout?
  ESP + ext4 root
  ESP + encrypted (LUKS) btrfs root, with subvolumes
  ESP + encrypted btrfs root + encrypted swap (random key)
  ESP + encrypted btrfs root + encrypted swap (LUKS, hibernates)
  Custom — compose the partitions yourself

  Same as above plus swap. The swap key is new on every boot, so it is safe
  but CANNOT hibernate.

  ESP      512MiB fat32  -> /boot
  swap       8GiB swap   [random key, cannot hibernate]
  root       rest btrfs  [LUKS cryptroot]
           subvolumes: @->/ @home->/home @log->/var/log @pkg->… @.snapshots->/.snapshots
```

The hibernate one adds a `kernel_cmdline` entry because `resume=` is not derived
from anything: without it the machine has a swap it can never resume from. The
review screen says so before you accept.

## The passphrase never enters the JSON

An encrypted layout asks for the passphrase (echoed as asterisks) and writes:

```json
"luks_password": { "$include_line": "secrets/luks-passphrase" }
```

…while the passphrase itself goes to `secrets/luks-passphrase` **beside the
config**, at mode `0600`. That path is what `$include_line` resolves against.

The wizard writes the file rather than leaving you the reference, because a
config pointing at a secret that does not exist is one `dasik check` refuses —
the reference alone would hand you something the tool rejects.

> **Do not commit that file.** `config/*/secrets/` in `.gitignore`, as
> [Config splitting](Config-splitting.md#secrets) describes.

## What it will not let you do

- **Install onto a floppy or an empty card slot.** Devices under 1 GiB and
  anything named `fd*` are not offered — QEMU hands every guest a 4 KiB
  `/dev/fd0` that `lsblk` calls a disk and sorts first.
- **Erase a populated disk by accident.** A disk that is not empty does not get
  a yes/no with a default; it gets two rows you have to choose between —
  *ERASE* or *Simulate* (below).
- **Type a size the schema will not take.** Sizes and labels are checked at the
  prompt by asking the model, and a refusal is shown with its reason and asked
  again.

## Simulating on a disk that is full

A disk with no free space is still worth pointing the wizard at: you may want to
see what a layout would be, keep the block for later, or hand it to another
machine. So the "this disk is not empty" screen offers both:

```text
This disk is not empty
  ERASE /dev/sda — set wipe_disk, so `dasik apply` repartitions it
  Simulate — compose the layout WITHOUT erasing /dev/sda
```

**Simulate writes the same config with `wipe_disk: false`.** Nothing is erased,
and `dasik plan` then says so rather than proposing anything:

```text
Warning: /dev/vda is populated and does not match the declared layout;
         set wipe_disk:true to repartition. Skipping.
```

That is dasik's own rule — it never silently reformats a populated disk — so the
simulation is safe by construction rather than by the wizard being careful. The
review screen says which of the two you picked before it writes anything.

## The terminal it needs

curses, on a real terminal. A serial console is fine — it is verified over one
(`TERM=vt220`) in the QEMU harness, and the screens are deliberately plain:
reverse video for the selected row, no colour pairs, no line-drawing characters.

Run from a pipe or a script, it says so and exits rather than ending a
partitioning session on `setupterm: could not find terminal`.

A **stray `ESC` never abandons a menu** — `q` does. An arrow key *is* an escape
sequence, and on a slow line its `ESC` can arrive alone, so a menu that quit on
`ESC` would quit on the very keys it tells you to use.

## Where the pieces live

| Layer | Module | Pure? |
| --- | --- | --- |
| inventory | `dasik/lib/wizard/inventory.py` | yes — `lsblk -J` in, disks out |
| layouts | `dasik/lib/wizard/recipes.py` | yes — options in, a `disks` stanza out |
| composition | `dasik/lib/wizard/compose.py` | writes the config and the secret |
| screens | `dasik/lib/wizard/tui.py` | curses; collects choices and nothing else |

The split is what makes the wizard testable without a disk: a recorded `lsblk`
payload and a script of keystrokes drive the whole thing.

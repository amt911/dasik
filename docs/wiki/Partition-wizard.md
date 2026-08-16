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
| **ESP + ext4 root** | the simplest thing that boots. No encryption |
| **ESP + LUKS + btrfs subvolumes** | encrypted root with `@`, `@home`, `@log`, `@pkg`, `@.snapshots` and `compress-force=zstd:3` |
| **…and a swap with a random key** | adds `swap_encryption: random` — **cannot hibernate**, by design |
| **…and a LUKS swap that can hibernate** | adds a swap inside LUKS **and** `resume=/dev/mapper/cryptswap` |
| **Custom** | one partition at a time, validated as a set before it reaches a screen |

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
- **Erase a populated disk by accident.** A disk that is not empty has to be
  confirmed, and declining abandons rather than composing a layout `plan` would
  refuse anyway (dasik never silently reformats).
- **Type a size the schema will not take.** Sizes and labels are checked at the
  prompt by asking the model, and a refusal is shown with its reason and asked
  again.

## The terminal it needs

curses, on a real terminal. A serial console is fine — it is verified over one
(`TERM=vt220`) in the QEMU harness, and the screens are deliberately plain:
reverse video for the selected row, no colour pairs, no line-drawing characters.

Run from a pipe or a script, it says so and exits rather than ending a
partitioning session on `setupterm: could not find terminal`.

## Where the pieces live

| Layer | Module | Pure? |
| --- | --- | --- |
| inventory | `dasik/lib/wizard/inventory.py` | yes — `lsblk -J` in, disks out |
| layouts | `dasik/lib/wizard/recipes.py` | yes — options in, a `disks` stanza out |
| composition | `dasik/lib/wizard/compose.py` | writes the config and the secret |
| screens | `dasik/lib/wizard/tui.py` | curses; collects choices and nothing else |

The split is what makes the wizard testable without a disk: a recorded `lsblk`
payload and a script of keystrokes drive the whole thing.

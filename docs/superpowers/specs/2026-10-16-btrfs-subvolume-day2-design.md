# Day-2 btrfs subvolumes — design

## Intent

On an installed machine, adding or removing an entry in a partition's
`btrfs_subvolumes` must change the machine. Today it does not:
`DiskPartitionAction.plan()` treats a disk as converged when its partition
LABELS match and skips it entirely, so a new `@var_lib_ollama` in
`msi-ge63/disks.json` produces no plan line and no apply. The user hit this
while adding `@var_lib_ollama` / `@var_lib_llama.cpp` to two installed
machines and had to do it by hand.

Why the user wants subvolumes at all: data that must stay **out of snapper
snapshots of `@`** and **survive a rollback of `@`** (model caches, container
storage, …).

## Behaviour (decided by the user)

- **Add** a subvolume to an installed btrfs:
  1. create it at the top level of the filesystem (flat layout, next to `@`);
  2. move what already lives at the mountpoint into it, **the fastest way
     possible** (reflink copy, same filesystem);
  3. **delete the old contents** of the directory in the parent subvolume —
     otherwise they stay in every `@` snapshot, which is the reason for the
     subvolume in the first place;
  4. mount it there and record it in `/etc/fstab`.
- **Remove** a subvolume that dasik owns:
  1. copy its contents back to the same path inside the parent subvolume
     (reflink);
  2. unmount it, drop its `/etc/fstab` line;
  3. **delete the subvolume**.

The data is never lost in either direction; only its location changes.

## Goals

- Missing on target ⇒ `plan` shows a change; present ⇒ silent; declared off but
  owned in the manifest ⇒ REMOVE (AGENTS.md detectability rule).
- `plan` → `apply` → `plan` is silent; `sync` → `check` → `plan` is silent.
- Works against `--target /` (the running machine) and `/mnt` (live ISO).
- A fresh install is unchanged: the subvolumes are still created by
  `DiskPartitionAction._create_btrfs_subvolumes` and written to fstab by
  genfstab.

## Non-goals

- Converting nested subvolumes (like the one ollama's tmpfiles `Q` makes)
  into flat ones. A path that already IS a subvolume is refused with a message
  (see Safety).
- Changing the filesystem, partitions or LUKS of an installed disk.
- Subvolumes on disks not declared with `btrfs_subvolumes`.

## Anti-goals (a failure even if it "works")

- Deleting data: a copy that failed or was partial followed by a delete.
- Touching a subvolume dasik never owned (one created by hand, docker's,
  snapper's snapshots, an unrelated data disk's).
- Removing or replacing `/` (`@`) or anything that is the current root.
- Running on a disk that this same apply is formatting, or writing a second
  fstab line that genfstab then duplicates.

## Constraints from the codebase

- The reconciler builds the WHOLE plan before any apply
  (`Reconciler.build_plan` → `apply`), so a disk that is not converged at plan
  time (fresh / being wiped) is simply skipped by the new domain — that is how
  the fresh-install path stays untouched.
- `DiskPartitionAction.import_state` is the single author of the `disks` block
  in `sync`, and already captures mounted subvolumes (`_btrfs_subvols`). The new
  action emits nothing from `import_state` (same reasoning as
  `EncryptedSwapAction.import_state`), but MUST implement `actual()` so its
  domain survives `sync` (memory: an action without `actual()` is dispossessed
  on every sync).
- Shell out only via `Command.execute`; paths through `context.target`.
- Mirror `EncryptedSwapAction` for the fstab append/drop shape.

## Shape (builder's latitude inside this)

- New action `BtrfsSubvolumeAction`, `config_key='__root__'` (reads `disks`),
  domain `btrfs_subvolumes`, registered right after `LuksTokenAction` (volumes
  open, before anything that writes into `/var/lib/…`).
- Item key: `<partition label>:<subvolume name>`, e.g. `root:@var_lib_ollama`.
- `actual()`: declared subvolumes on converged disks that exist on the
  filesystem AND are mounted at their mountpoint AND have their fstab line.
- `plan(managed)`:
  - INSTALL for each declared subvolume on a converged disk not in `actual()`;
  - REMOVE for each owned (`managed`) item no longer declared that still exists;
  - a mountpoint change of a declared subvolume (same name) is a MODIFY:
    remount + rewrite its fstab line, data stays in the subvolume;
  - `destructive=True` whenever old contents or a subvolume will be deleted,
    so the reconciler's confirmation prompt lists it.
- `apply`: mount the filesystem's top level (`subvolid=5`) on a temporary
  directory under the target, do all copies there (both sides are plain paths
  in one mount, so `cp -a --reflink=always` works and never silently falls
  back to a full copy), then mount/unmount the real mountpoint.
- fstab line in genfstab's form: `UUID=<fs uuid> <mp> btrfs <opts>,subvol=/<name> 0 0`,
  options from `_subvol_mount_options` (reuse, do not copy).

## Safety

- Copy must finish with rc 0 before anything is deleted; `check=True` on every
  step, abort the apply on the first failure (partial generation, as usual).
- Refuse, with a clear message, when:
  - the mountpoint is `/`, or is the current root's subvolume;
  - the path is busy (a process has a file or its cwd under it) — say which
    process, the user stops it and re-runs;
  - the path being added is already a subvolume or a mountpoint of something
    else (e.g. ollama's nested `Q` subvolume, a bind mount);
  - the subvolume being removed contains nested subvolumes.
- Never delete a subvolume that is not in `managed`.

## Verification

- TDD for the decision logic (`plan`/`actual`/refusals) with mocked
  `Command.execute` and a scratch target.
- Detectability + sync matrices extended
  (`tests/lib/test_feature_detectability.py`,
  `tests/lib/test_feature_sync_capture.py`).
- VM (minimal resources: host has ~12 GiB RAM free and ~60 GiB disk): one
  install with a trimmed package set and a btrfs root, then a guest script that
  adds a subvolume over a non-empty directory, applies, asserts (data present,
  is a subvolume, mounted, fstab line, old contents gone from `@`, second plan
  silent), removes it, applies, asserts the reverse, and checks
  `findmnt --verify` + a reboot that mounts cleanly. Each assertion seen red
  once.

## Decisions taken after the builder check (author's calls, not the user's)

1. **Ownership = declared and present.** Every declared subvolume that exists
   and is mounted is owned, including the install-time ones (`@home`, …), so
   removing any of them from the list migrates and deletes it, as the user's
   rule says. Busy paths are refused, so `@home` with a session open cannot be
   pulled out from under it.
   REMOVE is planned **only while the partition itself is still declared** and
   the subvolume has left its list. A dropped `disks` block, a dropped disk or a
   dropped partition (the reconciler's empty-config case) plans **nothing** —
   never "remove everything".
   A subvolume `sync` captured from a hand-made one becomes declared, hence
   owned; that is the user taking it over in the config, which is fine.
2. **Matching an existing machine is semantic, not textual.** "Has its fstab
   line" = some fstab line for that mountpoint with `subvol=/<name>` or
   `subvol=<name>` (genfstab's `subvolid=` and `/dev/mapper/…` sources accepted).
   No rewrite of lines that already match. The two hand-made subvolumes on the
   user's machines therefore read as converged.
   A declared subvolume that **already exists at the top level but is not
   mounted / not in fstab** is adopted: mounted + fstab line added, with the
   same content rule as (3).
3. **Never merge into a non-empty destination.** If both sides have content
   (ADD into an already non-empty subvolume, or REMOVE onto a non-empty
   directory in the parent), refuse and say which paths; nothing is copied or
   deleted. An empty side is fine.
4. **Copy = reflink first, full copy as fallback.** `cp -a --reflink=auto` is
   not used blindly: try `--reflink=always`; if it fails (NOCOW files, e.g.
   libvirt images), check free space on the filesystem against the source's
   size and do a normal `cp -a`; if the space is not there, refuse before
   deleting anything. A NOCOW (`+C`) source directory gets `+C` set on the new
   subvolume before copying so its files stay NOCOW.

## Left to the builder

- Exact refusal wording, temp-mount location, how "busy" is detected
  (`fuser -m` vs scanning `/proc`).
- Whether MODIFY of mount options remounts live or only rewrites fstab.
- Finding the parent subvolume of a nested mountpoint.

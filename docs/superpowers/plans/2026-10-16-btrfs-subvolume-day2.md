# Day-2 btrfs subvolumes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adding/removing/moving an entry of a partition's `btrfs_subvolumes` on an installed machine is planned and applied (create+migrate / migrate back+delete / remount).

**Architecture:** A new v3 action `BtrfsSubvolumeAction` (domain `btrfs_subvolumes`, `config_key='__root__'`) registered right after `LuksTokenAction`. Pure decision logic (fstab/findmnt/subvolume-list parsing, the diff) lives in `btrfs_subvol_state.py` so it is mutation-testable; the action does the probing and the `Command.execute` mechanics. Disks that `DiskPartitionAction` does not consider converged are skipped, so fresh installs are untouched.

**Tech Stack:** Python ≥3.10, pydantic models already in `dasik/lib/models/disk_model.py`, pytest + mocked `Command.execute`, QEMU harness `scripts/vmtest/qemu.sh`.

**Spec:** `docs/superpowers/specs/2026-10-16-btrfs-subvolume-day2-design.md`

## Global Constraints

- Shell out only via `Command.execute` (`dasik/lib/command_worker/command_worker.py`); never `subprocess`.
- All mechanics run on the HOST with host paths from `context.target.path(...)` (no `target=` → no arch-chroot): `mount`, `btrfs`, `cp` must see the real block device.
- Item key: `"<partition label>:<subvolume name>"`, e.g. `"root:@var_lib_ollama"`.
- fstab line form: `UUID=<fs uuid> <mountpoint> btrfs <opts> 0 0`, `<opts>` = `",".join(DiskPartitionAction._subvol_mount_options(partition, subvol))` — reuse, don't copy. (It already appends `subvol=<name>`.)
- Never touch: mountpoint `/`, the subvolume mounted at `/`, any subvolume not in `managed` (REMOVE), any disk not converged per `DiskPartitionAction._disk_converged`.
- REMOVE only while the partition (by label) is still declared; empty config / dropped disk / dropped partition ⇒ no changes.
- Copy: `cp -a --reflink=always SRC/. DST/`; on failure, free-space check then `cp -a SRC/. DST/`; nothing is deleted unless the copy returned 0.
- No new dependencies. Stage explicit paths only (never `git add -A`).
- Gates on push: pytest+cov ≥80%, mypy, bandit, mutation (`scripts/mutation.sh`).

## Review Focus

1. Fresh install of `config/vm-btrfs.json` (disk wiped this run) ⇒ the new domain plans **nothing** and writes no fstab line (genfstab would duplicate it). Test in Task 2.
2. Machine fixed by hand with a genfstab-style line (`/dev/mapper/x … subvolid=262,subvol=/@var_lib_ollama`) ⇒ converged, no MODIFY. Test in Task 1.
3. Config with the whole `disks` key removed but manifest owning `root:@x` ⇒ no REMOVE. Test in Task 2.
4. Copy fails (rc≠0) mid-ADD ⇒ old contents still there, subvolume deleted again or left empty, apply raises. Test in Task 3.
5. Path busy (a process cwd/fd under it) ⇒ apply raises naming the pid before any mutation. Test in Task 3.

---

### Task 1: Pure state + diff (`btrfs_subvol_state.py`)

**Files:**
- Create: `dasik/lib/actions/btrfs_subvol_state.py`
- Test: `tests/lib/actions/test_btrfs_subvol_state.py`
- Modify: `pyproject.toml` `[tool.mutmut] only_mutate` — add `dasik/lib/actions/btrfs_subvol_state.py` (+ one comment line in the existing block's style).

**Interfaces — Produces:**
- `parse_fstab_subvols(text: str) -> dict[str, str]` — mountpoint → subvolume name (leading `/` stripped) for btrfs lines carrying `subvol=`; comments/blank/other fs ignored.
- `parse_findmnt(text: str) -> list[MountRow]` for `findmnt -rn -t btrfs -o TARGET,SOURCE,UUID,OPTIONS` output; `MountRow = NamedTuple(target: str, device: str, uuid: str, subvol: str)` — `device` is SOURCE without the `[/…]` suffix, `subvol` from `subvol=` option, leading `/` stripped. findmnt's `\x20` escapes decoded in target.
- `parse_subvolume_list(text: str) -> set[str]` for `btrfs subvolume list <mp>` output — only `top level 5` entries, their `path`.
- `@dataclass(frozen=True) class Live: mounted: dict[str, str]  # subvol name -> canonical mountpoint; fstab: dict[str, str]  # canonical mountpoint -> subvol; existing: set[str]`
- `@dataclass(frozen=True) class Wanted: key: str; label: str; name: str; mountpoint: str`
- `diff(wanted: list[Wanted], live_by_label: dict[str, Live], declared_labels: set[str], managed: list[str]) -> list[tuple[Op, str, str]]` — `(op, key, reason)`:
  - wanted, label not in `live_by_label` ⇒ nothing (disk not converged / partition not mounted);
  - mounted at its mountpoint AND `fstab[mountpoint] == name` ⇒ nothing;
  - mounted at a DIFFERENT mountpoint ⇒ `(Op.MODIFY, key, "move <old> -> <new>")`;
  - otherwise ⇒ `(Op.INSTALL, key, reason)` with reason `"create + move contents"` when `name not in existing`, `"adopt existing subvolume"` when it exists, `"add fstab line"` when mounted correctly but fstab missing;
  - for `k in managed`, `k` not wanted, `k.split(":",1)[0] in declared_labels` and in `live_by_label`, name in `existing`, name not mounted at `/` ⇒ `(Op.REMOVE, k, "no longer declared")`.

- [ ] **Step 1: Write failing tests** (names + key asserts):
  - `test_fstab_reads_genfstab_style_line`: `"/dev/mapper/cryptroot /var/lib/ollama btrfs rw,noatime,compress-force=zstd:3,subvolid=262,subvol=/@var_lib_ollama 0 0"` → `{"/var/lib/ollama": "@var_lib_ollama"}`.
  - `test_fstab_ignores_comments_and_non_btrfs`: `"# x\nUUID=a /boot vfat rw 0 2\n"` → `{}`.
  - `test_findmnt_strips_bracket_and_slash`: `"/ /dev/mapper/cryptroot[/@] 1111 rw,subvolid=256,subvol=/@\n"` → `[MountRow("/", "/dev/mapper/cryptroot", "1111", "@")]`.
  - `test_subvolume_list_keeps_only_top_level`: `"ID 256 gen 9 top level 5 path @\nID 300 gen 9 top level 256 path var/lib/ollama\n"` → `{"@"}`.
  - `test_diff_converged_is_silent`, `test_diff_missing_subvolume_is_install_create`, `test_diff_existing_unmounted_is_adopt`, `test_diff_missing_fstab_only`, `test_diff_moved_mountpoint_is_modify`, `test_diff_owned_undeclared_is_remove`, `test_diff_unowned_undeclared_is_left_alone`, `test_diff_dropped_partition_plans_no_remove` (label absent from `declared_labels`), `test_diff_never_removes_root_subvolume` (managed `root:@`, `@` mounted at `/`), `test_diff_unconverged_label_plans_nothing`.
- [ ] **Step 2:** `pytest tests/lib/actions/test_btrfs_subvol_state.py -v` → FAIL (module missing).
- [ ] **Step 3:** Implement the functions above. No IO, no `Command`.
- [ ] **Step 4:** Same command → PASS; `scripts/mutation.sh` → no real survivors in the new module (document equivalents by diff signature, as the repo does).
- [ ] **Step 5:** Commit `feat(btrfs): pure state + diff for day-2 subvolumes`.

### Task 2: Action — probing, plan, actual, registration

**Files:**
- Create: `dasik/lib/actions/btrfs_subvolume_action.py`
- Modify: `dasik/lib/actions/actions_handler_v2.py` (register after `LuksTokenAction`, `config_key='__root__'`, `is_optional=True`, comment why: volumes open, before anything writes under `/var/lib`).
- Test: `tests/lib/actions/test_btrfs_subvolume_action_plan.py`; extend `tests/lib/test_feature_detectability.py`.

**Interfaces:**
- Consumes: Task 1 functions; `DiskPartitionAction(cfg)` for `.disks` and `._disk_converged(disk)`; `DiskPartitionAction._subvol_mount_options`.
- Produces: `class BtrfsSubvolumeAction(AbstractAction)` with `_DOMAIN = "btrfs_subvolumes"`, `name -> "Btrfs Subvolumes"`, `is_optional -> True`, `empty_config() -> {}`, `actual() -> set[str]` (converged keys), `managed_keys() -> {"btrfs_subvolumes": sorted(actual())}`, `plan(managed) -> list[Change]`, `import_state(managed=None) -> {}` (docstring: `disks` has one author, as in `EncryptedSwapAction.import_state`). Private probe `_live() -> dict[str, Live]` keyed by partition label, built from: `findmnt -rn -t btrfs -o TARGET,SOURCE,UUID,OPTIONS` (host), target's `/etc/fstab` file, `btrfs subvolume list <host path of a mounted subvol of that fs>`. A partition is matched to its filesystem by any of its declared subvolumes being mounted (`subvol` + canonical mountpoint); canonical = host target minus `target.root` prefix. Change `destructive=True` when INSTALL will delete old contents (host dir at mountpoint non-empty and not itself a mountpoint) and for MODIFY never.
- [ ] **Step 1: Failing tests** (fake target = tmp_path with `etc/fstab`; monkeypatch `Command.execute` to return canned findmnt / subvolume-list stdout by argv):
  - `test_fresh_disk_plans_nothing`: `_disk_converged` false (lsblk returns no labels) → `[]`.
  - `test_new_subvolume_on_installed_disk_is_planned`: `@`,`@home` mounted+fstab, config adds `@var_lib_ollama` → one `Change("btrfs_subvolumes", Op.INSTALL, "ROOT:@var_lib_ollama")`.
  - `test_install_over_non_empty_dir_is_destructive`: `tmp_path/var/lib/ollama/model.bin` exists → `change.destructive is True`; empty dir → False.
  - `test_converged_plans_nothing` and `test_actual_lists_converged_keys`.
  - `test_dropped_disks_block_removes_nothing`: config `{}` with `managed=["ROOT:@home"]` → `[]`.
  - `test_owned_subvolume_dropped_from_list_is_removed`.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** Implement; register in `setup_actions()`.
- [ ] **Step 4:** run Task 1+2 tests + `pytest tests/lib/test_feature_detectability.py` → PASS; add the three detectability cases there (missing ⇒ planned, present ⇒ silent, owned-undeclared ⇒ REMOVE) and the unowned-left-alone case.
- [ ] **Step 5:** `pytest` full + `mypy dasik` → clean. Commit `feat(btrfs): plan day-2 subvolume changes`.

### Task 3: Apply mechanics + refusals

**Files:**
- Modify: `dasik/lib/actions/btrfs_subvolume_action.py`
- Modify: `dasik/lib/exceptions/exceptions.py` — add `class SubvolumeChangeRefused(Exception)`.
- Test: `tests/lib/actions/test_btrfs_subvolume_action_apply.py`

**Interfaces — Produces:** `apply(changes) -> None`; private `_top_level_mount(device) -> contextmanager[str]` (mount `-o subvolid=5 <device>` on `target.path("/run/dasik-btrfs-top")`, always unmount+rmdir in `finally`); `_busy_pids(host_path: str) -> list[int]` (scan `/proc/*/cwd`, `/proc/*/fd/*` readlinks under the path; pure-python, testable with a fake proc root parameter); `_copy(src: str, dst: str) -> None` (reflink→space check via `os.statvfs` vs `du -sb`→plain; raises `CommandExecutionError` on rc≠0).

Order per op (all paths inside the top-level mount `T`; parent of a mountpoint = the declared/mounted subvolume whose mountpoint is the longest prefix of it, normally `@` for `/`):
- **INSTALL create:** refuse checks → `btrfs subvolume create T/<name>` → if `T/<parent>/<rel>` has content: `_copy` it into `T/<name>`, then delete its entries (not the dir) → (if source dir has `+C` via `lsattr -d`, `chattr +C T/<name>` BEFORE copying) → `mkdir -p` mountpoint → `mount -o <opts> <device> <host mp>` → append fstab line.
- **INSTALL adopt:** refuse if both `T/<name>` and the parent dir are non-empty; else move content as above if parent dir non-empty → mount → fstab.
- **INSTALL fstab-only:** append line.
- **REMOVE:** refuse checks (busy, nested subvolumes via `btrfs subvolume list -o T/<name>`, parent dir non-empty) → `umount <host mp>` → `_copy(T/<name>, T/<parent>/<rel>)` → drop fstab line(s) for that mountpoint → `btrfs subvolume delete T/<name>`.
- **MODIFY (move):** busy check old mp → `umount old` → rewrite fstab line → mkdir+mount new.
- Refusals raise `SubvolumeChangeRefused` with the path and what to do, BEFORE any mutation for that item: mountpoint `/`; busy (`pid N (comm)`); add-path is already a mountpoint (`os.path.ismount`) or a subvolume (`btrfs subvolume show` rc 0 on the parent-side path, i.e. ollama's nested `Q`); both sides non-empty; not enough space for the non-reflink fallback.

- [ ] **Step 1: Failing tests** (record argv list from mocked `Command.execute`; fake top-level mount = tmp dirs):
  - `test_install_create_runs_create_copy_mount_fstab_in_order` — asserts argv order `btrfs subvolume create` < `cp -a --reflink=always` < `mount -o …subvol=@var_lib_ollama` and fstab gains `UUID=1111 /var/lib/ollama btrfs compress-force=zstd:3,subvol=@var_lib_ollama 0 0`.
  - `test_install_deletes_old_contents_after_copy` — old file gone from the parent-side dir, dir itself kept.
  - `test_copy_failure_deletes_nothing_and_raises` (Review Focus 4).
  - `test_reflink_failure_falls_back_to_plain_copy_when_space_allows` / `test_..._refuses_when_space_short`.
  - `test_busy_path_refused_before_any_mutation` (Review Focus 5) — no `btrfs`/`mount` argv recorded.
  - `test_nested_subvolume_at_path_refused` (ollama `Q` case).
  - `test_remove_copies_back_unmounts_drops_fstab_deletes` (order: `umount` < `cp` < `btrfs subvolume delete`; fstab line gone).
  - `test_remove_refused_when_parent_dir_not_empty`, `test_remove_refused_with_nested_subvolumes`, `test_root_never_touched`.
  - `test_modify_moves_mountpoint_and_rewrites_fstab`.
  - `test_top_level_mount_unmounted_even_on_failure`.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** run → PASS; break one assertion on purpose per test family, see red, restore. **Step 5:** full `pytest --cov=dasik`, `mypy dasik`, `bandit -r dasik -q` → clean. Commit `feat(btrfs): apply day-2 subvolume add/remove/move`.

### Task 4: sync round trip, docs, VM config + guest script

**Files:**
- Modify: `tests/lib/test_feature_sync_capture.py` — machine with `@var_lib_ollama` mounted ⇒ captured `disks` lists it; captured config re-plans to nothing in `btrfs_subvolumes`.
- Modify: `docs/config-reference.md` (the `btrfs_subvolumes` entry: day-2 behaviour, refusals) and `docs/wiki/` page for disks if it describes subvolumes (search `rg -n btrfs_subvolumes docs/wiki`).
- Create: `config/vm-subvol-day2.json` (= `config/vm-btrfs.json` + `python`, `python-pydantic`, `python-colorama` in packages so dasik runs in the guest; nothing else), `config/vm-subvol-day2-add.json` (+ `{"name":"@data","mountpoint":"/srv/data"}`), `config/vm-subvol-day2-move.json` (`@data` at `/srv/data2`).
- Create: `scripts/vmtest/guest-subvol-day2.sh` echoing `SUBVOL-<step>=rc` lines, ending `SUBVOL-DONE`.

Guest script steps (target `/`, repo at `/root/repo`, mirror `guest-day2.sh`):
1. `SUBVOL-BASE`: `plan config/vm-subvol-day2.json --target /` prints no `[btrfs_subvolumes]` line.
2. Seed `/srv/data/big.bin` (`head -c 50M /dev/urandom`), sha256 it.
3. `SUBVOL-PLAN-ADD`: plan add-config shows `+ [btrfs_subvolumes] install ROOT:@data`.
4. `SUBVOL-APPLY-ADD`: `apply … --yes` rc 0; `findmnt -no OPTIONS /srv/data | grep subvol=/@data`; sha256 matches; mount top-level and assert `@/srv/data` is empty; `grep -q '/srv/data btrfs' /etc/fstab`; `findmnt --verify` rc 0.
5. `SUBVOL-REPLAN-ADD`: plan add-config has no `[btrfs_subvolumes]` line.
6. `SUBVOL-SYNC`: `sync --output /tmp/s.json` then `check /tmp/s.json` rc 0 and `plan /tmp/s.json` has no `[btrfs_subvolumes]` line.
7. `SUBVOL-MOVE`: apply move-config; `/srv/data2` mounted, `/srv/data` not, fstab updated, sha ok.
8. `SUBVOL-REMOVE`: apply base config; `btrfs subvolume list / | grep -c '@data$'` is 0; `/srv/data2/big.bin` is a plain file in `@` with same sha; no fstab line.
9. `SUBVOL-REBOOT`: (second `drive` run) after reboot `findmnt --verify` rc 0 and `plan` base config silent.

- [ ] **Step 1:** sync test → FAIL then PASS. **Step 2:** docs. **Step 3:** configs validate: `dasik check config/vm-subvol-day2*.json` → OK ×3. **Step 4:** commit `test(vm): day-2 subvolume guest script + configs`.

### Task 5: VM run (minimal resources) and PR

- [ ] Fresh qcow2: `DASIK_VM_RAM=2048 DASIK_VM_DISK=8G DASIK_VM_WORKDIR=/var/tmp/dasik-vmtest-subvol scripts/vmtest/qemu.sh install-driven config/vm-subvol-day2.json` → install OK.
- [ ] `scripts/vmtest/qemu.sh drive $DASIK_VM_WORKDIR/vda.qcow2 guest-subvol-day2.sh SUBVOL-DONE` → every `SUBVOL-*=0`.
- [ ] Mutation-in-guest: revert the "delete old contents" step, re-drive from a fresh copy of the post-install qcow2, see `SUBVOL-APPLY-ADD` go red, restore.
- [ ] Delete the qcow2s afterwards (disk is tight).
- [ ] Push, PR with "How to test manually", agentic verdict comment naming which verbs ran for real in the guest.

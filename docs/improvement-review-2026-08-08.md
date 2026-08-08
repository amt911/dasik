# Improvement review — 2026-08-08

A read of the whole `dasik/` package looking for things worth changing, written
after two real failures found the same week (PR #169 — a systemd unit no package
provides; PR #170 — dracut silently dropping its `resume` module). Every finding
below is backed by code or by a command whose output is quoted; nothing here is
a guess about what the code "probably" does.

Nothing in this document is implemented. It is a list of candidates, ordered by
what would hurt most if it stayed.

---

## P0 — Wiping a disk is not treated as a destructive change

`Change.destructive` is membership in a fixed op set:

```python
# dasik/lib/state/change.py
_DESTRUCTIVE_OPS = frozenset({Op.REMOVE, Op.DISABLE, Op.DELETE})
```

`DiskPartitionAction.plan()` emits the repartition step as `Op.INSTALL`:

```python
# dasik/lib/actions/disk_partition_action.py
changes.append(Change(
    self._DOMAIN, Op.INSTALL, disk.device,
    reason="wipe_disk" if disk.wipe_disk else "empty disk",
))
```

So the classification is inverted at the worst possible place:

```text
$ python -c "…"
wipe.destructive           -> False
remove-package.destructive -> True
plan.destructive()         -> ['vim']

  + [disks] install /dev/nvme0n1  (wipe_disk)
  - [packages] remove vim
```

`Reconciler.apply()` only prompts `Apply N destructive change(s)? [y/N]` when
`plan.destructive()` is non-empty, and `_warn_live_host()` is gated on the same
list. Consequences:

* An apply whose plan is **only** disk work — the fresh-install case, and any
  re-partition — runs `wipefs --all`, `sgdisk --zap-all` and `mkfs` **without
  ever asking**. Removing `vim` asks; erasing the disk does not.
* The live-host warning (`--target /`) does not fire for it either, which is
  exactly the run where it matters most.

The prompt also never names what it is about to destroy. For a tool whose
CLAUDE.md says "treat any code path that reaches `execute()` as capable of
wiping a disk", the confirmation should read like

```text
DESTRUCTIVE: /dev/nvme0n1 (476.9 GiB, 3 partitions: esp, root, home) will be
WIPED and repartitioned. Type the device name to continue:
```

**Fix.** Let a `Change` carry destructiveness explicitly rather than deriving it
from the op alone (the disk domain knows it is wiping), keep the op set as the
default, and enrich the prompt with device / size / current labels. Cheap, and
it closes the gap between what the safety section promises and what the code
does.

---

## P1 — Only the root partition gets LUKS kernel parameters

`KernelCmdlineAction._derive_from_disks()` skips every partition that does not
provide `/`:

```python
for part in disk.get("partitions", []):
    if not mounts_root(part):
        continue
```

Everything LUKS-related — `rd.luks.name`, `rd.luks.key`, `rd.luks.options`
(tpm2/fido2/verbatim) — lives inside that branch. A second encrypted device
(swap for hibernation, an encrypted `/home`, a data volume) therefore never gets
an unlock parameter, and `/etc/crypttab` alone does not help: dasik marks the
non-root entry `luks` without `x-initrd.attach`, so the initramfs does not open
it.

Worse, the obvious workaround is a trap. `_merge()` treats a parameter as a
single-valued key:

```python
key = p.split("=")[0] if "=" in p else p
```

`rd.luks.name` is a **repeatable** kernel parameter, so declaring the swap one
explicitly in `kernel_cmdline` silently discards the derived root one — the
config asks for more and gets less, and the machine stops booting. That is why
`config/vm-laptop-hibernate.json` and `config/laptop-p14s.json` both spell out
*both* tokens by hand:

```json
"kernel_cmdline": [
  "rd.luks.name=0ed69442-…=cryptroot",
  "rd.luks.name=7c678858-…=cryptswap",
  "resume=/dev/mapper/cryptswap"
]
```

**Fix.** Derive the LUKS parameters for **every** encrypted partition (the
deterministic `luks_uuid()` already makes this a pure function of the config),
and make `_merge()` compare whole tokens for the repeatable `rd.luks.*` family
instead of the key prefix. Add a property test: for any config, every encrypted
partition contributes exactly one `rd.luks.name`, and explicit tokens never
remove derived ones.

---

## P1 — `sync` cannot round-trip a hibernating machine

```python
# KernelCmdlineAction.import_state
return {self._DOMAIN: list(self.explicit_params)}
```

Only the parameters that were **already declared** come back. Capturing a real
machine from an empty seed — the workflow `docs/copy-your-config-and-test.md`
recommends — therefore drops `resume=`, `amd_pstate=`, `nvidia_drm.modeset=1`
and anything else that was set by hand or by another tool. The disk layout is
captured, the packages are captured, but the boot entry's own parameters are
not, and with P1 above the config cannot regenerate them either.

**Fix.** Read the live entry, subtract what the action derives from `disks`, and
keep the remainder as `kernel_cmdline`. The subtraction is what keeps the config
portable — the same reason the comment gives for not emitting resolved UUIDs.

---

## P2 — The "first failure hides the rest" pattern still exists in `UsersAction`

PR #169 fixed this for systemd units. The same shape remains here:

```python
for name in creates:
    …
    Command.execute("useradd", argv, target=target, check=True)
    Command.execute("usermod", ["-p", u["hashed_password"], name], …, check=True)
```

The sequencing *within* one user is deliberate and correct (a failed `useradd`
must stop before `usermod -p` sets a password on a user that does not exist),
but a failure on user A still hides whether user B would have worked. On an
install that means one broken user per run, and every run is a full apply.

**Fix.** The same shape as `SystemdAction._systemctl`: keep the per-user
sequence atomic, collect the per-user failures, raise once naming all of them.
`MicrosoftFontsAction`, `PackagesAction` and `BootloaderAction` were checked and
are either single-shot or already aggregate; `PackagesAction` in particular
already warns-and-skips unknown packages rather than aborting.

---

## P2 — `enable_trim` is a no-op on every encrypted install

```python
def expand_trim(config):
    if not config.get("enable_trim"):
        return {}
    return {"units": ["fstrim.timer"]}
```

That is the whole implementation, and `discard` appears nowhere else in the
package (`rg discard dasik/ --glob '*.py'` matches only preflight's list of
valid crypttab options). A LUKS mapping does not pass discards to the underlying
SSD unless it is opened with `discard` / `allow-discards`, so on the encrypted
configs — which is all of them — the timer runs and trims nothing, while the
config says TRIM is on.

**Fix.** Either wire it (append `discard` to the derived `rd.luks.options` and
to the crypttab entry when `enable_trim` is set) or say plainly in
`docs/config-reference.md` that `enable_trim` only schedules the timer and that
encrypted volumes need `luks_options: ["discard"]`, with the usual note that
discards leak which blocks are in use. Wiring it is better: the field reads like
a promise.

---

## P2 — A declared `files` entry can silently shadow a package's file

`DropFilesAction` already knows how to ask the question — discovery skips
pacman-owned files with `pacman -Qo`:

```python
res = Command.execute("pacman", ["-Qo", canonical], target=self._target())
```

but nothing checks the *declared* direction. Writing `/etc/pam.d/sudo` or
`/etc/pam.d/polkit-1` (as the new laptop config does, deliberately) overrides a
vendor file for good: pacman will drop a `.pacnew` beside it on upgrade and the
override never picks the change up. For PAM specifically, a stale override is
how a machine stops accepting logins.

**Fix.** A plan-time (or preflight) **warning**, not an error, listing declared
paths that a package owns, with the `.pacnew` consequence spelled out. Arch now
ships vendor PAM files under `/usr/lib/pam.d/`, so the same check should look
there too — those are not "owned" by anything under `/etc`, yet the override is
just as total.

---

## P3 — mkinitcpio has no hibernation support

PR #170 forced dracut's `resume` module. `MkinitcpioBackend._compute()` never
adds the equivalent hook, and it even uses `"resume"` as an *anchor* when
placing `btrfs`:

```python
insert_after = next((c for c in ("resume", "usr", "udev") if c in hooks), None)
```

so it expects the hook to possibly exist while never adding it. mkinitcpio is
dasik's **default** generator (`initramfs: "mkinitcpio"`), so the default path is
the unsupported one. It was left out of PR #170 on purpose — hook *order* is
load-bearing and unverified — but the asymmetry should not be permanent.

**Fix.** Add the hook with a VM run behind it (`qemu.sh hibernate` now exists;
point it at an mkinitcpio config), or state the limitation in
`docs/config-reference.md` next to `initramfs`.

---

## P3 — Stale docs and a stale TODO

* `CLAUDE.md:83` still documents `dasik config.json --dry-run` as "parsed but NOT
  implemented". The flag is gone: `rg 'dry.run' dasik/ --glob '*.py'` returns
  nothing. `plan` **is** the dry run — say that, and drop the promise of a future
  flag (`CLAUDE.md:218` repeats it).
* The repo-root `TODO` predates v3. Its one still-plausible item — "los paquetes
  deben tener la casuística de si están instalados asdeps o asexplicit" — is
  already implemented (`packages_action.py:616-618` runs `pacman -D --asdeps` /
  `--asexplicit`). The file now costs more than it carries.

---

## P3 — Password hashes travel in the config, and one config is public

`config/test-config.json` is tracked and pushed, hashes included
(`users[].hashed_password`, lines 25 and 34 — real yescrypt hashes of real
passwords). `.gitignore` even carries a comment saying logs must never be
committed for exactly this reason, but the config path has no such guard.

**Fix.** Rotate those passwords first — that is not a code change. Then consider
an indirection so the sensitive value need not sit in the JSON: a
`hashed_password_file` (or `$VAR`) resolved at apply time, mirroring how
`luks_keyfile` already exists next to `luks_password`. The `luks_password` field
has the same shape of problem and the same available answer.

---

## What is already good (so this is not read as a list of complaints)

* **Idempotency is genuinely tested**, not asserted: 1224 tests, 91% coverage,
  and Hypothesis property suites for the reconciler, set-math, config round-trip
  and per-action idempotency.
* **Failures are recorded, not hidden**: a part-way apply persists a `partial`
  generation, ownership of unreached domains carries forward, and `rollback`
  refuses to restore it.
* **The boot chain is defended by evidence**: deterministic LUKS UUIDs remove a
  plan/apply ordering hazard, the mkinitcpio neutralizers are written before the
  first transaction, and the VM harness now has layers for install, day-2,
  boot-unlock, lifecycle, sync-luks and hibernate.
* **Discovery is conservative**: `sync` skips pacman-owned files, locked LUKS and
  unrepresentable filesystems rather than emitting a config that lies.
* **The gates are real**: mypy clean, Bandit clean, a mutation tier on the
  set-math, and a pre-push hook that runs all of them.

## Suggested order

1. P0 disk-wipe gating — smallest change, largest downside avoided.
2. P1 multi-LUKS derivation + token-aware merge (unblocks hibernation and
   encrypted `/home` without hand-written cmdlines), then P1 cmdline capture in
   `sync` (they are the same feature seen from both directions).
3. P2 user-failure aggregation, TRIM, pacman-owned warning.
4. P3 mkinitcpio parity, doc/TODO cleanup, secret indirection.

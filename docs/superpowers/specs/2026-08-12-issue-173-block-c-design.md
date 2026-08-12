# Issue #173, block C — hardening: encrypted swap, AppArmor, PAM, firewall backends

**Date:** 2026-08-12
**Issue:** [#173](https://github.com/amt911/dasik/issues/173)
**Status:** approved, ready for implementation plans

Blocks A (#174) and B (#175) are merged. This is block C: the four remaining
concrete items from the issue's tail — encrypted swap, AppArmor, PAM hardening,
a firewall that is not only firewalld — plus a no-code audit of packages and
procedures that upstream has changed under us.

Four features, four specs' worth of work, **four PRs**, one VM pass at the end
that drives all of them through all six verbs. The audit ships as a document and
an issue comment, not as code.

---

## Ground truth established before designing

Verified on the host (an Arch machine), not assumed:

| Fact | Why it matters |
| --- | --- |
| `pam_faillock.so` is already in Arch's `/etc/pam.d/system-auth` | faillock needs **no** PAM-stack edit — only `/etc/security/faillock.conf` |
| `/etc/security/faillock.conf` is a `pam` **backup file**, and has no `.d` drop-in dir | dasik must own the whole file; pacman will never clobber it, it drops a `.pacnew` |
| `/etc/security/pwquality.conf.d/` **exists** (shipped by `libpwquality`) | pwquality needs no ownership of `pwquality.conf` — a drop-in suffices |
| `pam_pwquality.so` is **not** in Arch's stack | pwquality still requires editing `/etc/pam.d/passwd`, there is no way around it |
| `/etc/pam.d/passwd` is a `shadow` **backup file**, 4 lines, all `include system-auth` | owning it is pacman-safe and its blast radius is the `passwd` command, not login |
| `/etc/security/limits.d/` is a real drop-in dir | limits needs no ownership of `limits.conf` |
| Arch wiki `Dm-crypt/Swap_encryption` and the old `installer-1.sh:424` agree | the 1 MiB ext2 label partition + `offset=2048` is the established procedure |
| `laptop-p14s.json` already does LUKS swap + hibernation (PR #170) | the "swap that hibernates" half needs verification and docs, not new code |
| `FirewallAction.import_state` already rebuilds the live `public` zone | the "does it pick up existing rules" item is a verification, not an implementation |

---

## C1 — Encrypted swap

Two mutually exclusive modes, because the choice is a real trade-off and not a
default anyone can pick for the user:

* **random key** — a new key every boot. Maximum protection for swapped-out
  fragments, and **hibernation becomes impossible**: resume would have to read
  what the previous key wrote.
* **LUKS** — one persistent key, unlocked in the initramfs, **hibernation
  works**. Already implemented; this block verifies and documents it.

### Model — the partition field

A new per-partition field on `Partition` (`dasik/lib/models/disk_model.py`):

```jsonc
{
  "label": "swap",
  "filesystem": "swap",
  "swap_encryption": "random"        // "none" (default) | "random"
}
```

Named `swap_encryption`, not `encryption`: a field called `encryption` sitting
next to the existing `encrypt` boolean would read as the same knob spelled two
ways, and they are different mechanisms — `encrypt: true` is LUKS, this is plain
dm-crypt re-keyed every boot. A model validator refuses both at once, and
refuses `swap_encryption: "random"` on anything whose filesystem is not `swap`.

**Names are derived from the partition label**, so two random-key swaps on one
machine cannot collide: partition `label: "swap"` gives mapper `/dev/mapper/swap`
and ext2 label `cryptswap`; `label: "swap2"` gives `swap2` / `cryptswap2`.

Default `"none"`: the mode **reformats the partition**, and destructive steps in
this repo live behind an explicit opt-in.

### Action — `EncryptedSwapAction`

Owns three artifacts, and nothing else owns any of them:

1. **The label partition.** `mkfs.ext2 -L <label> <device> 1M`. The 1 MiB
   filesystem exists for one reason: to carry a persistent `LABEL`. `mkswap`
   re-runs every boot and would take a normal UUID with it, so the swap is
   addressed by the label of a tiny filesystem that sits *in front of* it.
2. **The crypttab line.**
   `swap LABEL=cryptswap /dev/urandom swap,offset=2048,cipher=aes-xts-plain64,size=512,sector-size=4096`
   `offset=2048` is 2048 × 512 B = the 1 MiB the ext2 occupies, so the swap
   never overwrites its own address. `cipher`, `size` and `sector_size` are
   configurable with those defaults.
   The key source is `/dev/urandom`, which is what both the wiki page and the
   old `installer-1.sh` use. On kernels ≥ 5.6 it is cryptographically identical
   to `/dev/random` once the pool is initialised, and unlike `/dev/random` it
   can never block — which at boot, before entropy has accumulated, is the
   difference between a swap that comes up and a machine that waits.
3. **The fstab line.** `/dev/mapper/<name> none swap defaults 0 0`, appended
   after `genfstab`. `genfstab` cannot see it: the mapper device does not exist
   during installation, only from the first boot.

**Idempotency.** `plan()` proposes a change when the crypttab line is missing or
differs, or when `blkid` does not report the label on the device. It never
re-runs `mkfs` on a device that already carries the label — that is the
destructive guard.

**Ownership of `/etc/crypttab`.** `DropFilesAction` owns it *unless* the
initramfs generator is dracut, in which case the dracut backend composes it
(`dasik/lib/actions/initramfs/dracut.py`). The swap line must therefore be a
*contribution* both composers include, exactly like the derived root entry —
not a fourth writer of the same file.

**Interaction with hibernation.** `preflight()` **aborts** when a config
declares a random-key swap and hibernation/`resume` at the same time; the
message says the two are incompatible by construction and names both
declarations. The "does the initramfs need the resume module" check
(`initramfs/base.py:55`) stops counting a random-key swap as a resume device.

**`sync`.** `import_state` reads the crypttab line plus `blkid`, emits
`encryption: "random"` on the matching partition, and **subtracts** the crypttab
and fstab lines so they never come back as verbatim `files` entries — the same
by-name subtraction `KernelCmdlineAction` does for block-owned parameters.

### Mode B — LUKS swap that hibernates

No new code. Deliverables: a VM run through `qemu.sh`'s `hibernate` layer that
suspends and resumes, and a wiki page stating plainly which mode to pick and
why they are exclusive.

---

## C2 — AppArmor

### Model — the `apparmor` block

```jsonc
"apparmor": {
  "enable": true,
  "audit": false,                     // auditd + kernel audit params
  "extra_profiles": [                 // verbatim, copied to /etc/apparmor.d/
    {"name": "usr.bin.foo", "content": "..."}
  ]
}
```

### Expansion (`expand_apparmor`)

* `enable` → package `apparmor`, unit `apparmor.service`.
* `audit` → package `audit`, unit `auditd.service`, `user_groups: ["audit"]`
  (the same mechanism the kvm toggle uses for `libvirt`), and
  `/etc/tmpfiles.d/audit.conf` containing `z /var/log/audit 750 root audit - -`.
  Without that override, Arch's own tmpfiles entry resets the log directory to
  `700` on every upgrade and the `audit` group can never read it.

### Kernel cmdline

Derived, not hand-written: `lsm=landlock,lockdown,yama,integrity,apparmor,bpf`
(AppArmor must be the first *major* module in the list). With `audit: true`,
additionally `audit=1 audit_backlog_limit=8192`. Explicit `kernel_cmdline`
entries still win, as they do for `cpu`/`sysrq`. `lsm`, `audit` and
`audit_backlog_limit` join `_BLOCK_OWNED_PARAMS` so `sync` subtracts them by
name and the block — not a loose parameter — is what gets captured.

### Profiles

`extra_profiles` become files under `/etc/apparmor.d/`. dasik does **not** call
`apparmor_parser` during installation: AppArmor is not running in the chroot.
Profiles load at the next boot, or on `systemctl reload apparmor` on a running
system. The wiki page says so.

### `sync`

`enable` is true when the package is installed, the unit is enabled and the
`lsm` parameter names apparmor. `audit` is true when `audit` is installed and
`auditd.service` enabled. Profiles under `/etc/apparmor.d/` that pacman does not
own are captured into `extra_profiles`, and `DropFilesAction` skips that
directory so the same file is never captured twice under two different names.

### Deliberately out of scope

Desktop notifications (`aa-notify`, `python-notify2`, an autostart `.desktop`
under `$HOME`). They need to write into a user's home, which is config-saver's
territory and still undefined in the issue. Recorded as a follow-up on #173.

---

## C3 — PAM hardening

Same shape as `SystemdConfAction`: **write a drop-in, read the effective
configuration.** Where no drop-in mechanism exists, own the file — and every
file involved is a pacman backup file, so ownership costs at most a `.pacnew`.

```jsonc
"pam": {
  "faillock":  {"deny": 5, "fail_interval": 900, "unlock_time": 600, "persistent": true},
  "limits":    {"nproc_soft": 100, "nproc_hard": 200},
  "pwquality": {"enable": true, "minlen": 10, "difok": 6, "retry": 2,
                "enforce_for_root": false,
                "dcredit": -1, "ucredit": -1, "lcredit": -1, "ocredit": -1}
}
```

Every sub-block is optional; an absent sub-block is not the empty one.

| Sub-block | Writes | Removal (owned but no longer declared) |
| --- | --- | --- |
| `faillock` | `/etc/security/faillock.conf`, whole file, `# Managed by dasik` header | header-only file ⇒ the compiled-in defaults return |
| `limits` | `/etc/security/limits.d/10-dasik.conf` | delete the drop-in |
| `pwquality` | `/etc/security/pwquality.conf.d/10-dasik.conf`, plus `/etc/pam.d/passwd` rewritten with `pam_pwquality` + `pam_unix use_authtok`, plus package `libpwquality` | restore `shadow`'s original four lines |

Defaults: `deny=5` (three attempts is easy to burn with a long passphrase on a
Spanish keyboard), `unlock_time=600`, `fail_interval=900`, and
`persistent=true` — `dir=/var/lib/faillock`, so an attacker who can reboot the
machine does not clear the lockouts. `limits` stays off unless declared: on a
single-user desktop an nproc cap buys little.

**Reading is effective, not literal.** faillock is parsed from the file dasik
owns; pwquality merges `/etc/security/pwquality.conf` and every
`pwquality.conf.d/*.conf` in lexicographic order, later winning — the order
libpwquality itself applies. Reading only dasik's own drop-in would make a value
set in the package file invisible, which is precisely the bug PR #177 fixed for
systemd.

**A drop-in that outranks dasik's** (`99-something.conf` setting a declared key)
is refused before anything is mutated, with the file and the key named — the
same guard `SystemdConfAction._refuse_if_outranked` implements. Otherwise apply
succeeds, the effective value stays foreign, and the same change is planned
forever.

**Blast radius.** faillock and limits touch no file under `/etc/pam.d`. Only
pwquality does, and the worst case there is a broken `passwd` command, never a
machine that cannot log in. `pam_faildelay` in `system-login` and
`/etc/security/access.conf` are deliberately excluded: faillock already stops
brute force, and both of those can lock a user out of their own console.

---

## C4 — Firewall backends (firewalld and ufw)

`firewall` gains `backend: "firewalld" | "ufw"`, defaulting to `firewalld` so
every existing config keeps its meaning.

`FirewallAction` splits into a thin base plus two backends:

* **firewalld** — today's behaviour verbatim: dasik writes the complete
  `/etc/firewalld/zones/public.xml`, `import_state` reads the live permanent
  zone through `firewall-offline-cmd`.
* **ufw** — package `ufw`, unit `ufw.service`. `allowed_services` map to an
  application profile from `/etc/ufw/applications.d` when one exists by that
  name, and to port/protocol otherwise. A new `rules` list carries verbatim ufw
  rules (`allow 22/tcp`, `limit ssh`). Planning uses `ufw --dry-run`, which
  prints the rules a command *would* add without touching anything —
  `/etc/ufw/user.rules` is generated state and writing it directly would fight
  the tool. `import_state` parses `ufw status verbose`.

`rich_rules` stay firewalld-only; declaring them under the ufw backend is a
validation error rather than a silent drop — an access rule that cannot be
represented must fail closed, the rule `_rich_rule_to_xml` already follows.

`preflight()` aborts when both backends are declared, and when the other
backend's package is installed with its unit enabled: two netfilter front-ends
running at once overwrite each other's rules.

The content you asked for — smb and syncthing — ships as a sample config, not as
a default. firewalld already knows the service names `samba`, `samba-client` and
`syncthing`, so it is `"allowed_services": ["samba", "syncthing"]`.

---

## C5 — Audit of packages and procedures

No code. A document under `docs/` plus a comment on #173, listing every case of
the sd-boot-hook pattern: something from the AUR that is now official, a package
renamed or dropped from the repos, a procedure systemd now covers natively.
Scope: the package lists in `dasik/lib/expand/toggles.py` and the sample configs
under `config/`, checked against today's repos with `pacman -Si` / `pacman -F`.

---

## Cross-cutting requirements

Applies to each of the four features before it counts as done:

* **TDD** for every model and for `plan()` / `import_state()`. `execute()` bodies
  that only shell out stay covered through mocked `Command.execute`.
* **Detectability matrix** in `tests/lib/test_feature_detectability.py`: absent
  on the target ⇒ a change is planned; present ⇒ silence; declared off but owned
  by the manifest ⇒ `REMOVE`; set by someone else and unowned ⇒ left alone.
* **Capture matrix** in `tests/lib/test_feature_sync_capture.py`: the machine
  has it ⇒ the block is captured; the machine lacks it ⇒ nothing is invented;
  the captured config validates and re-plans to nothing.
* **The block removed from the config**, which is the trap that has bitten this
  repo before: the reconciler hands an action its *empty* config when a previous
  generation owned the domain, and empty is not the same as "the empty value".
* **Round trips**: `sync → check → plan` silent, `plan → apply → plan` silent.
* **Docs**: a wiki page per feature under `docs/wiki/`, plus the new fields in
  `docs/config-reference.md`.
* **VM**, once, at the end: all four features in one config, driven through
  `check`, `plan`, `apply`, `sync`, `generations`, `rollback`. `apply` and
  `rollback` run only against the disposable VM; the verdict states which verbs
  ran for real and which were asserted against mocks.

## Order of work

C1 → C2 → C3 → C4 → C5. C1 first because it touches the disk model and
`/etc/crypttab`, the two things the other three must not collide with; C4 last
of the code because it is a refactor of a working action and benefits from the
VM harness being warm.

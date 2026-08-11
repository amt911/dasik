# Issue #173 — Block A (quick wins): sudo, sd-boot fallback, CPU scaling, sysrq, boot-update, reflector

Date: 2026-08-10
Issue: [#173](https://github.com/amt911/dasik/issues/173) — "revisar esto"
Status: approved design, ready for an implementation plan

## Why

Issue #173 lists ten independent asks. They differ wildly in size and several are
blocked on information the issue does not carry (which podman repo, what
`config-saver` is, what "profiles y environments" means). The list is therefore
decomposed into blocks, each with its own spec → plan → PR. This spec covers
**block A**, the small well-understood items:

| Issue item | Covered here |
| --- | --- |
| "modificar visudo para que andres y otros usuarios estén correctamente en el grupo wheel" | §1 |
| "que tenga sd boot un fallback, aunque sea igual" | §2 |
| "amd_pstate y cosas que faltan de la instalación de la torre" | §3, §4, §5, §6 |

Out of scope (later blocks): profiles/environments, podman, docker, docker
integration, private AUR packages, config-saver, the partitioning TUI. Also out
of scope even though the old scripts did them: plymouth/splash, the pendrive LUKS
keyfile (`enable_crypt_keyfile`), and per-user `$HOME` dotfiles (that is
config-saver territory).

## Current state (verified in the tree)

- `UserModel.groups` puts a user in `wheel` (`usermod -G`), but **nothing writes a
  sudoers fragment** — the only `/etc/sudoers.d/` writes are the temporary
  NOPASSWD fragments for the AUR build user (`aur_installer.py`,
  `pkgbuild_git_installer.py`, `packages_action.py`), removed on cleanup. Stock
  Arch ships `%wheel` commented, so the declared user cannot `sudo`.
  `config/mysystem.json` (`archlinux-torre-amd`, the tower) declares
  `andres` in `wheel` — it is exactly the machine that hits this.
- `BootloaderAction._install()` writes one entry, `arch.conf`. The old imperative
  installer always shipped `arch.conf` **and** `arch-fallback.conf`
  (`installer-1.sh:625`) and applied every cmdline option to both.
- `KernelCmdlineAction.apply()` already loops over **all** entries in
  `/boot/loader/entries` (`kernel_cmdline_action.py:279`), so a second entry
  inherits every parameter for free. But `_current_cmdline()` reads
  `entries[0]` — `os.listdir` order — which stops being deterministic once a
  second entry exists.
- `install_cpu_scaler()` (`after-install-2.sh:410`) installed
  `powerdevil power-profiles-daemon python-gobject`, enabled the ppd service, and
  **only on non-Intel** appended `amd_pstate=active` to both entries. Nothing in
  dasik does any of this; `config/mysystem.json` has no `kernel_cmdline` at all,
  so a fresh install from the tower's own captured config comes up without it.
- `BaseInstallAction._detect_microcode()` already reads the CPU vendor to pick
  `amd-ucode`/`intel-ucode` — the vendor probe to reuse.
- Expand toggles (`dasik/lib/expand/`) merge `packages`, `units`, `sockets`,
  `modprobe_conf`, `files` and `user_groups` into the config, so a toggle can
  contribute a file, not only packages. They cannot contribute kernel parameters.

## Design rule for this block

**A toggle in `dasik/lib/expand/toggles.py` when the feature is only packages,
units, files or kernel parameters. A new Action only when the feature owns state
on disk that must be read back.** Only sudoers owns such state, so this block
adds exactly one action.

## 1. `sudo` — new `SudoModel` + `SudoAction`

Config:

```json
"sudo": {
  "wheel": true,
  "nopasswd": false,
  "rules": ["andres ALL=(ALL) NOPASSWD: /usr/bin/pacman"]
}
```

Behaviour:

- Writes `/etc/sudoers.d/10-dasik`, mode `0440`, owner `root:root`. Content is
  deterministic: a generated header comment, then `%wheel ALL=(ALL:ALL) ALL`
  (or `%wheel ALL=(ALL) NOPASSWD: ALL` when `nopasswd` is true) when `wheel` is
  enabled, then each entry of `rules` in declaration order.
- **Sane default.** When the `sudo` block is absent *and* at least one declared
  user has `wheel` among its groups, the action behaves as `{"wheel": true}`.
  An explicit `{"wheel": false}` disables it. This is what keeps the reported bug
  from coming back by omission.
- **Safe write.** The fragment is written to `/etc/sudoers.d/10-dasik.tmp`,
  validated with `visudo -cf`, and only on success moved into place with
  `install -m 0440 -o root -g root`. The temporary name is deliberate: sudo's
  `#includedir` skips any file whose name contains a `.`, so even a leftover
  temporary is never parsed. A fragment that fails validation is deleted and the
  action raises — a broken fragment must never reach the live directory, because
  it breaks `sudo` for every user.
- `rules` are validated in the model: non-empty, single-line (no `\n`/`\r`), no
  leading `@include`/`#include`. Same posture as the existing package-name and
  config-identifier injection guards.
- v3 contract: `actual()` is the set of effective lines of the fragment on the
  target; `plan()` diffs desired vs managed vs actual through `compute_changes`;
  `import_state()` reads the fragment back so `sync` captures it (a machine whose
  `%wheel` is enabled through the stock `/etc/sudoers` instead reports
  `{"wheel": true}` as well, so a captured config reproduces working sudo).
- Registered in phase 4, **after `UsersAction`**: the referenced groups/users then
  exist and the `sudo` package (and `visudo`) is installed by `PackagesAction`.
- Preflight: an **explicit** `sudo` block while no package providing `sudo` is
  declared → error, before the first mutation. The **implicit** default (block
  absent, a user in `wheel`) only warns: an existing config that installs today
  must not start failing preflight because of a default it never asked for.

## 2. sd-boot fallback entry — `BootloaderAction`

- `_install()` additionally writes `/boot/loader/entries/arch-fallback.conf`:
  `title Arch Linux (fallback initramfs)`, the same `linux` line, the same
  microcode `initrd` lines from `_ucode_initrds()`, and
  `initrd /initramfs-linux-fallback.img` **when that image exists on the ESP**
  (mkinitcpio); otherwise the same initrd as the main entry (dracut, which builds
  no fallback image — this is the "aunque sea igual" case).
- The initrd filename comes from the same source `arch.conf` uses (the
  `InitramfsAction` naming), never a second hardcoded string: a mismatch between
  the entry's filename and the generated image is precisely the boot hang already
  fixed once in this repo.
- `loader.conf` keeps `default arch`, so the fallback is a rescue, not the
  default.
- Idempotency: the entry is part of the action's `actual()`/`verify()`, so a
  re-run does not rewrite it. An install that already has both entries plans
  empty.
- `KernelCmdlineAction._current_cmdline()` stops reading `entries[0]` and reads
  the entry named by `default` in `loader.conf`, falling back to `arch.conf` and
  then to a sorted first entry. Writes keep going to every entry, which is what
  gives the fallback the same parameters the old installer maintained by hand.

## 3. `cpu` — scaling driver, power-profiles-daemon, governor

Config:

```json
"cpu": {
  "scaling_driver": "auto",
  "mode": "active",
  "power_profiles_daemon": true,
  "governor": null
}
```

- `scaling_driver`: `auto` (default) | `amd_pstate` | `intel_pstate` |
  `acpi_cpufreq` | `none`. `auto` probes the CPU vendor with the same
  `/proc/cpuinfo` reader `BaseInstallAction._detect_microcode()` uses: AMD →
  `amd_pstate`, Intel → `intel_pstate`, anything else → no parameter.
- `mode`: `active` (default) | `guided` | `passive` for `amd_pstate`;
  `active` | `passive` | `disable` for `intel_pstate`. The model rejects a mode
  the selected driver does not accept. The parameter is emitted **explicitly on
  both vendors** — `intel_pstate=active` is the kernel default, but writing it
  keeps the resulting cmdline deterministic and reviewable.
- `power_profiles_daemon: true` (default when the block is present) → toggle adds
  the `power-profiles-daemon` package and the `power-profiles-daemon.service`
  unit. `powerdevil` stays in `packages`: it is the KDE front-end, not part of the
  CPU domain.
- `governor: "performance"` → toggle adds the `cpupower` package, a deterministic
  `/etc/default/cpupower` through the toggle's `files` channel, and the
  `cpupower.service` unit. `null` (default) leaves frequency policy to ppd.
- The kernel parameter is produced by a new
  `KernelCmdlineAction._derive_from_cpu()` on the **auto** channel, so an explicit
  `kernel_cmdline` entry for the same key still wins through the existing
  `_merge`.
- **`import_state` must subtract the cpu-derived tokens too.** Today it subtracts
  only what `disks` derives, and its docstring keeps `amd_pstate=` deliberately as
  a hand-set token; leaving that unchanged would make every `sync` copy the
  derived parameter into `kernel_cmdline` and duplicate the declaration. The
  subtraction becomes "everything dasik derives" (disks + cpu + sysrq).
- Preflight: `power_profiles_daemon` together with an explicit `governor` →
  warning (ppd owns the energy-performance preference); ppd together with a
  declared `tlp` package → error (they conflict).

## 4. `sysrq`

Root-level `"sysrq": true` → `sysrq_always_enabled=1` on the same auto channel
(and subtracted in `import_state` like the rest). This matches what
`enable_reisub()` did in the old installer. The `sysctl kernel.sysrq=1` route was
considered and rejected: the cmdline value applies from early boot, which is when
REISUB matters.

## 5. `systemd-boot-update.service`

Toggle: when `bootloader` is `sd-boot`/`systemd-boot`, enable the
`systemd-boot-update.service` unit that systemd itself ships. It replaces the AUR
`systemd-boot-pacman-hook` the old installer built (`after-install-2.sh:525`) and
adds no package.

## 6. `reflector`

Config:

```json
"reflector": {
  "countries": ["ES", "FR"],
  "protocols": ["https"],
  "latest": 20,
  "sort": "rate"
}
```

Toggle: the `reflector` package, the `reflector.timer` unit, and a deterministic
`/etc/xdg/reflector/reflector.conf` (one option per line, in a fixed order)
emitted through the toggle's `files` channel so `DropFilesAction` — already
content-comparing and idempotent — writes it. Only the timer is enabled; the
one-shot service is left to the timer.

## Testing (TDD, red first)

- **Models** — `sudo`, `cpu`, `reflector` accept valid blocks and reject invalid
  ones: a rule containing `\n`, an `@include` rule, `mode` incompatible with the
  chosen driver, an unknown `scaling_driver`, a negative `latest`.
- **`SudoAction`** — no fragment on target → change planned; identical fragment →
  empty plan (idempotency); different content → rewrite; `visudo -cf` failing →
  nothing written to `/etc/sudoers.d/10-dasik` and the action raises; block absent
  but a user in `wheel` → the implicit default plans the fragment;
  `{"wheel": false}` → plans nothing; `import_state` round-trips.
- **`BootloaderAction`** — fallback entry written with
  `initramfs-linux-fallback.img` when it exists and with the main initrd when it
  does not; both entries present → no rewrite; `loader.conf` still `default arch`.
- **`KernelCmdlineAction`** — `_derive_from_cpu` for AMD/Intel/auto/none;
  explicit `kernel_cmdline` beats the derived value; `import_state` does not
  re-emit a derived `amd_pstate`/`sysrq_always_enabled`; `_current_cmdline` reads
  the entry named by `loader.conf`'s `default`, not `listdir` order.
- **Toggles** — `cpu`, `reflector` and the sd-boot update toggle emit the right
  packages/units/files; each returns `{}` when its block is absent.
- **Preflight** — sudo without the `sudo` package; ppd + explicit governor
  (warning); ppd + `tlp` (error).
- `Command.execute` is mocked everywhere; no test touches a real disk, and
  `execute()`/`apply()` paths that shell out are asserted through call args.

## Verification before the PR

- `dasik plan config/install-megamix.json` and the tower's own
  `config/mysystem.json` parse and plan without exceptions; a second `plan` after
  an `apply` in the VM is a no-op.
- VM run (`scripts/vmtest`, `vm-dracut.json`): both loader entries present, the
  machine boots, `sudo -l` as the declared user works, `cat /proc/cmdline` shows
  the derived `amd_pstate`/`sysrq_always_enabled`, and `dasik sync` does not
  duplicate them into `kernel_cmdline`.
- Gates: `pytest --cov=dasik` (≥80%), `mypy dasik`, `bandit`, the set-math
  mutation run.
- `docs/config-reference.md` gains the `sudo`, `cpu`, `reflector` and `sysrq`
  fields; `config/install-megamix.json` exercises them.
- The agentic PR verification pass runs and its verdict is posted as a PR comment.

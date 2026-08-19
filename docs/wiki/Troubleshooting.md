# Troubleshooting

Symptom → cause → fix. Most of these are real failures that happened, were
diagnosed, and left a guard behind in the code.

---

## The CLI

### `Error: dasik config.json (no verb) is no longer supported`

The bare form was removed with the legacy handler. Use a verb:

```bash
dasik plan config.json      # preview
dasik apply config.json     # converge
```

### `arch-chroot not found`

Every command against a target that is not `/` runs inside `arch-chroot`, which
ships in `arch-install-scripts`. You are almost certainly managing the running
host and forgot the flag:

```bash
dasik plan my-system.json --target /
```

On the ISO, install it: `pacman -S arch-install-scripts`.

### `sudo: dasik: command not found`

`sudo` resolves the binary from **root's** `PATH`, so a virtualenv install
vanishes. Use the absolute path:

```bash
sudo /home/you/repos/dasik/.venv/bin/dasik sync my-system.json --target /
```

### `sync` says nothing was captured, or captures too little

`sync` needs root — it reads `/etc/shadow`, `cryptsetup luksDump` and firewalld's
zone files. Without it, whole domains silently skip (per-action isolation is by
design: one failing probe must not lose the whole capture).

### `sync` inlined a value instead of writing it to its file

Two kinds of value cannot live in a file and be read back unchanged, so dasik
writes them into the JSON rather than corrupt them: anything containing a
carriage return (reading a file translates newlines), and an `$include_line`
value that is empty, padded with whitespace, or spans more than one line. If
you see a body inline that used to be an `$include_text`, its captured content
grew a CR — convert the file to LF and re-run.

### A new package landed in the wrong fragment

`$concat` members are indistinguishable to a capture: dasik cannot know whether
`htop` is "base" or "dev", so new entries go to the **last** member. Moving the
line to another fragment is a normal edit and changes nothing else
([Config splitting](Config-splitting.md#sync-writes-back-through-the-split)).

### The ISO runs out of space mid-install

```text
No space left on device
```

The archiso cowspace is small. Grow it:

```bash
mount -o remount,size=75% /run/archiso/cowspace
```

---

## Planning

### A plan line never goes away

`plan → apply → plan` should end silent. If the same change comes back forever,
apply is writing somewhere that does not win. Real cases: a systemd drop-in
another file outranks; a value dasik writes into a packaged file that a later
drop-in overrides.

Check what the effective configuration actually is (`systemctl show`,
`systemd-analyze cat-config`), not just whether dasik's file exists. This is the
one bug class a green test suite never catches — see
[Workflows](Workflows.md#the-round-trips-that-matter).

### Everything shows up as `- remove`

```text
- [packages] remove 7zip  (no longer declared)
- [packages] remove alsa-plugins  (no longer declared)
…
```

You are planning **config A against a manifest written by config B**. dasik
owns what the previous apply installed; a different config declares almost none
of it, so the difference is a wall of removals. This is correct behaviour, and
the reason `plan` loads the same manifest `apply` does.

If it surprised you, you probably meant to point the plan at a different target
(`--target /mnt` vs `/`), or to start from a `sync` of the machine.

### A block I declared appears nowhere in the plan

Two possibilities, and they look identical:

1. it is already converged (fine);
2. the key is **misspelled**, so it is silently ignored — the models do not
   forbid unknown keys.

Check the spelling in [Configuration](Configuration.md). Then force the
question: remove the thing on the machine (or plan against a scratch root) and
see whether the plan grows a line.

### `Warning: /dev/sda is populated and does not match the declared layout`

dasik refuses to repartition a disk with data unless you say `wipe_disk: true`.
That is the guard, not a bug. On a real install, set the flag **after** checking
the device name three times (`lsblk`).

---

## Applying

### The apply failed part-way

```text
error: apply failed: …
The progress made so far was recorded as a partial generation
```

The machine **has** been mutated. That state is recorded as a *partial*
generation: it is progress, not convergence.

```bash
dasik generations --target /   # shows "partial — apply failed part-way"
```

Fix the cause, run `apply` again. Completed work is not redone. `rollback`
refuses to restore a partial generation.

### `systemctl enable <unit>` failed and killed the apply

The unit's package is not installed. Common after trimming a package list while
leaving the units behind. Preflight catches the display-manager case as an
error, and known units as a warning — [Validation](Validation.md).

Fix: declare the package, or drop the unit. A `sync` now captures the package
behind an enabled unit precisely so a capture cannot produce this.

### A package aborts the transaction: `target not found`

The name does not exist in any repo, group or the AUR. Default policy is
`warn-and-skip`: it is skipped with a warning and retried next time. If the
apply aborted instead, either `package_policy.unknown` is `"error"`, or the AUR
was **unreachable** — which is always blocking, deliberately: "we could not
look" must not be downgraded to "it does not exist".

For something that lives in neither, declare a
[Git PKGBUILD source](Packages.md#packages-from-a-git-pkgbuild).

### An AUR build fails

Builds run as an unprivileged user (makepkg refuses root) with a temporary
sudoers entry, both cleaned up afterwards. The full clone/build output is in the
run log — `dasik-apply-*.log`, or `-v` to watch it live.

A partial failure retries with the declared helper if there is one. A package
you expect to break sometimes should be marked `{"name": …, "optional": true}`
so it cannot abort the whole apply.

### `warning: tailscale.auth_key_file declares …, which does not exist`

Working as intended. The field holds the **path** of the auth key so the key
itself never enters the config, and a `file:` reference pointing at nothing stops
`tailscaled` from starting at all — so dasik writes the conffile *without*
`AuthKey` and converges the rest. The node stays logged out until you create the
file (`0600`, root) and apply again:
[provisioning it](Tailscale.md#provisioning-it).

On a fresh install the first apply always warns: `/mnt` does not exist until the
disk is partitioned, so there was nowhere to put the key beforehand.

### `tailscale up` answers `can't reconfigure tailscaled when using a config file`

Also intended. A declared [`tailscale`](Tailscale.md) block owns the
preferences, which is what makes them visible to `plan` and capturable by
`sync`; the daemon locks the CLI out of them for as long as the conffile is in
use. Change the config, not the CLI — or drop the block, and dasik takes both
files away.

---

## Booting

### The machine hangs at boot waiting for a device

**`/dev/mapper/cryptroot` never appears.** The initramfs has no way to open the
LUKS volume. With dracut this was the classic case: dasik runs dracut inside
`arch-chroot`, where hostonly detection cannot see the target's LUKS root, so
`systemd-cryptsetup` was silently omitted. dasik now **forces** `crypt`,
`systemd`, `systemd-cryptsetup` (+ `btrfs`). Verify:

```bash
lsinitrd /boot/initramfs-linux.img | grep -i cryptsetup
cat /boot/loader/entries/arch.conf     # rd.luks.name= present?
```

**`/dev/disk/by-label/root` never appears.** The boot entry fell back to a label
because no `rd.luks.name` was derived — the old bug with a subvolume-mounted
root (`mountpoint: null`, `/` on `@`). Fixed; if you see it, your entry predates
the fix. Re-apply, or check the entry has `root=/dev/mapper/<name>` and
`rootflags=subvol=@`.

### systemd-boot: `Error preparing initrd: Not found`

The entry lists a microcode image that is not on the ESP. Exactly one microcode
package is installed per CPU, so listing both `amd-ucode.img` and
`intel-ucode.img` breaks the entry. dasik now lists only the image that exists.
Re-apply to rewrite the entry.

### The machine reboots straight back into the installer

The ISO was booted in **legacy BIOS** mode. `bootctl install` prints "Not booted
with EFI", exits 0, and the install reports success — with no bootable entry the
firmware can start. Preflight refuses this now
([`no_efi_firmware`](Validation.md#no_efi_firmware)). Boot the ISO in UEFI mode
(QEMU: OVMF; virt-manager: *Customize before install → Firmware = UEFI*).

### `genfstab produced an empty fstab` / `/boot` is not mounted

Nothing was mounted, so nothing was captured. Two historical causes, both fixed,
both worth knowing:

- a **btrfs root with `mountpoint: null`** was skipped when mounting, so the
  subvolumes never got mounted;
- a config captured by `sync` (`"format": false` everywhere) applied to a fresh
  disk left `/boot` raw, so the mount failed.

Formatting inside a repartition is now unconditional — the flag is not a day-2
preservation switch ([Disks](Disks.md#safety-rules)).

### The splash never appears, or the passphrase prompt is invisible

The plymouth hook must run **before** the crypt hook, or it never takes over the
prompt — on an encrypted machine that means the disk cannot be unlocked at all.
dasik places it there. If you edited `HOOKS` by hand, check the order.

A theme change must also rebuild the image; dasik fingerprints the theme in its
drop-in so the plan is not silent.

### Hibernation does not resume

Three requirements, all of them yours to declare: a swap partition at least as
large as RAM, `resume=` on the kernel cmdline, and the `resume` hook/module in
the initramfs (derived from the swap partition). On an encrypted machine the
swap must be reachable after unlock.

An earlier version dropped the `resume` hook during the encrypted hook rewrite —
if you are on an old image, re-apply.

### `pacstrap` "succeeded" but the system is broken

`pacstrap` exits 0 even when a **hook** it ran failed; alpm only reports the hook
error in its output. dasik parses that output and reports hook failures loudly.
Check `dasik-apply-*.log` for the hook name.

---

## Getting a useful report

```bash
dasik plan my-config.json --target / -v --log /tmp/plan.log
```

Attach `/tmp/plan.log`, the config (**with secrets removed** — `luks_password`,
hashes, WireGuard keys) and `dasik --version`. If the failure is a boot problem,
add `cat /boot/loader/entries/*.conf` and the initramfs contents (`lsinitcpio -a`
or `lsinitrd`).

Run logs from `sync` contain secrets. Never attach one unredacted.

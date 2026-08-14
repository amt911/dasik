# Sync — capturing the machine

```bash
sudo dasik sync my-system.json --target /
```

The reverse arrow. `sync` reads the machine and writes what it finds into the
config file. It does **not** touch the system.

- reads the seed config and **schema-validates it** — sync rewrites this file,
  so starting from a config pydantic would reject would launder it into a new
  one;
- runs **no preflight** — its job is to report reality, incoherent reality
  included;
- captures **undeclared** domains too, so you can bootstrap from `{}`;
- writes `<config>.bak`, then the new config;
- prints `Config already matches system reality - nothing to sync.` when nothing
  changed.

Needs root: it reads `/etc/shadow`, `cryptsetup luksDump`, firewalld's permanent
zone files.

---

## The invariant

**`sync` → `check` → `plan` must end in silence.**

```bash
cp my-system.json /tmp/s.json
sudo dasik sync /tmp/s.json --target /
dasik check /tmp/s.json                 # the capture must still be a valid config
sudo dasik plan /tmp/s.json --target /  # …and describe the machine it came from
```

Anything else is a bug in a capture, not a quirk. A config the tool then refuses
is a broken capture; a capture that re-plans changes is a capture that lost
something.

---

## What each domain captures

| Domain | Captured from the machine | Notes |
| --- | --- | --- |
| `disks` | live layout via `lsblk`/`findmnt`/`cryptsetup`: devices, partitions, filesystems, mountpoints, LUKS (`encrypt`, `luks_name`, `luks_uuid`, fido2/tpm2 tokens, `luks_options`), btrfs subvolumes and their options | `wipe_disk`/`format` always **false**; NTFS, locked LUKS and unrepresentable partitions skipped; disks with no partitions omitted; [role labels](Disks.md#what-sync-captures) synthesized |
| `packages` | every explicit package (`pacman -Qqe`) including AUR, as plain names; a declared package installed as a dep re-emitted as `{name, reason: "dep"}`; the package **behind an enabled unit**, as `dep` | transitive deps never captured; `optional` preserved as intent; a declared `aur-` prefix dropped |
| `users` | real users (uid ≥ 1000) plus root's hash from `/etc/shadow`; shell and groups refreshed from reality | root with no password ⇒ the declaration is dropped, not invented |
| `systemd` | every enabled unit/socket, declared or not | declared intent kept, drift appended; `disable_units` preserved |
| `files` + the `/etc` sections | local files under `/etc/{udev/rules.d,modprobe.d,modules-load.d,sysctl.d,tmpfiles.d,sddm.conf.d,profile.d}`; `/etc/crypttab` when it has real lines; `/etc/wireguard/*.conf`; NetworkManager `*.nmconnection` of type wireguard (mode `0600`) | symlinks and **pacman-owned** files are skipped (`pacman -Qo`); declared entries win over discovered ones |
| `locales` | `/etc/locale.gen`, `/etc/locale.conf`, `/etc/vconsole.conf` | |
| `timezone` | the `/etc/localtime` symlink target | reports the machine, not the config |
| `network` | `/etc/hostname`, whether the default hosts block is present | `network.type` passed through verbatim (it has no file) |
| `pacman` | the four flags dasik knows, from `/etc/pacman.conf` | |
| `bootloader` | which loader is really installed, by on-disk marker (`loader.conf` vs `grub.cfg`) | independent of the seed's value |
| `enable_microcode` | `amd-ucode`/`intel-ucode` installed ⇒ `true` | the flag matters even though the package round-trips: it wires the initrd into the entry |
| `initramfs` | the **active** generator (dracut detected via its own conf and the neutralizer hooks) | also `bluetooth.in_initramfs` from `dracut.conf.d` |
| `kernel_cmdline` | the boot entry's parameters, **minus** the ones a block owns | see [below](#block-owned-parameters) |
| `firewall` | the permanent `public` zone via `firewall-offline-cmd`: `allowed_services`/`remove_services` as the diff against firewalld's defaults, `rich_rules` verbatim | nothing captured when the binary is unavailable |
| `snapper` | the configs under `/etc/snapper/configs` and their `SUBVOLUME=` | |
| `zram` | `/etc/systemd/zram-generator.conf` | semantic ini compare |
| `oomd`, `systemd_system_conf`, `systemd_user_conf` | the **effective** configuration of each file (packaged file + drop-ins) | |
| `cpu` | driver/mode from the cmdline, `power_profiles_daemon` and `governor` from the units and `/etc/default/cpupower` | capture-only domain |
| `reflector` | `/etc/xdg/reflector/reflector.conf` parsed back into the block | capture-only domain |
| `plymouth` | plymouth installed ⇒ the block, with the theme from `plymouthd.conf` | capture-only domain |
| `sudo` | the fragment dasik owns | |
| `microsoft_fonts` | whether the fonts are present | |

## Capture-only domains

`cpu`, `reflector` and `plymouth` have an intentionally **empty `plan()`**.
Their convergence is delivered by the [expand toggles](Features.md) (packages,
units, files) and the kernel cmdline; nothing owned the way *back*, so a synced
config lost the `reflector` block outright and spelled `cpu` as a hand-set
kernel parameter. They are registered purely so `sync` — which only visits
registered v3 actions — reaches them.

The general lesson: **a feature delivered purely by an expand toggle has no
owner on the way back until you give it one.**

## What a sync does NOT do: take ownership

A capture changes the **config**. It does not make dasik the owner of what it
saw.

The rule the whole model rests on is that removal is scoped to what dasik
itself **applied** — anything else is drift, reported and left alone. `sync`
used to break it: it recorded ownership of everything present, so on any
machine it would suddenly own `mkinitcpio`, `getty@.service`, `remote-fs.target`
— packages it never installed and units it never enabled. The bill arrived at
the next `rollback`, which offered to remove them, and died half-applied when
pacman refused to drop `mkinitcpio`.

So a sync keeps owning what it already owned, plus what the (expanded) config
declares and the machine confirms. Ownership still follows reality downwards —
an owned item that vanished stops being owned. And the observation is not lost:
it lands in the captured config, and **applying that config is what makes it
owned**, by having applied it.

## Block-owned parameters

`amd_pstate=`, `intel_pstate=`, `sysrq_always_enabled=` and (when plymouth is
installed) `splash` are subtracted **by name** from the captured
`kernel_cmdline`, whether or not the config declares the block. Otherwise the
capture describes the same policy without ever growing the block that explains
it.

Everything else on the entry is kept — `resume=`, `quiet`, an unlock for a
device this config does not describe. Dropping it is how hibernation used to
vanish from a synced machine.

---

## Two legitimate reasons a key is missing from a capture

Do not read either as data loss:

1. **The seed's own toggles re-derive it.** `subtract_contributions` strips
   whatever your blocks already imply, so `systemd-boot-update.service` never
   appears in `systemd.enable_units` — `bootloader: sd-boot` derives it.
2. **A newly-added empty value is dropped**, so a bootstrap does not rewrite the
   file just to add `"packages": []`.

Assert **reproducibility**, not literal presence: expand the captured config and
check it plans to nothing.

---

## Refusals and limits

| Situation | Behaviour |
| --- | --- |
| the config uses `$include`/`$include_text`/`$concat` | **written back through the split**: each value returns to the file it came from, a directive whose value did not change is left alone, and a new `$concat` entry goes to the last member ([Config splitting](Config-splitting.md#sync-writes-back-through-the-split)) |
| a captured value a file cannot hold (a CR, or a padded/empty/multi-line `$include_line`) | written **inline** instead — the file would not read back as the same string, and an ugly value beats a wrong one |
| a domain's probe fails (a binary missing, a locked volume) | that domain is skipped, the rest still captured — per-action isolation |
| `/etc/libvirt/hooks` (GPU passthrough) | **not** captured: a nested tree of executable scripts the flat file model cannot round-trip |
| secrets | captured verbatim when they live in a file dasik reads (WireGuard, NetworkManager). Treat both the config and the sync log as sensitive |
| a `sync` log | records what was read back — do not commit `dasik-sync-*.log` |

---

## Turning a capture into an installable config

A captured `disks` block describes *this* machine and is deliberately inert
(`wipe_disk: false`, `format: false`, real UUIDs). Making it install a new
machine is a deliberate edit — device → target device, `wipe_disk: true`, drop
the data disks, `rest` for the last partition, drop `luks_uuid`, swap FIDO2 for
a passphrase, keep exactly one ESP. Step by step:
[Recipes](Recipes.md#making-a-captured-disks-block-generic).

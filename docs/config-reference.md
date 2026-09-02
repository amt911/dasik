# dasik config reference — every option

The full set of fields dasik accepts in a config JSON, generated from the pydantic
models in [`dasik/lib/models/`](../dasik/lib/models/) (the source of truth — if
this doc and the models ever disagree, the models win).

**Everything is optional.** A config is a set of independent sections; include only
what you want dasik to manage. `dasik plan <config>` shows what a section would
change, `dasik apply` converges it, `dasik sync` captures many of them back from a
running system. Unknown top-level keys are ignored; unknown keys *inside* a modeled
section are rejected by validation.

Legend: **type** as in the model, **default** (`—` = required when the section is
present), and whether `sync` captures it.

---

## Splitting a config across files

A config that manages a real machine is mostly two things: a long package list and
a handful of verbatim file bodies escaped into single JSON lines. Both read badly
inline, so any value may be replaced by one of three directives, resolved **before**
validation:

| Directive | Becomes |
| --- | --- |
| `{"$include": "path.json"}` | the parsed JSON of that file (object, list, string…) |
| `{"$include_text": "path.conf"}` | that file's contents, as a string (verbatim, trailing newline included) |
| `{"$include_line": "secrets/hash"}` | that file's first line, stripped — for secrets |
| `{"$concat": [ ... ]}` | the lists inside it, flattened into one |

Two directories do the same job for whole trees of files: `etc_tree` mirrors
`/etc`, `home_tree` mirrors users' homes (`<tree>/<user>/<path>`). Every file
under them becomes a `files` / `home_files` entry, and `sync` extracts back into
them.

```json
{
  "hostname": "archlinux-p14s",
  "packages": {"$concat": [
    {"$include": "packages-base.json"},
    {"$include": "packages-desktop.json"}
  ]},
  "disks": {"$include": "disks.json"},
  "files": [
    {"path": "/etc/pam.d/sudo", "content": {"$include_text": "parts/pam-sudo"}}
  ]
}
```

A working example lives in [`config/split-example/`](../config/split-example/) —
`dasik check config/split-example/main.json` assembles and validates it.

Rules, each of them there so a config cannot quietly load something its reader
does not see:

* Paths are **relative to the file that names them**, so a directory of fragments
  moves as a unit. Absolute paths and `..` are refused.
* A directive must be the **only key** in its object.
* Include cycles are reported, but including the same fragment from two places is
  fine.
* `$include_text` never parses: the file arrives verbatim, newlines and all.

**Secrets.** Use `$include_line`, not `$include_text`: the latter is verbatim by
design (a PAM file needs its final newline), and `usermod -p '$y$…\n'` sets a hash
nobody can log in with while nothing complains.

```json
"hashed_password": {"$include_line": "secrets/hashed-password"},
"luks_password":   {"$include_line": "secrets/luks-passphrase"}
```

Referencing one secret file from two places is the point: both encrypted
partitions read the same passphrase, so root and swap cannot drift apart and
systemd's initrd password cache asks once. `.gitignore` keeps
`config/*/secrets/*` out of the repo and tracks only the `.example` next to it.

Two ready-made splits live in the repo:
[`config/test-config-split/`](../config/test-config-split/) (the tracked sample,
427 lines → a 97-line `main.json` plus 18 fragments) and
[`config/laptop-p14s-split/`](../config/laptop-p14s-split/) (the ThinkPad config,
with its hash and passphrase in `secrets/`). `tests/lib/test_split_configs.py`
asserts each one assembles to exactly its single-file counterpart, so the two
forms cannot drift.

**`sync` writes back through the split**: each value returns to the file it came
from, and a directive whose value did not change is left alone — its file is not
even opened. With `etc_tree`/`home_tree` declared, captured file bodies are
written into those directories instead of inlined
([Config splitting](wiki/Config-splitting.md#sync-writes-back-through-the-split)).

---

## Top-level fields at a glance

| Field | Type | What it manages |
| --- | --- | --- |
| `disks` | object | Partitioning, filesystems, LUKS, btrfs subvolumes |
| `timezone` | object | `/etc/localtime` |
| `locales` | object | `/etc/locale.gen`, `locale.conf`, `vconsole` keymap |
| `network` | object | The network manager: package, unit, DHCP profile — plus `/etc/hosts` |
| `hostname` | string | `/etc/hostname` |
| `users` | list | User accounts + groups + shell |
| `packages` | list | pacman + AUR + Git-source packages (real names) |
| `package_policy` | object | Unknown packages (`unknown`: `warn-and-skip` \| `error`) and install/build failures (`build_failure`: `abort` \| `warn-and-continue`) |
| `luks_token_policy` | object | What an apply does when a TPM2/FIDO2 enrolment fails with no terminal to ask on (`enroll_failure`: `abort` \| `warn-and-continue`) |
| `package_sources` | object | Git PKGBUILD source per package (outside repo/AUR) |
| `drivers` | list | GPU driver selection |
| `bootloader` | string | GRUB or systemd-boot |
| `initramfs` | string | mkinitcpio or dracut |
| `kernel_cmdline` | list | Extra kernel parameters |
| `systemd` | object | Enable/disable units + sockets |
| `pacman` | object | `/etc/pacman.conf` options + multilib |
| `udev_rules`, `modprobe_conf`, `modules_load`, `sysctl_d`, `tmpfiles_d`, `sddm_conf_d`, `profile_d` | list | Local `/etc/*.d` snippet files |
| `etc_environment` | list | `/etc/environment` lines |
| `files` | list | Arbitrary `/etc/...` files (verbatim). Anything under `/etc/systemd/` triggers a `systemctl daemon-reload` on a live target — see below |
| `etc_tree` | string | Directory mirroring `/etc`; every file under it becomes a `files` entry ([Config splitting](wiki/Config-splitting.md)) |
| `etc_tree_modes` | object | Tree-relative path → octal mode, for the modes Git cannot carry (`0600` on a keyfile) |
| `home_tree` | string | Directory mirroring users' homes (`<tree>/<user>/<path>`); every file becomes a `home_files` entry |
| `home_tree_modes` | object | Tree-relative path (`<user>/<path>`) → octal mode |
| `home_files` | list | Files inside a user's `$HOME` (dotfiles, autostart entries) |
| `zram` | object | `/etc/systemd/zram-generator.conf` |
| `sudo` | object | `/etc/sudoers.d/10-dasik` — wheel access + extra rules |
| `cpu` | object | CPU scaling driver, power-profiles-daemon, cpupower governor |
| `reflector` | object | `/etc/xdg/reflector/reflector.conf` + `reflector.timer` |
| `plymouth` | object | Boot splash: package, theme, initramfs hook/module, `splash` |
| `apparmor` | object | Mandatory access control: package, unit, the `lsm=` kernel parameter, optional audit framework, local profiles |
| `pam` | object | PAM hardening: account lockout, nproc limits, password policy |
| `firewall` | object | firewalld (one or more zones) **or** ufw — see below |
| `tailscale` | object | Tailscale preferences, as `/etc/tailscale/tailscaled.conf` — see below |
| `containers` | object | Container runtime: podman or docker (the engine, not the containers) |
| `config_saver` | object | config-saver: the package, its backup documents, its timers, and restoring an archive into `$HOME` |
| `bluetooth`, `hardware_acceleration`, `kvm`, `cups`, `microsoft_fonts`, `snapper` | object | Feature toggles |
| `wireguard` | list | Tunnels, each naming a file next to the config; a wg-quick `.conf` can be served by NetworkManager (see below) |
| `enable_trim`, `enable_microcode`, `remove_home_on_delete`, `sysrq` | bool | Simple toggles |
| `metadata`, `notes` | object / string | Free-form; not applied |

---

## `apparmor`

Mandatory access control. Declaring the block is the declaration — `enable`
defaults to `true`. See [the wiki page](wiki/AppArmor.md) for the full story.

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `enable` | bool | `true` | Installs `apparmor`, enables `apparmor.service`, and derives `lsm=landlock,lockdown,yama,integrity,apparmor,bpf`. **The parameter is what turns AppArmor on** — the package alone leaves every profile inert. |
| `audit` | bool | `false` | Also installs `audit` + `auditd.service`, derives `audit=1 audit_backlog_limit=8192`, adds every declared user to `adm` and writes `/etc/tmpfiles.d/audit.conf` so `/var/log/audit` stays readable across upgrades. |
| `desktop_notifications` | bool | `false` | Runs `aa-notify` on login: adds `python-notify2`, `python-psutil`, `tk` and an autostart entry in every declared (non-root) user's `$HOME`. Needs `audit: true` — the notifier reads `/var/log/audit/audit.log`, and the schema refuses the pair without it. |
| `extra_profiles` | list | `[]` | `{name, content}` copied verbatim to `/etc/apparmor.d/<name>`. `name` is a file name, not a path. They load at the next boot — dasik does not run `apparmor_parser` in the chroot. |

`sync` captures `enable: false` for a machine that has the package but no `lsm=`
naming it: that machine is not protected, and reporting otherwise would describe
a system that does not exist. Profiles pacman owns are never captured.

---

## `containers`  *(sync ✓)*

The container **runtime**, installed and configured. dasik does not manage
containers.

```json
"containers": { "runtime": "podman", "rootless": true, "docker_compat": true }
```

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `runtime` | `"podman"` \| `"docker"` | — | Exactly one: both own `/usr/bin/docker` and the same bridge networks. |
| `rootless` | bool | `true` (podman) / `false` (docker) | podman: every declared non-root user gets a `subuid`/`subgid` range, which is what rootless containers map into. Refused for docker — rootless docker is a separate daemon setup, not a flag. |
| `docker_compat` | bool | `false` | podman: installs `podman-docker`, so `docker` on the command line is podman. Refused for docker. |
| `compose` | bool | `false` | `podman-compose` / `docker-compose`, following the runtime. |
| `api_socket` | bool | `false` | Enables `podman.socket` / `docker.socket`. For docker this **replaces** `docker.service`: the engine starts on first use instead of at boot. |
| `daemon_json` | object | `null` | docker only: `/etc/docker/daemon.json`, written as JSON. Refused for podman, which has no daemon. |

docker also puts every declared user in the `docker` group — the only way to use
docker without root, and worth knowing that it is **root-equivalent**: a member
can bind-mount `/` into a container.

`useradd` already writes a subuid range for users it creates (shadow ≥ 4.11.1-3),
so on a fresh install this domain usually converges to nothing. It exists for the
machines where it does not: an older account, a user restored from a capture, a
machine that grew podman later.

---

## `config_saver`  *(sync ✓ — configs and timers from the machine, the rest as intent)*

[config-saver](https://github.com/amt911/config-saver) backs up the parts of
`$HOME` a config file cannot carry (themes, browser profiles, whole
directories). dasik declares the policy — and, on a fresh machine, unpacks the
archive the old one produced.

```json
"config_saver": {
  "source": { "url": "https://github.com/amt911/config-saver-aur.git",
              "ref": "7bca56d2e5a016d8e07541b6b5f7708190339d26" },
  "configs": { "dotfiles": { "normalize_content": true,
                             "directories": [{ "source": "$HOME",
                                               "files": [".zshrc"] }] } },
  "timer_users": ["andres"],
  "restore": [{ "user": "andres", "archive": "/run/media/usb/dotfiles.tar.gz" }]
}
```

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `source` | object | `null` | `{url, ref, subdir}` of the **Git PKGBUILD** that builds it. config-saver is not in the AUR, so without this (or a `package_sources` entry of your own) the name resolves nowhere and `warn-and-skip` drops it silently. |
| `configs` | object | `{}` | name → config-saver document, written to `/etc/config-saver/configs/<name>.json`. It reads JSON as well as YAML, and JSON needs no new dependency and round-trips exactly. |
| `timer_users` | list | `[]` | Enables `config-saver@<user>.timer` for each. |
| `restore` | list | `[]` | `{user, archive}` — an absolute path **on the target**. Unpacked into that user's `$HOME` with `config-saver --decompress`. |

**Where a configuration can live (config-saver ≥ 3.3.0).** Three levels; the
two active ones are merged by file name and the more specific wins:

| Level | Owner | How it reaches a fresh machine |
| --- | --- | --- |
| `/usr/share/config-saver/configs` | the package | examples only — **never active**; use them with `--input <path>` |
| `/etc/config-saver/configs` | **dasik**, from `configs` above | written by the first `apply` |
| `~/.config/config-saver/configs.d` | the user | rides inside the `$HOME` archive itself, so a restore brings it back |

With nothing in either active level config-saver **exits 6** ("No
configurations found") rather than falling back to the examples — those reach
`~/.ssh` and `~/.config/rclone`, and a package install should not start a daily
timer that archives credentials nobody chose. `preflight` warns when
`timer_users` is declared and `configs` is empty, because that is exactly the
combination that produces a timer failing on every fire.

**Making the archive self-sufficient.** A restore brings back the *data*; it
brings back *what to back up* only if some configuration archived the directory
the configurations live in. config-saver ships that one-liner as
`own-configs.yaml`, but it is an example, so on a dasik machine you declare it:

```json
"configs": {
  "own-configs": { "normalize_content": false,
                   "directories": ["$CONFIG_DIR/config-saver/configs.d"] }
}
```

That covers the *user's* level. `/etc/config-saver/configs` is deliberately not
in it: dasik rebuilds that level from this config file, and an archive that
restored it would leave the machine diverging from its own declaration.
config-saver keeps it behind explicit `--include-system-configs` /
`--restore-system-configs` for people not using a declarative installer.

**Restore is once per archive content.** The marker under
`~/.local/state/dasik/config-saver/<sha256>` names what was unpacked, so
re-applying restores nothing and replacing the file with a newer capture
restores again. An archive that is not there is planned anyway and `apply` says
which path it could not find — silence would be indistinguishable from "already
restored".

**Un-declaring a restore removes nothing.** Unpacking cannot be undone; the
files belong to the user now. The domain plans no removal at all.

**It is not limited to `$HOME`.** A document takes absolute paths, so system
configuration works the same way:

```json
"configs": {
  "etc-ssh": { "normalize_content": true,
               "directories": [{ "source": "/etc/ssh",
                                 "files": ["sshd_config", "ssh_config"] }] }
},
"timer_users": ["root"]
```

with three conditions that decide whether it works:

- **`timer_users` needs `root`** for anything under `/etc` — a user timer cannot
  read it, and the archive comes out short without saying so.
- **Name the files, never the whole `/etc/ssh`**: that directory also holds
  `ssh_host_*_key`, the host's private keys.
- **`ref` is the full 40-character sha**; a short one is rejected by the model.

And keep the two ideas apart: **`files` applies, `config_saver` saves.** A
setting you want *identical on every machine* belongs in
[`files`](#files) — dasik writes it, sees the drift and repairs it. A setting
this machine *grew*, which you want back on the next one, belongs here. For
`/etc/ssh` both usually apply: the hardening snippet declared, the rest backed
up. The worked example is in the wiki's Recipes page.

`sync` reads the documents and the enabled timers off the machine. `source` and
`restore` come back from the config: a marker names a content hash and a built
package names no repository, so neither can be reconstructed from the target —
they are intent, like a package's `optional` flag.

---

## `pam`

Three independent, optional sub-blocks. See [the wiki page](wiki/PAM.md).

### `pam.faillock` → `/etc/security/faillock.conf`

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `deny` | int | `5` | Failed attempts before lockout. `0` is rejected — pam_faillock reads it as "disable the lockout". |
| `fail_interval` | int | `900` | Seconds within which the failures must fall. |
| `unlock_time` | int | `600` | Seconds the account stays locked. |
| `persistent` | bool | `true` | Writes `dir = /var/lib/faillock`, so a reboot does not clear the lockout. |

`pam_faillock` is already in Arch's `system-auth`, so this touches nothing under `/etc/pam.d`.

### `pam.limits` → `/etc/security/limits.d/10-dasik.conf`

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `nproc_soft` | int | `100` | Soft per-user process limit (raisable with `prlimit`). |
| `nproc_hard` | int | `200` | Hard limit — the ceiling a fork bomb hits. |

### `pam.pwquality` → `/etc/security/pwquality.conf.d/10-dasik.conf` + `/etc/pam.d/passwd`

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `enable` | bool | `true` | Adds `libpwquality` and puts `pam_pwquality.so` in the `passwd` stack. |
| `minlen` | int | `10` | Minimum length; below 6 is rejected. |
| `difok` | int | `6` | Characters that must differ from the old password. |
| `retry` | int | `2` | Prompts before `passwd` gives up. |
| `enforce_for_root` | bool | `false` | Apply to root too. |
| `dcredit`, `ucredit`, `lcredit`, `ocredit` | int | `-1` | pwquality's convention: **negative = require** at least that many of the class. |

The only PAM stack file dasik writes is `/etc/pam.d/passwd`, so a mistake breaks the `passwd`
command, never login. `sync` captures pwquality only when the module is actually in that stack.

---

## `firewall`

One block, two backends — never both installed at once (preflight refuses it).
See [the wiki page](wiki/Firewall.md).

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `enable` | bool | `false` | Installs and enables the chosen backend. |
| `backend` | `firewalld` \| `ufw` | `firewalld` | Which tool applies the rules. |
| `allowed_services` | list | `[]` | firewalld: a service it knows (`samba`, `syncthing`). ufw: an application profile from `/etc/ufw/applications.d`. |
| `remove_services` | list | `[]` | **firewalld only.** Services to strip from the `public` zone defaults (`ssh`, `dhcpv6-client`). ufw denies incoming by default, so it is a validation error there. |
| `rich_rules` | list | `[]` | **firewalld only**, `firewall-cmd --add-rich-rule` syntax. A rule that cannot be represented losslessly is rejected, never approximated. |
| `rules` | list | `[]` | **ufw only.** `<action> <target>` — `allow 22/tcp`, `limit 22/tcp`, `allow 6000:6007/udp`, `allow Syncthing`. `allow ssh` is rejected: ufw reports it as `22/tcp`, so dasik could never tell applied from missing. |

firewalld converges as a file (`/etc/firewalld/zones/public.xml`, owned whole);
ufw converges through its own CLI with `ufw status` as the read side.

---

## Files systemd reads

A file written under `/etc/systemd/` — through `files` or `etc_tree` — is not
configuration until systemd re-reads it. On a **live** target (`--target /`)
dasik therefore runs `systemctl daemon-reload` after any apply that wrote or
removed one. Install targets are skipped: there is no systemd running under
`/mnt`, and its first boot reads every unit and drop-in there is.

`systemctl restart` does **not** reload unit files, so restarting the unit
yourself was never a workaround — which is why this was a real bug (issue #300)
rather than a documentation gap.

A reload is not always the whole story, and dasik says so rather than acting:

| What changed | What a reload does not do |
| --- | --- |
| `…/<unit>.d/*.conf` | a **running** unit keeps its old configuration until `systemctl restart <unit>` |
| `logind.conf.d/`, `resolved.conf.d/` | those daemons re-read on restart, not on reload |
| `system.conf.d/` | needs `systemctl daemon-reexec` |
| `network/` | needs `networkctl reload` |
| `sleep.conf.d/` | nothing — `systemd-sleep` reads it each time it runs |

dasik never restarts a daemon for you. Restarting `systemd-logind` can end the
graphical session, and reloading the network stack can drop the connection the
apply is travelling over. It reloads, then names what is left to do.

## `tailscale`  *(sync ✓)*

Preferences written to `/etc/tailscale/tailscaled.conf`, the file
`tailscaled --config` reads. Declaring the block also pulls the `tailscale`
package, enables `tailscaled.service`, and writes `/etc/default/tailscaled` —
the EnvironmentFile the vendor unit reads, and what actually points the daemon at
the conffile. The conffile alone changes nothing.

A `tailscaled.service.d` drop-in setting `FLAGS` does **not** work: measured in a
guest, the EnvironmentFile wins, with or without `systemctl daemon-reload`.
pacman lists `/etc/default/tailscaled` under `Backup Files`, so owning it is the
vendor-sanctioned route and an upgrade writes a `.pacnew` rather than clobbering
it. dasik writes `PORT` there as well, because the unit interpolates `${PORT}`
and an empty one is not a working command line — hence the `port` field.

**Logging in**: conffile mode has no interactive login — `tailscale up` and
`tailscale login` both answer `can't reconfigure tailscaled when using a config
file`. Declare `auth_key_file` with the absolute path (on the target) of a
root-owned 0600 file holding a tailnet auth key; it renders as the conffile's
`AuthKey` `file:` reference, so only the PATH ever reaches the Git config.
While the file does not exist yet, dasik omits the entry with a warning — a
dangling `file:` reference stops tailscaled from starting (measured in the
guest oracle) — and the next apply after you create the file wires it in. Once
the node is logged in you may delete the key file and revoke the key: the node
identity lives in tailscaled's state, not in the auth key. `sync` captures the
path, and never a literal key someone put in the conffile by hand.

```json
"tailscale": {
  "auth_key_file": "/etc/tailscale/authkey",
  "accept_routes": true,
  "accept_dns": true,
  "ssh": false,
  "web_client": false,
  "shields_up": false,
  "exit_node": "auto:any",
  "exit_node_allow_lan_access": true,
  "advertise_routes": ["192.168.1.0/24"],
  "advertise_exit_node": false,
  "hostname": "archbox",
  "operator": "andres",
  "netfilter_mode": "on",
  "port": 41641,
  "posture_checking": false,
  "server_url": "https://controlplane.tailscale.com"
}
```

> ⚠️ **Declaring the block takes the keys away from the CLI.** While the conffile
> is in use, `tailscale set --accept-routes` answers `can't reconfigure
> tailscaled when using a config file; config file is locked`. That is the point
> — it is what makes the domain visible to `plan` and capturable by `sync` — but
> it does mean the CLI is no longer the place to change these. Remove the block
> and dasik takes both files away, and the CLI works again.

**Unset is not the same as declared-false.** A field left out leaves the
preference to tailscale; a field set to `false` is dasik's, and locked. So
declare what you mean to own, not everything.

**There is no `auth_key`.** The conffile accepts one, but it is a tailnet
credential and this config is meant for Git — `dasik save` commits it. Logging in
stays a manual `tailscale up`: the node key in
`/var/lib/tailscale/tailscaled.state` is that machine's identity in the tailnet,
so it is not portable between machines even in principle.

`advertise_routes` entries need an explicit prefix length: `"10.0.0.0/8"`, never
`"10.0.0.0"`. tailscaled parses them with `netip.ParsePrefix`, which requires it,
and refuses to start on a file it cannot parse.

The underlying schema is `alpha0` and undocumented; every key dasik emits was
verified against the binary (`scripts/vmtest/guest-tsspike.sh`). Three of the
obvious guesses are wrong — the conffile says `RunSSHServer`, not `SSH`, and
`AllowLANWhileUsingExitNode`, not the `ExitNodeAllowLANAccess` that
`tailscale debug prefs` reports — which is why the map is explicit rather than
mechanical case conversion.

## `disks`

`{"disks": {"disks": [ <DiskLayout>, ... ]}}` — a list of disks. **Destructive** when
`wipe_disk`/`format` are on. See [copy-your-config-and-test.md](copy-your-config-and-test.md)
for making a captured layout portable.

### DiskLayout

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `device` | string | — | Target disk, e.g. `/dev/sda`, `/dev/nvme0n1`, `/dev/vda`. No auto-detect. |
| `partition_table` | `gpt` \| `msdos` | `gpt` | |
| `wipe_disk` | bool | `false` | **DESTRUCTIVE** — wipe the whole disk before partitioning. |
| `partitions` | list | — | ≥1 `Partition` (below). |

### Partition

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `label` | string | — | `[A-Za-z0-9_.-]{1,36}`, unique per disk. dasik keys off this, not the partition number. |
| `size` | string | — | `512MiB` / `1GiB` / `100MB` / `50%` / `rest` (fills remainder; only one, must be last). |
| `filesystem` | `ext4` \| `btrfs` \| `fat32` \| `swap` \| `xfs` | — | |
| `partition_type` | `esp` \| `linux` \| `linux-swap` \| `lvm` | `linux` | GPT type. |
| `mountpoint` | string | `null` | e.g. `/`, `/boot`. For a btrfs-with-subvolumes root, leave unset and put mounts on the subvolumes. |
| `format` | bool | `true` | `sync` sets `false` (never reformat on re-apply). A freshly-created partition is formatted regardless. |
| `swap_encryption` | `none` \| `random` | `none` | Swap only. `random` is plain dm-crypt re-keyed from `/dev/urandom` at every boot: dasik writes a 1 MiB ext2 label filesystem at the front of the partition, the crypttab entry and the fstab line. **Forbids hibernation** (preflight refuses it next to `resume=`) and cannot be combined with `encrypt`. Captured by `sync`. See [Swap](wiki/Swap.md). |
| `encrypt` | bool | `false` | LUKS2. Requires `luks_name`. |
| `luks_name` | string | `null` | dm-mapper name (`/dev/mapper/<name>`); `[A-Za-z0-9_-]+`. Use a generic name like `cryptroot`. |
| `luks_password` | string | `null` | Passphrase, **plaintext** in config. Omit → cryptsetup prompts at install. |
| `luks_keyfile` | string | `null` | Path to a key file (instead of a passphrase). |
| `luks_uuid` | string | `null` | Explicit LUKS header UUID. Unset → deterministic UUID (header ↔ cmdline agree). `sync` bakes the real one. |
| `unlock_keyfile` | string | `null` | Key file added as an extra LUKS key for auto boot-unlock (`rd.luks.key`). dasik creates it if missing. **With** `unlock_keydev` the path is relative to that device's root; **without** it, an absolute path inside the target, embedded into the initramfs. |
| `unlock_keydev` | string | `null` | Device holding `unlock_keyfile` (e.g. a USB pendrive): a bare FS UUID, or an explicit `UUID=`/`PARTUUID=`/`LABEL=`/`/dev/…`. |
| `unlock_keydev_fs` | string | `null` | Filesystem of `unlock_keydev` (`vfat`, `exfat`, `ext4`, `btrfs`, `xfs`) — the module the initramfs needs to read it. |
| `unlock_tpm2` | bool | `false` | Enroll a TPM2 keyslot (passwordless). Its own domain (`luks_token`): planned when the header lacks it, silent when it has it, and the keyslot is WIPED when the flag is dropped — unless that would leave the volume with no passphrase. Enrolling needs `luks_password` to authorise the new keyslot. |
| `unlock_fido2` | bool \| int | `false` | FIDO2 keyslots to enroll — `true` is one key, an integer is that many, one per physical key you own (max 31). Needs the key at enroll **and** boot. Same `luks_token` domain and same rules as `unlock_tpm2`, with two additions: keys are enrolled **one at a time** (`systemd-cryptenroll --fido2-device=auto` needs exactly one plugged in), so dasik asks you to swap them and lets you answer `s` to skip the ones you do not have; and a removal wipes keyslots **by number**, so going from 3 keys to 2 takes one, not all three. The header can be counted, never named — dasik knows how many tokens are enrolled, not which key each one is. |
| `luks_options` | list[str] | `[]` | Extra verbatim `rd.luks.options` tokens (e.g. `token-timeout=10s`). |
| `mount_options` | list[str] | `[]` | Extra mount options for the partition. |
| `btrfs_subvolumes` | list | `[]` | Only for `btrfs` (below). |

### BtrfsSubvolume

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `name` | string | — | e.g. `@`, `@home`. |
| `mountpoint` | string | — | e.g. `/`, `/home`. |
| `mount_options` | list[str] | `["compress-force=zstd"]` | |

### Unlocking from a keyfile (a pendrive)  *(sync ✓)*

```json
{ "encrypt": true, "luks_name": "cryptroot", "luks_password": "…",
  "unlock_keyfile": "/keyfile-tuxedo", "unlock_keydev": "1234-ABCD",
  "unlock_keydev_fs": "vfat" }
```

The volume opens by itself when the key device is attached, and still accepts
the passphrase when it is not. What dasik does with that declaration:

* **creates the keyfile** if it is missing (512 × 4 bytes from `/dev/random`,
  mode `0600`) and **enrolls it** as an extra keyslot, authorised by
  `luks_password`/`luks_keyfile`. An existing file is never overwritten — the
  pendrive may already carry another machine's key.
* **is idempotent**: the check is `cryptsetup open --test-passphrase`, so a
  converged machine plans nothing, and a machine that gains a pendrive later
  gets it on the next `apply` (this no longer rides the disk format).
* **writes `rd.luks.key=<luks-uuid>=<path>:UUID=<fs-uuid>`** plus
  `rd.luks.options=<luks-uuid>=keyfile-timeout=10s`. That timeout is not
  optional: without it a boot with the key device absent waits forever instead
  of asking for the passphrase. Declare your own `keyfile-timeout=…` in
  `luks_options` to override it.
* **puts `unlock_keydev_fs` in the initramfs** — dracut `filesystems+=` in
  `/etc/dracut.conf.d/dasik.conf`, mkinitcpio `MODULES+=` (plus FAT's
  `nls_cp437`/`nls_iso8859-1`, without which the mount fails with "IO charset
  cp437 not found") in `/etc/mkinitcpio.conf.d/dasik.conf`. An embedded keyfile
  goes into the image itself the same way (`install_items+=` / `FILES+=`). Both
  live in a dasik-owned drop-in so they can be taken back — your own `MODULES`
  and `FILES` arrays are never touched.

Two caveats worth knowing:

* **`plan` mounts the key device read-only.** Whether the key is enrolled can
  only be answered by reading it, so this is the one place the dry run touches
  anything; the mount is read-only, lives under `/run`, and is always
  unmounted. Without the device attached, the plan says so rather than
  pretending the unlock exists.
* **An `unlock_keyfile` with no `unlock_keydev` is baked into the initramfs,
  which lives on the unencrypted ESP** — the LUKS key then ships next to the
  disk it opens. `preflight` warns about it; it only defends against a disk
  pulled from a powered-off machine *without* its ESP.

`sync` reads all three fields back from the live `rd.luks.key`, and probes the
device's filesystem with `lsblk`.

**Un-declaring removes the kernel parameter, not the keyslot.** `luksKillSlot`
on the wrong slot destroys access to the volume, so dasik reports the keyslot it
is leaving behind and you remove it yourself with `cryptsetup luksRemoveKey`
once you are sure of your other way in.

---

## System basics

### `timezone`  *(sync ✓)*

| Field | Type | Default |
| --- | --- | --- |
| `region` | string | — (e.g. `Europe`) |
| `city` | string | — (e.g. `Madrid`) |

Both or neither: with one missing the section is treated as undeclared, so
`plan` proposes nothing and `sync` captures nothing rather than inventing the
timezone `None/None`. There is no "unset the timezone" operation — dropping the
block leaves the machine's `/etc/localtime` alone.

### `locales`  *(sync ✓)*

| Field | Type | Default |
| --- | --- | --- |
| `selected_locales` | list[str] | — (e.g. `["en_US.UTF-8 UTF-8"]`) |
| `desired_locale` | string | — (e.g. `en_US.UTF-8`) |
| `desired_tty_layout` | string | — (vconsole keymap, e.g. `us`) |

### `network` + `hostname`  *(sync ✓)*

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `network.type` | `NetworkManager` \| `systemd-networkd` | — | **Installs and enables it.** `NetworkManager` adds the `networkmanager` package and `NetworkManager.service`; `systemd-networkd` needs no package (systemd ships it) and enables `systemd-networkd.service` + `systemd-resolved.service` plus a DHCP profile at `/etc/systemd/network/20-dasik-dhcp.network` — the unit alone matches no interface and configures nothing. Write any file under `/etc/systemd/network` yourself and yours is the only one. |
| `network.add_default_hosts` | bool | `true` | Write the `/etc/hosts` block Network_configuration(7) recommends (`127.0.0.1 localhost`, `::1 localhost`, `127.0.1.1 <hostname>`). Without it, software that reads the file directly resolves the machine's own name over the network. Set `false` when something else manages the file. |
| `hostname` | string | `""` | `/etc/hostname`. |

### `users`  *(sync ✓)*

List of accounts:

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `username` | string | — | |
| `hashed_password` | string | — | Crypt hash; `dasik hash-password` prints yescrypt (`$y$…`), the format Arch's `passwd` writes and `sync` captures. `--method sha512` gives the older `$6$…`. |
| `shell` | string | `/bin/bash` | |
| `groups` | list[str] | `[]` | Supplementary groups. |

**The root password is an entry in this list**, with `username: "root"`:

```json
"users": [
  { "username": "root", "hashed_password": "$y$j9T$…" },
  { "username": "andres", "hashed_password": "$y$j9T$…", "groups": ["wheel"] }
]
```

Root is special-cased throughout: it is never created or deleted (the account
always exists), only its password is reconciled — `plan` shows
`~ [users] root (password)` and `apply` runs `usermod -p` and nothing else.
Because of that, a root entry **may not declare `shell` or `groups`**; the model
rejects them rather than accepting values that would be silently ignored.

Omitting root entirely means *dasik does not manage the root password* — it is
left exactly as it is, never locked. `sync` reads the real hash out of
`/etc/shadow`, and captures nothing when root is locked (`!`, `*`, `!$6$…`),
clearing a declaration the machine does not back.

---

## Packages, drivers, boot

### `packages`  *(sync ✓)*

A list; each item is either a **string** (explicitly installed) or an object
`{"name": "...", "reason": "explicit" | "dep", "optional": true | false}`. Use the
**real package name only**
— dasik resolves each name's origin automatically at apply time (configured repo →
pacman group → `package_sources` → AUR), so `firefox`, `yay` and
`claude-desktop-bin` all just work; the same name keeps working if a package later
moves from the AUR into a repo. Names never encode the origin — no `aur-` prefix,
no URLs.

A name found in **no** repo, group, `package_sources` or the AUR is, by default,
**skipped with a visible warning** (yellow console + `[WARNING]` in the log); the
rest install and `apply` exits 0. The skipped name is not recorded as managed and
is retried on the next apply. Set `package_policy.unknown = "error"` to restore a
hard abort instead (useful in CI). A source that could not be *reached* (the AUR
was unreachable) is **always** a blocking error, whatever the policy — dasik does
not know whether the package exists, so it refuses rather than skip.

> The deprecated `aur-<name>` prefix is still accepted (with a warning) for
> configs produced by older syncs; `sync` rewrites it back to the plain name.

A **pacman group** may be declared by name — `"xorg"`, `"texlive"` — and stands
for its members. No group name is ever an installed package, so every verb has
its own answer for one: `plan` calls the group converged when **all** its
members are installed and otherwise plans the group itself (`apply` runs one
`pacman -S xorg`); `sync` keeps the group and does not re-emit its members; and
dropping it from the config plans the removal of the **members**, since that is
what `pacman -R` on a group expands to. Replacing a captured member list with
the group it came from removes nothing. A group installs everything in it, which
may include members the machine did not have (`xorg` carries
`xorg-server-src`) — `pacman -Sgq <group>` lists them.

`sync` captures the explicit packages (`pacman -Qqe`) **plus the package behind
every enabled unit**, as `{"name": "...", "reason": "dep"}` when pacman has it
installed as a dependency. Explicit alone is not enough: a service whose provider
arrived as a dependency — `sddm` pulled in by an orphaned `sddm-kcm` — is invisible
to `-Qqe`, so the captured config re-installed a machine with `sddm.service`
enabled and no `sddm` to enable, and `dasik check` rejected it with
`unit_without_provider`. The provider is found by asking pacman who owns the unit
file (`systemctl show -p FragmentPath` → `pacman -Qqo`), so there is no unit→package
table to keep up to date. Units under `/etc/systemd/system` are yours, not a
package's, and capture nothing — and neither does `base` or one of its direct
dependencies, since dasik pacstraps `base` on every machine it builds and the
entry would change nothing.

#### `optional: true` — a failure that must not stop the install

A package marked `{"name": "sunshine", "optional": true}` may **fail to install
without aborting the apply**. Optional packages are installed in their own batch
*after* the required ones (repo packages one at a time, AUR packages in a separate
build batch), so one broken upstream cannot take the others — or the rest of the
convergence — down with it.

It is not a licence to lie about state: a failed optional package is reported in
red, is **excluded from the manifest** (dasik never claims it is installed), and
the next `plan` lists it again. An unknown optional name is skipped even under
`package_policy.unknown = "error"`. `sync` preserves the flag, since it is intent.

Use it for peripheral software whose source can break independently of your
system: large AUR applications, vendor printer drivers, fonts. Do **not** use it
for anything the machine needs to boot or log in.

```json
"packages": [
  "base", "linux", "plasma-meta",
  {"name": "sunshine", "optional": true},
  {"name": "epsonscan2", "optional": true}
]
```

Why it exists: on 2026-07-19 three peripheral AUR packages out of 311 failed
(one upstream `pkg_resources` transition, two vendor URLs returning HTTP 403).
`yay` installed everything else and exited 1, and that single exit code stopped
the reconciler before users, systemd units, firewall, snapper, the initramfs and
the bootloader — on an already-partitioned disk.

### `luks_token_policy`

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `enroll_failure` | `abort` \| `warn-and-continue` | `abort` | What happens when `systemd-cryptenroll` fails (no key plugged in, never touched, needs a PIN, no TPM). |

`abort` stops the apply and records a partial generation — the default, because
a silent failure leaves the machine asking for a token at boot that nobody
enrolled. `warn-and-continue` reports the failure in red, keeps the keyslot OUT
of the manifest (so `plan` keeps showing it and the next apply retries it), and
carries on with the rest.

**This is the answer for a run nobody is watching.** At a terminal you are asked
instead, per key: `[r] retry  [s] skip this key and the rest  [a] abort`, so
declaring three keys and owning two is a `s` away from an install that finishes
— and the third keeps showing up in `dasik plan` until it is enrolled.

`--yes` counts as "nobody is watching" even on a terminal, and this policy
decides there too: it is the flag that promises no question will be asked, and
an unattended install that deadlocks on *plug in key 2 of 3* is worse than one
that reports the keyslot it could not enrol.

### `package_policy`

`{"unknown": "warn-and-skip" | "error", "build_failure": "abort" | "warn-and-continue"}`

`unknown` — how to treat a declared package that resolves to no known source.
`warn-and-skip` (default) skips it with a warning and continues; `error` aborts
the whole apply before installing anything.

`build_failure` — how to treat a package whose source exists but whose install
or **build** fails (an upstream PKGBUILD that no longer compiles, a download
that 403s). `abort` (default) stops the apply and records a partial generation.
`warn-and-continue` reports the failure, prints an end-of-domain summary naming
every package that is NOT on the machine, keeps them out of the manifest (so
`plan` shows them again and the next apply retries), and carries on with
everything else — the semantics `optional: true` gives one package, applied
machine-wide. An unreachable AUR still always aborts.

### `package_sources`  *(sync ✓ — preserved)*

A map of **package name → Git PKGBUILD source**, for packages that live in no
pacman repo/group and no AUR (e.g. your own public GitHub repo). Only needed for
such packages; everything else resolves automatically.

```json
"packages": ["firefox", "config-saver"],
"package_sources": {
  "config-saver": {
    "type": "pkgbuild-git",
    "url": "https://github.com/amt911/config-saver-aur.git",
    "ref": "7bca56d2e5a016d8e07541b6b5f7708190339d26"
  }
}
```

| Field | Type | Notes |
| --- | --- | --- |
| `type` | `"pkgbuild-git"` | Only value for now. |
| `url` | string | Any HTTPS URL ending in `.git` (GitHub, GitLab, Codeberg, a self-hosted forge). Refused: plain HTTP, and credentials in the URL — `sync` would copy the secret into the captured config. |
| `ref` | string | **Full 40-char commit SHA** — pins the build for reproducibility. Change it deliberately to update. |
| `subdir` | string | Optional; PKGBUILD subdirectory (default `.`). Must stay inside the clone (no `..`). |

Each key must also appear in `packages`. dasik clones the URL, checks out the
exact `ref` (refusing any other commit), builds the PKGBUILD as an **unprivileged**
user, and verifies the built package's `pkgname` matches the declared name
**before** installing. The applied SHA is tracked, so an unchanged `ref` is a
no-op on re-apply and a **changed `ref` triggers a rebuild** even when the name is
already installed. `sync` keeps `packages` as real names and preserves this map
untouched — it never infers a Git source from a package's metadata. A pinned SHA
protects reproducibility, but a PKGBUILD is still third-party code you must trust.

### `drivers`

`list[str]` — GPU driver selection (e.g. NVIDIA). Expanded into packages + config.

### `bootloader`  *(sync ✓)*

`string` — `grub` (default) or `sd-boot` (a.k.a. `systemd-boot`).

On `sd-boot` dasik writes **two** entries: `arch.conf` (the `default`) and
`arch-fallback.conf`, a rescue entry loading `initramfs-linux-fallback.img` when
mkinitcpio built one and the same image as the main entry otherwise (dracut
builds no fallback). Every `kernel_cmdline` parameter is written to both. It also
enables systemd's own `systemd-boot-update.service`, which keeps the loader on
the ESP up to date.

**Switching bootloader removes the old one.** Change the value and the next plan
shows the removal alongside the install:

```text
+ [bootloader] install sd-boot        (install bootloader)
- [bootloader] remove grub            (switched to sd-boot)
```

`apply` uninstalls first, then installs. Leaving GRUB means `/boot/grub`,
`/boot/EFI/GRUB` and the `GRUB` NVRAM entry go; leaving systemd-boot runs
`bootctl remove` and clears `/boot/EFI/systemd` and `/boot/loader`
(`loader.conf`, `entries/`, `random-seed`). The **package** is not touched —
drop `grub` from `packages` yourself if you want it gone.

The stale loader is removed whether or not dasik installed it: two loaders on
one ESP is not a state anyone wants, and after a `sync` the manifest is empty,
so an ownership-gated cleanup would never fire. The firmware (NVRAM) part is
best-effort — a chroot without `efivars` logs a warning instead of aborting the
install — while the on-ESP files always go.

### `initramfs`  *(sync ✓)*

`string` — `mkinitcpio` (default) or `dracut`. Switching to dracut neutralizes
mkinitcpio's pacman hooks automatically.

### `kernel_cmdline`  *(sync ✓)*

`list[str]` — extra kernel parameters appended to the boot entry (e.g.
`intel_iommu=on`). The LUKS `rd.luks.*` params are derived from `disks` — one set
per **encrypted partition**, not just the root one — so they do not belong here.

Two rules worth knowing:

* `rd.luks.name`, `rd.luks.key` and `rd.luks.options` are **repeatable** (one per
  device): an entry you write here is *added* next to the derived ones. Every
  other parameter is single-valued, so yours replaces the derived one
  (`root=`, `resume=`).
* `sync` captures the boot entry's own parameters minus everything dasik derives
  from `disks` (machine-specific UUIDs never enter the config) and minus the
  parameters a block owns — `amd_pstate=`, `intel_pstate=`, `sysrq_always_enabled=1`
  come back as the `cpu` block and the `sysrq` flag, not as raw parameters. What
  is left is what you really set by hand: `resume=`, `quiet`, `intel_iommu=on`.

---

## Services — `systemd`  *(sync ✓)*

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `enable_units` | list[str] | `[]` | Services/timers to enable (`foo.service`, `bar.timer`, `getty@.service`). |
| `enable_sockets` | list[str] | `[]` | Sockets to enable. |
| `disable_units` | list[str] | `[]` | Units to ensure disabled. |

---

## `pacman`  *(sync ✓)*

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `options.Parallel` | bool | `true` | `ParallelDownloads`. |
| `options.Color` | bool | `true` | |
| `options.VerbosePkgLists` | bool | `false` | |
| `multilib` | bool | `false` | Enable the `[multilib]` repo. |

---

## Local `/etc` files  *(sync ✓ — discovers your non-package files)*

Each of these is a `list` of `{"name": "<file>", "content": "<verbatim>"}`, written
into the matching directory. `sync` discovers the files **you** created there
(skipping symlinks and pacman-owned files):

| Field | Directory |
| --- | --- |
| `udev_rules` | `/etc/udev/rules.d` |
| `modprobe_conf` | `/etc/modprobe.d` |
| `modules_load` | `/etc/modules-load.d` |
| `sysctl_d` | `/etc/sysctl.d` |
| `tmpfiles_d` | `/etc/tmpfiles.d` |
| `sddm_conf_d` | `/etc/sddm.conf.d` |
| `profile_d` | `/etc/profile.d` |

Plus:

- **`etc_environment`** *(sync ✓)* — `list[str]`, lines written to `/etc/environment`.
- **`files`** *(sync ✓ for known ones)* — `list` of `{"path": "/etc/...",
  "content": "...", "mode": "0600"?}`, arbitrary absolute-path files written
  verbatim (e.g. `/etc/crypttab`, `/etc/wireguard/wg0.conf`, NetworkManager
  `*.nmconnection`). Optional `mode` is an octal string applied via `chmod` after
  writing — needed for secret keyfiles (wireguard / NetworkManager refuse a
  world-readable one); sync sets `"0600"` on the files it discovers there.

---

## `home_files` — files inside a user's `$HOME`  *(sync ✓ — declared and owned only)*

```json
"home_files": [
  {"user": "andres", "path": ".config/mangohud/MangoHud.conf",
   "content": "fps_limit=144\n"},
  {"user": "andres", "path": ".ssh/config", "content": "…", "mode": "0600"}
]
```

| Field | Type | Notes |
| --- | --- | --- |
| `user` | string | Owner. **The machine decides where the home is** — dasik reads the target's `/etc/passwd`, so this stays right on a machine whose homes are not under `/home`. Before the user exists (a fresh install plans everything up front) the plan falls back to `/home/<user>`. |
| `path` | string | Relative to that home. An absolute path or a `..` segment is refused. |
| `content` | string | Verbatim. |
| `mode` | string | Optional octal, e.g. `"0600"`. |

Ownership is part of the desired state: a file whose content is right but which
is still `root:root` is planned as a MODIFY, and every directory `apply` had to
create is chowned to the user too — a `.config` owned by root is a directory the
desktop cannot add to.

`sync` **never scans a home directory** (it holds ssh keys, browser profiles and
gigabytes of state). It reports back only what the config declares or the
manifest owns, which is exactly what dasik can honestly claim to have put there.

---

## `ai_skills` — AI agent skills and plugins  *(sync ✓)*

Declares which skills each user's AI coding agents carry, and installs them with
**each agent's own official installer** — so updating stays `claude plugin
update` / `npx skills update` and dasik never fights it.

```json
"ai_skills": {
  "users": ["andres"],
  "failure_policy": "warn-and-continue",
  "entries": [
    {"name": "superpowers", "method": "claude-plugin",
     "marketplace": {"name": "claude-plugins-official",
                     "source": "anthropics/claude-plugins-official"}},
    {"name": "superpowers", "method": "codex-plugin",
     "marketplace": {"name": "openai-curated"}},
    {"name": "caveman", "method": "claude-plugin",
     "marketplace": {"name": "caveman", "source": "JuliusBrussee/caveman"}},
    {"name": "impeccable", "method": "skills",
     "source": "pbakaus/impeccable", "agents": ["claude-code", "codex"]}
  ]
}
```

| Field | Type | Notes |
| --- | --- | --- |
| `users` | list[string] | Whose `$HOME` receives them. Empty = every declared user except root. These artefacts live in a home directory, so "system-wide" means "every human on the machine". |
| `failure_policy` | `warn-and-continue` \| `abort` | What `apply` does when an installer fails (no network, no `npx`, marketplace down). Default warns, keeps going, and leaves the item unowned so the next `plan` asks again. |
| `entries[].name` | string | The artefact as its installer knows it. |
| `entries[].method` | `claude-plugin` \| `codex-plugin` \| `skills` | Which official installer to drive. |
| `entries[].marketplace` | `{name, source?}` | Plugin methods only. `source` (owner/repo, git URL) is what `... plugin marketplace add` registers; omit it for a marketplace the agent ships with, like Codex's `openai-curated`. |
| `entries[].plugin` | string | Plugin name inside the marketplace, when it differs from `name`. |
| `entries[].source` | string | `skills` only: what `npx skills add` installs from. |
| `entries[].agents` | list[string] | `skills` only: agent ids of the `skills` CLI — `claude-code`, `codex`, `opencode`, `cursor`. |
| `entries[].users` | list[string] | Narrows this entry to some of the block's users. Only needed when people on one machine carry different sets. |

**There is no `version` field, on purpose.** The block declares *presence*, like
`packages` declares package names: the official CLI owns the version, and
pinning one here would make every `claude plugin update` look like drift the
next `plan` reverts.

What `plan` shows is one item per (user, agent, artefact), plus the marketplace
registration as an item of its own:

```
+ [ai_skills] create andres:claude-code:marketplace:caveman
+ [ai_skills] create andres:claude-code:plugin:caveman@caveman
+ [ai_skills] create andres:codex:skill:impeccable
```

Ordering is load-bearing in both directions: a marketplace is registered before
the plugin that needs it and removed after it. A marketplace already registered
from a **different** source is a MODIFY (re-registered), because `marketplace
add` on an existing name would keep pointing at the other repository.

`apply` runs every command as the user, inside the target
(`su - <user> -c 'claude plugin install "$1" -y --scope user' -- sh <plugin>@<mkt>`),
so it needs the agent's binary — and `nodejs`/`npm` for `npx skills` — installed
on the target. `check` warns when they are not among the declared packages.

Where a skill lands is the `skills` CLI's business, and it is not uniform:
**codex, cursor and opencode read `~/.agents/skills` directly** (the CLI calls
them universal agents), so installing for them writes that one directory and
nothing else; **claude-code** gets `~/.claude/skills/<name>` of its own, a link
to the same copy. dasik reads presence the same way — looking for
`~/.codex/skills/<name>` is how an earlier version of this domain planned the
same install forever while every `apply` reported success.

`sync` reads the agents' own state (`installed_plugins.json`,
`~/.codex/config.toml`, the canonical and per-agent skill directories and
`~/.agents/.skill-lock.json`) and reports the block back. A skill with no
recorded source is named and omitted: capturing it would produce a config no
other machine could reproduce. Skills the agent ships with (Codex's
`~/.codex/skills/.system`) are never captured, and a skill somebody installed by
hand is left alone unless the manifest owns it.

---

## `zram`  *(sync ✓)*

Mirrors `/etc/systemd/zram-generator.conf` as `{device: {option: value}}`:

```json
"zram": { "zram0": { "zram-size": "min(ram / 2, 8192)", "swap-priority": 100 } }
```

Pulls in `zram-generator`.

`sync` reports the file, not the config: no `/etc/systemd/zram-generator.conf`
on the target means no zram, so a declared block the machine does not have is
captured **empty** rather than echoed back. A target that never had zram still
captures nothing.

---

## `oomd`, `systemd_system_conf`, `systemd_user_conf`  *(sync ✓)*

The three pacman-owned `/etc/systemd/*.conf` files, one block per file, each
holding that file's single section:

| Block | File | Section |
| --- | --- | --- |
| `oomd` | `/etc/systemd/oomd.conf` | `[OOM]` |
| `systemd_system_conf` | `/etc/systemd/system.conf` | `[Manager]` |
| `systemd_user_conf` | `/etc/systemd/user.conf` | `[Manager]` |

```json
"oomd": { "DefaultMemoryPressureDurationSec": "20s", "SwapUsedLimit": "90%" },
"systemd_system_conf": { "DefaultTimeoutStopSec": "10s" }
```

Keys are systemd directive names verbatim; values are strings or numbers (a
number is written as-is). A declared `oomd` block enables `systemd-oomd.service`
— the settings do nothing without the daemon.

Reads and writes are deliberately asymmetric. dasik **writes** a drop-in,
`<conf>.d/10-dasik.conf`, never the package file — that is systemd's supported
override mechanism and it keeps `.pacnew` handling out of the picture. It
**reads** the effective configuration: the package file first, then every
`<conf>.d/*.conf` in lexicographic order, exactly as systemd applies them. That
asymmetry is the point: a value someone set by editing `oomd.conf` itself is
still "the machine has it", so `plan` stays silent and `sync` captures it.

Commented-out defaults are documentation, not configuration — a stock file
captures nothing. Dropping a block removes the drop-in **dasik owns**; a
drop-in no generation recorded is left alone.

`sync` reports the machine, not the config: if a declared setting is not in the
effective configuration (someone deleted the drop-in by hand), the block is
captured **empty** rather than echoed back. An undeclared block still captures
nothing, so a bootstrap `sync` invents no empty sections.

Because dasik always writes `10-dasik.conf` and systemd applies drop-ins in
lexicographic order, a foreign drop-in that sorts later — `99-user.conf` is the
conventional admin name — would override the declared value forever. `plan`
refuses in that case, naming the file and the key, instead of proposing the same
change on every run:

```text
Error: /etc/systemd/oomd.conf.d/99-user.conf sets SwapUsedLimit and systemd
applies it AFTER dasik's 10-dasik.conf, so the declared value could never take
effect. Rename or remove that drop-in (or drop the key from the `oomd` block).
```

A later drop-in holding *other* keys, or already holding the declared value, is
not a conflict.

These files could not be covered by the `files` block or the `/etc` snippet
sections: `DropFilesAction` discovery deliberately skips package-owned paths,
which is why a setting here used to survive `apply` and vanish on `sync`.

---

## `sudo`  *(sync ✓)*

Writes `/etc/sudoers.d/10-dasik` (mode `0440`), validated with `visudo -cf`
through a temporary whose name sudo's `#includedir` skips — a fragment that
fails validation never reaches the directory.

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `wheel` | bool | `true` | Writes `%wheel ALL=(ALL:ALL) ALL`. |
| `nopasswd` | bool | `false` | Makes the wheel rule `NOPASSWD: ALL`. |
| `rules` | list | `[]` | Extra sudoers lines, verbatim and in order. Single-line only; `@include`/`#include` are rejected. |

```json
"sudo": { "wheel": true, "nopasswd": false, "rules": ["andres ALL=(ALL) NOPASSWD: /usr/bin/pacman"] }
```

**Implicit default:** with no `sudo` block at all, a user declared in `wheel`
still gets the password-protected wheel rule — stock Arch ships `%wheel`
commented out, so the group alone grants nothing. Opt out with
`"sudo": { "wheel": false }`. Declare a package providing sudo (`sudo` or
`base-devel`): preflight errors on an explicit block without one, and warns for
the implicit default.

---

## `cpu`  *(sync ✓)*

CPU frequency scaling — the old installer's `install_cpu_scaler`.

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `scaling_driver` | string | `auto` | `auto` \| `amd_pstate` \| `intel_pstate` \| `acpi_cpufreq` \| `none`. `auto` reads the CPU vendor from `/proc/cpuinfo`. |
| `mode` | string | `active` | `active` \| `guided` (AMD only) \| `passive` \| `disable`. |
| `power_profiles_daemon` | bool | `true` | Installs and enables `power-profiles-daemon.service`. |
| `governor` | string | `null` | Pins a cpupower governor (`performance`, …): pulls `cpupower`, writes `/etc/default/cpupower`, enables `cpupower.service`. |

```json
"cpu": { "scaling_driver": "auto", "mode": "active", "power_profiles_daemon": true }
```

The kernel parameter (`amd_pstate=active` / `intel_pstate=active`) is **derived**:
it lands on every loader entry and an explicit `kernel_cmdline` entry for the same
key still wins. `sync` subtracts it from `kernel_cmdline` and rebuilds this block
instead — driver and mode from the live entry, `governor` from
`/etc/default/cpupower`, `power_profiles_daemon` from the unit. A machine with no
pstate parameter and no governor captures no `cpu` block at all (ppd on its own is
already covered by `packages` + `systemd`), and `<driver>=disable` comes back as
`scaling_driver: "acpi_cpufreq"` — the only reason dasik emits it. Preflight warns when `power_profiles_daemon`
and `governor` are both set (ppd owns the energy-performance preference) and
errors on ppd + `tlp`.

---

## `reflector`  *(sync ✓)*

Periodic pacman mirrorlist refresh: installs `reflector`, enables
`reflector.timer`, and writes `/etc/xdg/reflector/reflector.conf`.

| Field | Type | Default |
| --- | --- | --- |
| `countries` | list | `[]` |
| `protocols` | list | `["https"]` (`https` \| `http` \| `rsync` \| `ftp`) |
| `latest` | int | `20` |
| `sort` | string | `rate` (`rate` \| `age` \| `score` \| `delay` \| `country`) |
| `save` | string | `/etc/pacman.d/mirrorlist` |

```json
"reflector": { "countries": ["ES"], "protocols": ["https"], "latest": 20, "sort": "rate" }
```

`sync` reads the conf back (repeated *and* comma-separated `--country` lines, both
`--flag value` and `--flag=value`). A conf with no `--latest` captures
`"latest": null` — defaulting it to 20 would add a filter the machine never had.

---

## `plymouth`  *(sync ✓)*

Graphical boot splash. The block is a declaration on its own: `"plymouth": {}`
means the splash with plymouth's default theme; **omitting the block means no
splash at all**.

| Field | Type | Default |
| --- | --- | --- |
| `theme` | string | `null` — leave plymouth's own default (Arch ships `bgrt`) |

```json
"plymouth": { "theme": "bgrt" }
```

What it converges, across four owners:

* the `plymouth` package (it lives in `extra`; the old imperative installer
  still built it from the AUR),
* `/etc/plymouth/plymouthd.conf` with `[Daemon] Theme=…`, when a theme is set,
* `splash` on the kernel cmdline,
* the splash **inside the initramfs** — the `plymouth` hook for mkinitcpio
  (placed after `systemd`/`udev` and before `sd-encrypt`, or it never takes over
  the passphrase prompt), the forced `plymouth` module for dracut.

Changing only the theme rewrites `plymouthd.conf` and nothing else, so that file
counts as an input to the image freshness check: a theme change shows up in
`plan` and rebuilds the initramfs, as the Arch wiki requires.

`sync` captures the block when `/usr/bin/plymouthd` exists on the target, with
the theme read back from `plymouthd.conf`. `splash` is subtracted from the
captured `kernel_cmdline` **only** when plymouth is installed — on a machine
that carries `splash` without plymouth the parameter is somebody else's and
stays a plain entry.

---

## Feature toggles

### `bluetooth`  *(sync ✓ for `in_initramfs`)*

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `enable` | bool | `false` | Installs `bluez`/`bluez-utils` + `bluetooth.service`. |
| `package` | string | `bluez` | |
| `in_initramfs` | bool | `false` | Pull the BT stack into the initramfs (dracut) so a paired BT keyboard works at the early LUKS/FIDO2 prompt. |

### `hardware_acceleration`

| Field | Type | Default |
| --- | --- | --- |
| `enable` | bool | `false` |
| `install_codecs` | bool | `true` |

### `kvm`

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `install` | bool | `false` | libvirt/QEMU stack + user groups. |
| `default_network` | bool | `false` | Autostart libvirt's shipped `default` NAT network. **Independent of `install`**, so a config that carries libvirt as literal packages (what `sync` captures) can declare it without switching to the toggle. |

libvirt ships the network *definition* but not the symlink under
`/etc/libvirt/qemu/networks/autostart/` that starts it, so without
`default_network` a fresh install has a `default` network that is defined,
inactive, and stays that way — and the first guest fails with `Requested
operation is not valid: network 'default' is not active`. Turning the flag off
again removes only the symlink; the network is never undefined.

### `cups`

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `install` | bool | `false` | CUPS + scanning stack + `cups.socket`. |

### `microsoft_fonts`

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `install` | bool | `false` | |
| `source_iso` | string | `null` | Path to a Windows ISO to extract fonts from. |

### `firewall`  *(sync ✓)*

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `enable` | bool | `false` | firewalld; owns the `public` zone. |
| `allowed_services` | list[str] | `[]` | Services to allow (e.g. `syncthing`). |
| `remove_services` | list[str] | `[]` | Default services to remove (e.g. `ssh`). |
| `rich_rules` | list[str] | `[]` | `firewall-cmd` rich-rule strings. |

Rich rules round-trip **losslessly**: family, source/destination address, service,
port+protocol, protocol value, the action (`accept`/`reject`/`drop`) and its rate
`limit`. A rule dasik cannot represent exactly (`log`, `audit`, `masquerade`,
`NOT` …) is **rejected** with `ConfigValidationError` rather than approximated —
dropping a clause of an access rule (e.g. `accept limit value="2/m"`) would
silently widen it.

### `wireguard`  *(a LIST of tunnels; sync ✓ as its own block)*

Each tunnel names a file **next to the config**, in the format its backend
already reads. dasik never converts between the two.

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `name` | string | — | Interface / connection id. 1-15 chars of `[A-Za-z0-9_=+.-]` (IFNAMSIZ). |
| `source` | string | — | Path to the tunnel file, relative to the config that names it. No `..`, no absolute path, no symlink. |
| `backend` | `auto`\|`wg-quick`\|`networkmanager` | `auto` | `auto` reads the file's own format; a declared backend that disagrees is an error, not a conversion. |
| `enable` | bool | `true` | wg-quick only: enable `wg-quick@<name>.service`. |

`wg-quick` gets `/etc/wireguard/<name>.conf` + `wireguard-tools` + the unit;
`networkmanager` gets `/etc/NetworkManager/system-connections/<name>.nmconnection`
and nothing else (NM's keyfile plugin reads the directory itself, which is why
this works during an install with no daemon). **Both are written `0600`** — the
body is the interface's private key, and wg-quick warns while NetworkManager
silently ignores a world-readable one.

### `snapper`  *(sync ✓)*

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `enable` | bool | `false` | Package + timers via the toggle; this creates the configs. |
| `configs` | list | `[{name: "root", subvolume: "/"}]` | Each `{"name": "...", "subvolume": "/abs/path"}`. |

The action runs **before** the package transaction (snap-pac's pacman hooks
snapshot each transaction, so the config must already exist) and installs
`snapper`/`snap-pac` itself if they are not on the target yet. `sync` captures
every config found under `/etc/snapper/configs`.

### Simple bool toggles

| Field | Default | Effect |
| --- | --- | --- |
| `enable_trim` | `false` | `fstrim.timer` for SSDs. |
| `enable_microcode` | `false` | CPU microcode (`amd-ucode`/`intel-ucode`) in the boot entry. |
| `remove_home_on_delete` | `false` | Remove a user's home when the account is removed. |
| `sysrq` | `false` | REISUB: derives `sysrq_always_enabled=1` on the kernel cmdline. `sync` captures it back as this flag (never as a raw `kernel_cmdline` entry), and clears it when the live entry does not carry the parameter. |

---

## Free-form

- **`metadata`** — arbitrary object; stored, not applied.
- **`notes`** — free-text string; not applied.

---

## Complete example (every field)

One config exercising every section — validate a copy with `dasik check`
(this one passes). Sections are independent; delete the ones you don't want.

```json
{
  "timezone": { "region": "Europe", "city": "Madrid" },
  "locales": {
    "selected_locales": ["en_US.UTF-8 UTF-8", "es_ES.UTF-8 UTF-8"],
    "desired_locale": "en_US.UTF-8",
    "desired_tty_layout": "us"
  },
  "network": { "type": "NetworkManager", "add_default_hosts": true },
  "hostname": "archbox",
  "users": [
    { "username": "andres", "hashed_password": "$6$salt$hash",
      "shell": "/bin/zsh", "groups": ["wheel", "docker"] }
  ],
  "packages": [
    "base-devel",
    "docker",
    "openssh",
    "firefox",
    { "name": "linux-headers", "reason": "dep" },
    "yay",
    "config-saver"
  ],
  "package_policy": { "unknown": "warn-and-skip" },
  "package_sources": {
    "config-saver": {
      "type": "pkgbuild-git",
      "url": "https://github.com/amt911/config-saver-aur.git",
      "ref": "7bca56d2e5a016d8e07541b6b5f7708190339d26"
    }
  },
  "drivers": ["nvidia"],
  "bootloader": "sd-boot",
  "initramfs": "dracut",
  "kernel_cmdline": ["intel_iommu=on", "quiet"],
  "enable_microcode": true,
  "enable_trim": true,
  "sysrq": true,
  "remove_home_on_delete": false,
  "sudo": { "wheel": true, "nopasswd": false,
            "rules": ["andres ALL=(ALL) NOPASSWD: /usr/bin/pacman"] },
  "cpu": { "scaling_driver": "auto", "mode": "active",
           "power_profiles_daemon": true, "governor": null },
  "reflector": { "countries": ["ES"], "protocols": ["https"],
                 "latest": 20, "sort": "rate", "save": "/etc/pacman.d/mirrorlist" },
  "plymouth": { "theme": "bgrt" },
  "systemd": {
    "enable_units": ["sshd.service", "fstrim.timer"],
    "enable_sockets": ["cups.socket"],
    "disable_units": ["systemd-networkd.service"]
  },
  "pacman": {
    "options": { "Parallel": true, "Color": true, "VerbosePkgLists": false },
    "multilib": true
  },
  "disks": { "disks": [
    { "device": "/dev/vda", "partition_table": "gpt", "wipe_disk": true,
      "partitions": [
        { "label": "esp", "size": "1GiB", "filesystem": "fat32",
          "partition_type": "esp", "mountpoint": "/boot", "format": true },
        { "label": "cryptswap", "size": "4GiB", "filesystem": "swap",
          "partition_type": "linux-swap", "format": true },
        { "label": "root", "size": "rest", "filesystem": "btrfs",
          "partition_type": "linux", "mountpoint": "/", "format": true,
          "encrypt": true, "luks_name": "cryptroot", "luks_password": "CHANGE_ME",
          "unlock_tpm2": false, "unlock_fido2": false,
          "luks_options": ["token-timeout=10s"],
          "mount_options": ["compress-force=zstd:3"],
          "btrfs_subvolumes": [
            { "name": "@",     "mountpoint": "/" },
            { "name": "@home", "mountpoint": "/home" }
          ] }
      ] }
  ] },
  "udev_rules":    [{ "name": "99-mydev.rules",    "content": "SUBSYSTEM==\"usb\", MODE=\"0660\"\n" }],
  "modprobe_conf": [{ "name": "nvidia.conf",       "content": "options nvidia_drm modeset=1\n" }],
  "modules_load":  [{ "name": "virtio.conf",       "content": "virtio\nvirtio_pci\n" }],
  "sysctl_d":      [{ "name": "99-swappiness.conf","content": "vm.swappiness=10\n" }],
  "tmpfiles_d":    [{ "name": "mytmp.conf",        "content": "d /run/mytmp 0755 root root\n" }],
  "sddm_conf_d":   [{ "name": "autologin.conf",    "content": "[Autologin]\nUser=andres\nSession=plasma\n" }],
  "profile_d":     [{ "name": "editor.sh",         "content": "export EDITOR=nvim\n" }],
  "etc_environment": ["EDITOR=nvim", "MOZ_ENABLE_WAYLAND=1"],
  "files": [{ "path": "/etc/crypttab", "content": "swap LABEL=cryptswap /dev/urandom swap,cipher=aes-xts-plain64\n" }],
  "zram": { "zram0": { "zram-size": "min(ram / 2, 8192)", "swap-priority": 100 } },
  "tailscale": {
    "accept_routes": true,
    "accept_dns": true,
    "ssh": false,
    "shields_up": false,
    "advertise_routes": ["192.168.1.0/24"],
    "operator": "andres",
    "netfilter_mode": "on"
  },
  "bluetooth": { "enable": true, "package": "bluez", "in_initramfs": true },
  "hardware_acceleration": { "enable": true, "install_codecs": true },
  "kvm": { "install": true, "default_network": true },
  "cups": { "install": true },
  "microsoft_fonts": { "install": false, "source_iso": null },
  "firewall": {
    "enable": true,
    "allowed_services": ["syncthing", "samba"],
    "remove_services": ["ssh"],
    "rich_rules": ["rule family=\"ipv4\" source address=\"192.168.1.0/24\" accept"]
  },
  "wireguard": [{ "name": "wg0", "source": "wg/example.conf" }],
  "snapper": { "enable": true, "configs": [{ "name": "root", "subvolume": "/" }] },
  "metadata": { "author": "andres", "created": "2026-07" },
  "notes": "Free-form notes; not applied."
}
```

---

## Validation: `check`, and what runs before a mutation

`dasik check <config>` validates without touching anything, and **the same
validation now runs inside `plan`, `apply` and `sync`** — a config never reaches a
destructive action unvalidated. Two layers:

1. **Schema** (pydantic `JsonModel`): field types, required keys, package-name and
   path grammar.
2. **Preflight** (cross-field coherence, on the *expanded* config). Errors abort
   before the first mutation; warnings are printed and do not block.

| Code | Level | Meaning |
| --- | --- | --- |
| `group_without_provider` | error | A user needs a supplementary group no declared package creates (e.g. `docker` with only `podman-docker`). `useradd -G` would fail — after the disk was wiped. |
| `unknown_group` | warning | Group is neither a base group nor one dasik knows a provider for. |
| `unit_without_provider` (display manager) | error | `sddm.service` enabled but no `sddm` declared, etc. |
| `unit_without_provider` (other units) | warning | Provider not declared, but it is often present as a dependency. |
| `multiple_display_managers` | error | Two DM units enabled; `display-manager.service` can be only one. |
| `display_manager_config_mismatch` | warning | e.g. `sddm_conf_d` declared while Plasma Login Manager is the enabled DM (it reads `/etc/plasmalogin.conf.d`). |
| `crypttab_bad_option` | error | Not `crypttab(5)` syntax (`size512` instead of `size=512`). |
| `crypttab_undeclared_device` | error / warning | Entry names a device no declared partition provides. **Error** when the entry carries `swap`, which reformats that device on every boot. |
| `sudo_without_provider` | error | A `sudo` block is declared but nothing provides sudo (`sudo` / `base-devel`). |
| `wheel_without_sudo` | warning | A user is in `wheel` with no package providing sudo, so the group grants nothing. |
| `ppd_and_governor` | warning | `cpu.power_profiles_daemon` and `cpu.governor` both set; ppd owns the energy-performance preference. |
| `ppd_and_tlp` | error | power-profiles-daemon and `tlp` both manage power policy and conflict. |

## Generations and a failed apply

`apply` records a generation when it converges. When it fails **part-way** — the
disk is already partitioned, some packages are installed — the progress is still
recorded, as a **partial** generation:

```
$ dasik generations
Generation 4
Generation 5 (current, partial — apply failed part-way)
```

A partial generation:

* claims only the domains of actions that actually completed; domains of failed
  or never-reached actions keep the **previous** manifest's ownership (dasik does
  not know they changed, and forgetting ownership would make a later plan stop
  seeing what it owns);
* is **not** a convergence — the next `plan` still lists what is missing;
* **cannot be rolled back to** (`rollback N` refuses it, and a bare `rollback`
  skips it and picks the last complete generation).

Re-running `apply` after fixing the cause does not redo completed work: every
action derives its plan from the live system (`pacman -Qq`, `lsblk`, files under
`/etc`), so installed packages and converged disks are skipped.

## See also

- [copy-your-config-and-test.md](copy-your-config-and-test.md) — capturing a running
  system with `sync`, making the `disks` block generic, and testing in a VM.
- Sample configs under [`config/`](../config/).

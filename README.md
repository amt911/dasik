# dasik

**d**eclarative **a**rch linux **s**cript **i**nstaller **(k**inda**)**

Describe the machine you want in one JSON file. Run `dasik apply config.json`.
Run it again and nothing happens — because nothing has to.

```text
config.json  ──  check → plan → apply ──▶  the machine
config.json  ◀──────────  sync  ─────────  the machine
```

It installs Arch from the live ISO onto `/mnt`, and it manages the machine you
are already running (`--target /`). Same config, same verbs, both directions.

📖 **[Full documentation — the wiki](https://github.com/amt911/dasik/wiki)**

---

## Install

```bash
pip install .          # or: pip install -e '.[dev,mut]' for development
dasik --help
```

Requires Python ≥ 3.10. Two runtime dependencies: `pydantic`, `colorama`.
Managing a target other than `/` also needs `arch-chroot`
(`arch-install-scripts`). Both supported bootloaders are EFI-only.

## Use

```bash
dasik check  config.json                 # validate: JSON + schema + coherence  (read-only)
dasik plan   config.json                 # the dry run: every change, touches nothing
dasik apply  config.json --target /mnt   # converge  (DESTRUCTIVE on install)
dasik sync   config.json --target /      # capture the running system into the config
dasik generations --target /             # what has been applied here
dasik rollback --target /                # restore + re-apply a previous generation
dasik hash-password                      # a crypt hash for users[].hashed_password
```

`plan` and `apply` default to `--target /mnt` (the install target); `sync`,
`generations` and `rollback` default to `/`. To manage the machine you are
booted into, pass `--target /` explicitly.

There is no bare `dasik <config>` form — it exits 2 and points at `plan`/`apply`.

## What makes it idempotent

Every action reads **real** system state — `pacman -Qq`, `/etc/shadow`, `lsblk`,
`cryptsetup luksDump`, the boot entry — and plans only the difference. After a
successful apply, dasik records a manifest of what it owns, so the next plan is
set math:

```text
INSTALL = declared \ actual            # asked for, not there
REMOVE  = (owned ∩ actual) \ declared  # dasik put it there, you stopped asking
```

That middle term is the contract: a package **you** installed by hand is not in
the manifest, so dropping it from the config removes nothing. dasik only takes
back what it put there.

## A config, briefly

```json
{
  "hostname": "archbox",
  "bootloader": "sd-boot",
  "enable_microcode": true,
  "timezone": { "region": "Europe", "city": "Madrid" },
  "locales": { "selected_locales": ["en_US.UTF-8 UTF-8"],
               "desired_locale": "en_US.UTF-8", "desired_tty_layout": "es" },
  "disks": { "disks": [{
    "device": "/dev/nvme0n1", "partition_table": "gpt", "wipe_disk": true,
    "partitions": [
      {"label": "esp",  "size": "1GiB", "filesystem": "fat32",
       "partition_type": "esp", "mountpoint": "/boot"},
      {"label": "root", "size": "rest", "filesystem": "btrfs",
       "partition_type": "linux", "mountpoint": null,
       "encrypt": true, "luks_name": "cryptroot", "luks_password": "…",
       "btrfs_subvolumes": [{"name": "@", "mountpoint": "/"},
                            {"name": "@home", "mountpoint": "/home"}]}
    ]}]},
  "packages": ["base", "linux", "linux-firmware", "sudo", "networkmanager"],
  "systemd": { "enable_units": ["NetworkManager.service"] },
  "users": [{ "username": "andres", "hashed_password": "$y$…",
              "groups": ["wheel"] }],
  "sudo": { "wheel": true },
  "snapper": { "enable": true }
}
```

Every section is optional — `{"packages": ["htop"]}` is a valid config. Feature
blocks expand into the packages, units and files they imply; `disks` handles
partitioning, LUKS, btrfs subvolumes and automatic unlock (keyfile, TPM2,
FIDO2); configs can be split across files with `$include` / `$include_text` /
`$include_line` / `$concat`.

## ⚠️ It is destructive

`apply` and `rollback` partition disks, run `mkfs` and drive `pacman`. Nothing is
repartitioned unless the disk is empty or marked `wipe_disk: true`, destructive
changes are flagged in the plan and confirmed before they run — but point them
at a VM while you are learning, never at a disk you care about.

## Documentation

Everything lives in the **[wiki](https://github.com/amt911/dasik/wiki)**; its
source is versioned in [`docs/wiki/`](docs/wiki/) and published from there.

| | |
| --- | --- |
| [Installation](https://github.com/amt911/dasik/wiki/Installation) · [Quickstart](https://github.com/amt911/dasik/wiki/Quickstart) | get a machine installed |
| [From zero](https://github.com/amt911/dasik/wiki/From-zero) | the whole path: private config repo, `$HOME` archive, install, day two |
| [CLI](https://github.com/amt911/dasik/wiki/CLI) | every verb, flag and exit code |
| [Configuration](https://github.com/amt911/dasik/wiki/Configuration) | **every JSON field there is** |
| [Disks](https://github.com/amt911/dasik/wiki/Disks) · [Boot](https://github.com/amt911/dasik/wiki/Boot) · [Packages](https://github.com/amt911/dasik/wiki/Packages) · [Features](https://github.com/amt911/dasik/wiki/Features) | the deep dives |
| [Config splitting](https://github.com/amt911/dasik/wiki/Config-splitting) | many files, and keeping secrets out |
| [Workflows](https://github.com/amt911/dasik/wiki/Workflows) · [Sync](https://github.com/amt911/dasik/wiki/Sync) · [Validation](https://github.com/amt911/dasik/wiki/Validation) | how it actually works |
| [Recipes](https://github.com/amt911/dasik/wiki/Recipes) · [Troubleshooting](https://github.com/amt911/dasik/wiki/Troubleshooting) | copy a config, fix a boot |
| [Development](https://github.com/amt911/dasik/wiki/Development) | architecture, gates, contributing |

Also in the repository: [`docs/config-reference.md`](docs/config-reference.md)
(a single annotated config containing every field),
[`docs/copy-your-config-and-test.md`](docs/copy-your-config-and-test.md) (capture
your system and test it in a VM), [`docs/vm-testing.md`](docs/vm-testing.md),
[`docs/mutation-testing.md`](docs/mutation-testing.md).

## Development

```bash
pip install -e '.[dev,mut]'
git config core.hooksPath .githooks   # enables the four gates on push

pytest --cov=dasik      # 1,700+ tests, coverage gate 80%
mypy dasik
bandit -r dasik
scripts/mutation.sh
```

New logic is test-driven; new features must be visible to `plan` **and**
capturable by `sync`. See
[Development](https://github.com/amt911/dasik/wiki/Development).

## License

MIT

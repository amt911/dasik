# Installation

dasik is a Python package with a console entry point. It has **two** runtime
dependencies on purpose: `pydantic` (config schema) and `colorama` (output).
Everything else it needs, it shells out to.

## Requirements

| | |
| --- | --- |
| Python | ≥ 3.10 |
| Runtime deps | `pydantic`, `colorama` (installed automatically) |
| To manage a target that is not `/` | `arch-chroot`, from `arch-install-scripts` |
| To install a machine | the Arch live ISO, booted **in UEFI mode** |

Both bootloaders dasik installs are EFI-only (`bootctl install`, or
`grub-install --target=x86_64-efi`). Booting the ISO in legacy BIOS mode
produces an install that reports success and then reboots straight back into the
installer — so [preflight](Validation.md#no_efi_firmware) refuses it up front
when `/sys/firmware/efi` is absent and the config partitions disks.

## Install

```bash
git clone https://github.com/amt911/dasik.git
cd dasik
pip install .
```

Development install (editable, plus the test/quality extras):

```bash
pip install -e '.[dev]'        # pytest, pytest-cov, hypothesis, mypy, bandit
pip install -e '.[dev,mut]'    # + mutmut — required by the pre-push hook
```

Two spellings, same program:

```bash
dasik --help
python -m dasik --help
```

## Installing onto the live ISO

The ISO has Python but no pip packages and no writable site-packages worth
fighting. The reliable route is a virtualenv on the ISO's tmpfs:

```bash
# on the booted Arch ISO, with networking up
pacman -Sy --noconfirm git python-pip python-virtualenv
git clone https://github.com/amt911/dasik.git /root/dasik
python -m venv /root/venv
/root/venv/bin/pip install /root/dasik
/root/venv/bin/dasik --version
```

If the ISO runs out of space mid-install (`cowspace`), grow it:

```bash
mount -o remount,size=75% /run/archiso/cowspace
```

## Running as root

`apply` and `rollback` obviously need root. So does **`sync`** — it reads
`/etc/shadow`, `cryptsetup luksDump` and firewalld's permanent zone files.

Watch the venv trap: `sudo dasik …` resolves `dasik` from **root's** `PATH`, not
yours, so a user-level install disappears. Use the absolute path:

```bash
sudo /home/you/repos/dasik/.venv/bin/dasik sync my-system.json --target /
```

## Verify the install

```bash
dasik --version                       # dasik 0.1.0
dasik check config/install-simple.json
```

`check` is read-only, needs no target and no root. If that prints
`OK — valid dasik config`, the install works.

## Next

- Installing a machine: **[Quickstart](Quickstart.md)**
- Capturing the machine you already run: **[Sync](Sync.md)**

# DASIK - Arch Linux System Installer Kit

A Python-based tool for automated Arch Linux installation and system configuration.

## Installation

```bash
pip install .
```

## Usage

dasik is verb-based (`dasik <verb> <config>`):

```bash
dasik plan   config.json            # show what would change (read-only)
dasik apply  config.json --target /mnt --yes   # converge (DESTRUCTIVE on install)
dasik sync   config.json --target /  # capture the running system back into the config
dasik generations                    # list recorded generations
dasik rollback                       # restore + re-apply a previous generation
```

The bare `dasik <config>` form (no verb) was **removed** — it now errors and
points you at `dasik plan` / `dasik apply`.

> 📖 **[Config reference — every option](docs/config-reference.md)** — the full set
> of config fields (disks, users, packages, services, files, feature toggles, …),
> with types, defaults, and which ones `sync` captures.
>
> 📖 **[Copy your running system into a config and test it in a VM](docs/copy-your-config-and-test.md)**
> — step-by-step for `sync` (capture your system), making the `disks` block
> generic, completing a dracut + LUKS + FIDO2 + bluetooth config, and testing it in
> a KVM. Covers the common gotchas (`sudo dasik` not found, `sync` needs root,
> `arch-chroot` missing).

## Configuration

Place your configuration files in the `config/` directory and reference them when running the tool.

## Development

To install in development mode:

```bash
pip install -e .
```

## License

MIT

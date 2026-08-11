# DASIK - Arch Linux System Installer Kit

A Python-based tool for automated Arch Linux installation and system configuration.

## Installation

```bash
pip install .
```

## Usage

dasik is verb-based (`dasik <verb> <config>`):

```bash
dasik check  config.json            # validate the config (schema + coherence), read-only
dasik plan   config.json            # show what would change (read-only)
dasik apply  config.json --target /mnt --yes   # converge (DESTRUCTIVE on install)
dasik sync   config.json --target /  # capture the running system back into the config
dasik generations                    # list recorded generations
dasik rollback                       # restore + re-apply a previous generation
```

The bare `dasik <config>` form (no verb) was **removed** — it now errors and
points you at `dasik plan` / `dasik apply`.

`plan`, `apply` and `sync` validate the config before reaching the reconciler;
`check`, `plan` and `apply` also run cross-field preflight on the expanded config.
Preflight catches coherence problems such as a user group with no declared
provider, a display-manager unit with no package provider, or a bad/destructive
`/etc/crypttab` entry before mutation. Warnings inform but do not block.

## Documentation

> 📚 **[User wiki](docs/wiki/README.md)** — start here for the current implementation:
> - **[CLI reference](docs/wiki/cli.md)** — every verb, flag, default target and safety note;
> - **[JSON configuration reference](docs/wiki/configuration.md)** — every modeled field, nested parameter, default, enum and validation rule;
> - **[Config splitting and secrets](docs/wiki/config-splitting.md)** — `$include`, `$include_text`, `$include_line` and `$concat`;
> - **[Workflows and state](docs/wiki/workflows.md)** — `check → plan → apply`, `sync`, ownership/idempotency, generations, partial generations and rollback.
>
> 📖 **[Existing single-page config reference](docs/config-reference.md)** — the older long-form field reference remains available for established links. The wiki is organized by task and is cross-checked against the current CLI/models.
>
> 📖 **[Copy your running system into a config and test it in a VM](docs/copy-your-config-and-test.md)**
> — step-by-step for `sync` (capture your system), making the `disks` block
> generic, completing a dracut + LUKS + FIDO2 + bluetooth config, and testing it in
> a KVM. Covers the common gotchas (`sudo dasik` not found, `sync` needs root,
> `arch-chroot` missing).

## Configuration

Place your configuration files in the `config/` directory and reference them when running the tool. For large machine configs, a directory with `main.json` plus fragments is supported; run `dasik check <path>/main.json` to assemble and validate it before planning.

## Development

To install in development mode:

```bash
pip install -e .
```

## License

MIT

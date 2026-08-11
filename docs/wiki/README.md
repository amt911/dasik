# dasik wiki

This wiki documents the **current declarative implementation** of dasik: the `dasik/` package at the repository root. The old material under `archinstall/` is reference/legacy material and does not define the current config format.

## Start here

| I want to… | Read |
| --- | --- |
| See every command and flag | [CLI reference](cli.md) |
| See every JSON field and allowed value | [Configuration reference](configuration.md) |
| Split one large JSON into several files | [Config splitting and secrets](config-splitting.md) |
| Understand `check → plan → apply`, `sync`, generations and rollback | [Workflows and state](workflows.md) |
| Capture a real Arch system and test it in a VM | [Copy your config and test it](../copy-your-config-and-test.md) |
| Look up the older single-page field reference | [Legacy config reference](../config-reference.md) |

## The mental model in one minute

Dasik has two directions:

```text
config JSON  -- check/plan/apply -->  system
system       -------- sync ------->  config JSON
```

- `check` validates JSON, the Pydantic schema and cross-field coherence without touching the system.
- `plan` is the dry run. It compares declared state with reality and prints what would change.
- `apply` converges the target to the config. It can partition/format disks, install/remove packages and rewrite system configuration, so treat it as destructive.
- `sync` reads reality back into the config. It is non-destructive to the system, but it **rewrites the JSON file** and creates a `.bak` next to it when it changes.
- Successful applies record generations. A failed apply can record a **partial generation** so completed progress is not mistaken for convergence.
- `rollback` restores a previous complete config generation and applies it; it is therefore also destructive.

## Target roots matter

Dasik was designed first for installing from an Arch ISO, where the target system is mounted at `/mnt`.

- `plan` and `apply` default to `--target /mnt`.
- `sync`, `generations` and `rollback` default to `--target /`.
- Any target other than `/` requires `arch-chroot` from `arch-install-scripts`.

For day-2 management of the machine you are currently booted into, the usual command is therefore:

```bash
dasik plan my-system.json --target /
```

not a bare `dasik plan my-system.json`, which would target `/mnt`.

## What is the source of truth?

The docs intentionally follow the code rather than historical examples:

1. `dasik/__main__.py` — CLI verbs, flags and defaults.
2. `dasik/lib/models/` — JSON fields, types, defaults and model validation.
3. `dasik/lib/json_parser/includes.py` — config-split directives.
4. `dasik/lib/expand/` — feature blocks that derive packages, units, files or other state.
5. `dasik/lib/validation/preflight.py` — cross-field errors/warnings.
6. actions/reconciler/state code — plan/apply/sync/generation behavior.

If a prose document and the implementation disagree, treat the implementation as authoritative and file/fix the documentation drift.

## Safe first commands

These do not mutate the system:

```bash
# Schema + coherence only
dasik check config/install-megamix.json

# Preview day-2 changes on the running host
dasik plan config/install-megamix.json --target /

# Show the command surface
dasik --help
```

Do not use a real `apply` or `rollback` as a documentation smoke test. Disk actions can wipe and format devices.
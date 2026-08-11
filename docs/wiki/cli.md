# CLI reference

The console entry point is `dasik = dasik.__main__:main`. The old no-verb form `dasik <config>` has been removed; use `dasik plan <config>` or `dasik apply <config>` explicitly.

## Global flags

These flags can be placed before the verb; `-v`/logging flags are also accepted after the operational verbs through their shared parser.

| Flag | Meaning |
| --- | --- |
| `--version` | Print the current package CLI version (`0.1.0`) and exit. |
| `-v`, `--verbose` | Echo the live command stream and show command errors prominently. |
| `--log PATH` | Write the run log to an explicit path. |
| `--no-log` | Disable the run log file. |

`plan`, `apply`, `sync`, `rollback` and `generations` log by default to `./dasik-<verb>-<YYYYmmdd-HHMMSS>.log`. `check` and `hash-password` do not create a default run log.

## Verb summary

| Verb | Mutates system? | Main purpose | Default target |
| --- | --- | --- | --- |
| `check <config>` | No | Validate JSON + schema + preflight | none |
| `plan <config>` | No | Show the convergence diff | `/mnt` |
| `apply <config>` | **Yes** | Converge target to config | `/mnt` |
| `sync <config>` | No system mutation; **rewrites config** | Capture reality back into JSON | `/` |
| `generations` | No | List saved generations | `/` |
| `rollback [N]` | **Yes** | Restore a saved config and apply it | `/` |
| `hash-password` | No | Generate a crypt hash for `users[].hashed_password` | none |

## `dasik check <config>`

```bash
dasik check config.json
```

Read-only validation. It performs, in order:

1. file existence/readability checks;
2. JSON parsing;
3. config-split resolution (`$include`, `$include_text`, `$include_line`, `$concat`);
4. Pydantic validation with `JsonModel`;
5. toggle expansion;
6. cross-field preflight validation.

There is no `--target` because it is intended to validate the declaration without touching a machine.

Successful output is of the form:

```text
config.json: OK — valid dasik config.
```

Use `check` as the first gate for a new or edited config.

## `dasik plan <config>`

```bash
dasik plan config.json [--target ROOT]
```

Options:

| Argument | Default | Meaning |
| --- | --- | --- |
| `config` | required | Path to JSON config. |
| `--target ROOT` | `/mnt` | Root whose state is inspected. Use `/` for the running host. |

`plan` validates and expands the config, runs preflight, asks every registered action for its current/desired state and renders the resulting diff. It does not apply the changes.

Typical install-from-ISO preview:

```bash
dasik plan config/my-machine.json --target /mnt
```

Typical day-2 preview on the running machine:

```bash
dasik plan config/my-machine.json --target /
```

Any target other than `/` is a chroot target and requires `arch-chroot` (`arch-install-scripts`).

## `dasik apply <config>`

```bash
dasik apply config.json [--target ROOT] [--yes]
```

Options:

| Argument | Default | Meaning |
| --- | --- | --- |
| `config` | required | Path to JSON config. |
| `--target ROOT` | `/mnt` | Target system root. |
| `-y`, `--yes` | false | Skip confirmation for destructive changes. |

`apply` runs the same validation/preflight path as `plan`, builds the plan, prints it, then applies it.

**This is a destructive command.** Depending on the config, it can wipe/repartition disks, format filesystems, install/remove packages, create/delete users, change services and rewrite boot/system files.

If the plan is empty, `apply` exits without changing anything. That is the core idempotency goal: re-applying the same declaration to an already-converged target should be a no-op.

### Failed applies and partial generations

If an apply fails after some actions have completed, dasik records the progress as a **partial generation**. A partial generation is not treated as a valid converged state and cannot be a rollback target. Fix the cause and run `apply` again; already-completed/converged work is discovered from real state rather than blindly replayed.

## `dasik sync <config>`

```bash
dasik sync config.json [--target ROOT]
```

| Argument | Default | Meaning |
| --- | --- | --- |
| `config` | required | Existing valid JSON seed to update. |
| `--target ROOT` | `/` | System to read reality from. |

`sync` is the reverse direction: **system → config**. It imports state from convergence-aware actions, subtracts resources that are already implied by high-level feature blocks, and writes the resulting JSON back to the file.

Important behavior:

- The seed must already be valid JSON and pass the Pydantic schema.
- `sync` intentionally does not require preflight to pass; it is allowed to repair a declaration from reality.
- If the result differs, the original file is copied to `config.json.bak` before the new JSON is written.
- Newly discovered empty sections are dropped instead of creating noise.
- A config using split directives is **refused** because writing one resolved document would silently flatten the split. See [Config splitting and secrets](config-splitting.md).

For a split config, use a scratch copy if you need capture information and then fold changes back into the fragments manually.

## `dasik generations`

```bash
dasik generations [--target ROOT]
```

| Argument | Default | Meaning |
| --- | --- | --- |
| `--target ROOT` | `/` | Root whose `/var/lib/dasik/generations` store is listed. |

Each successful apply stores the config snapshot and state manifest under:

```text
<target>/var/lib/dasik/generations/<N>/
  config.json
  state.json
```

A `current` symlink marks the active generation. The list also labels partial generations created by a failed apply.

## `dasik rollback [generation]`

```bash
dasik rollback [N] [--target ROOT] [--yes]
```

| Argument | Default | Meaning |
| --- | --- | --- |
| `generation` | previous complete generation | Generation number to restore. |
| `--target ROOT` | `/` | Root to converge after restoration. |
| `-y`, `--yes` | false | Skip destructive-change confirmation. |

Rollback is not a filesystem snapshot restore. It loads the selected saved **config** and reconciles the current machine toward it.

That means rollback can remove/install packages, change files/services and perform any other changes represented by the generated plan. Treat it as destructive.

Rules:

- An explicit partial generation is rejected.
- With no `N`, dasik chooses the latest earlier **complete** generation, skipping partial ones.
- If current reality already matches the target generation, the plan is empty.
- A successful rollback creates a new generation representing the newly converged state.

## `dasik hash-password`

```bash
dasik hash-password [--method yescrypt|sha512]
```

It prompts twice with `getpass` and prints a crypt hash suitable for `users[].hashed_password`.

| Option | Default | Meaning |
| --- | --- | --- |
| `--method yescrypt` | yescrypt | Arch-style `$y$…` hash, matching the format current Arch tooling writes and `sync` captures. |
| `--method sha512` | — | Older sha512crypt `$6$…` format. |

Empty input, mismatched confirmation or hashing failure returns an error instead of a hash.

For split/private configs, put the resulting one-line hash in a gitignored secret file and reference it using `$include_line`.

## Removed invocation

This no longer installs anything:

```bash
dasik config.json
```

It is deliberately rejected and prints explicit `plan`/`apply` alternatives. Always name the verb so a config cannot be applied merely because a positional argument was supplied.

## Recommended command sequences

### Validate and preview a running machine

```bash
dasik check config/my-machine.json
dasik plan config/my-machine.json --target /
```

### Install from an Arch ISO

```bash
dasik check config/my-machine.json
dasik plan config/my-machine.json --target /mnt
# Review the plan carefully before the destructive step:
dasik apply config/my-machine.json --target /mnt
```

### Capture an existing system

```bash
echo '{}' > /tmp/captured.json
sudo "$(command -v dasik)" sync /tmp/captured.json --target /
dasik check /tmp/captured.json
```

See [Workflows and state](workflows.md) for how the commands relate to ownership, idempotency and generations.
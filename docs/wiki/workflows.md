# Workflows and state

Dasik is easiest to reason about as a declarative reconciler with two directions:

```text
config --check/plan/apply--> system
system --------sync--------> config
```

The important part is not only what a command does, but **which target root** it operates on and which state dasik considers owned.

## 1. Install-from-ISO versus day-2 management

### Fresh installation

Dasik's original target is an Arch ISO with the future system mounted under `/mnt`:

```bash
dasik check config/my-machine.json
dasik plan config/my-machine.json --target /mnt
dasik apply config/my-machine.json --target /mnt
```

`plan` and `apply` default to `/mnt`, so the explicit flag is optional in this workflow, but spelling it out makes a destructive command easier to review.

Anything with a target root other than `/` runs commands via `arch-chroot <target>`, so the host needs `arch-chroot` from `arch-install-scripts`.

An empty `/mnt` is not itself considered an error: on a fresh ISO, the disk action may be the thing that creates/mounts the target first.

### Managing the running host

The running machine is target `/`:

```bash
dasik check config/my-machine.json
dasik plan config/my-machine.json --target /
# Only after reviewing the plan:
dasik apply config/my-machine.json --target /
```

This distinction matters because a bare:

```bash
dasik plan config/my-machine.json
```

targets `/mnt`, not `/`. On a normal installed Arch system that often fails immediately with the actionable `arch-chroot not found` message; it does **not** mean the config itself is necessarily broken.

`sync`, `generations` and `rollback` instead default to `/`, matching day-2 use.

---

## 2. The safe sequence: `check → plan → apply`

### Step 1 — `check`

```bash
dasik check config/my-machine.json
```

`check` is target-free and read-only. It catches:

- malformed JSON;
- missing/bad split fragments;
- Pydantic type/enum/model errors;
- cross-field preflight errors/warnings after feature expansion.

It cannot tell whether the target already matches the declaration because it intentionally does not inspect a target system.

### Step 2 — `plan`

```bash
dasik plan config/my-machine.json --target /
```

`plan` asks actions to compare **desired state** with **actual state** and prints changes. It is the dry run.

A quiet/empty plan means the parts of reality inspected by dasik already converge with the expanded declaration. For feature development, the project tests explicitly require missing state to become visible in plan and present state to become silent; an implementation that simply ignores a declaration would otherwise look deceptively similar to convergence.

### Step 3 — `apply`

```bash
dasik apply config/my-machine.json --target /
```

Apply builds/prints the plan first and then executes it. Destructive changes prompt unless `--yes` is supplied.

Avoid habitual `--yes` while changing disk/user/package ownership. The prompt is a safety layer, not boilerplate.

---

## 3. Idempotency: why a second apply should do nothing

The project goal is NixOS-like declarative convergence: the same config applied twice should not blindly replay imperative setup.

Conceptually:

```text
D = desired resources from the config
A = actual resources on the target
M = resources dasik already considers managed/owned
```

Each action computes changes from the desired/current/owned sets or scalar values. It only applies what is divergent.

That gives the user-facing invariant:

```text
apply(config)
apply(config again)  -> empty plan / no-op, if the first apply converged
```

A no-op re-run is important for destructive domains: partitioning, package removal, user deletion and boot configuration must never happen just because the tool was re-run.

### Ownership matters for removals

Dasik does not generally treat every resource visible on the system as fair game to delete. Removals are based on declared state plus the manifest of what dasik owns. That protects unrelated state installed/configured outside dasik.

In other words, omitting a package/file/unit from your config is not equivalent to saying "delete everything in the world that is not listed". Dasik can remove resources it previously managed where the relevant action/domain supports that ownership semantics.

---

## 4. High-level blocks expand into shared domains

A config can express policy at a higher level:

```json
"kvm": {"install": true}
```

Before reconciliation, `expand_config()` can turn that into contributions such as:

- packages;
- systemd units/sockets;
- modprobe snippets;
- arbitrary files;
- supplementary user groups.

Other examples:

- `bluetooth.enable` → Bluetooth packages + service;
- `cups.install` → packages + socket;
- `firewall.enable` → package + service;
- `wireguard.enable` → package + unit + config file;
- `cpu` → packages/units/files and a pstate kernel argument;
- `reflector` → package + timer + config file;
- `sysrq` → kernel argument;
- `bootloader=sd-boot` → systemd-boot update service.

This explains why `plan` may display a change under `[packages]`, `[systemd]`, `[files]` or `[kernel_cmdline]` rather than under the high-level block name.

The requirement is visibility somewhere in the plan, not necessarily a one-to-one display section for every JSON block.

---

## 5. `sync`: reality back into the declaration

### Basic capture

Start from an existing valid JSON file. `{}` is a useful bootstrap seed:

```bash
echo '{}' > /tmp/captured.json
sudo "$(command -v dasik)" sync /tmp/captured.json --target /
```

`sync` asks registered convergence-aware actions to import real state.

When a change is written:

```text
/tmp/captured.json      <- new captured config
/tmp/captured.json.bak  <- previous exact file text
```

It is non-destructive to the system, but it **is destructive to the config file's previous contents in the ordinary file-editing sense**, which is why the backup exists.

### Why sync subtracts feature contributions

Suppose you declare:

```json
"cpu": {
  "scaling_driver": "amd_pstate",
  "mode": "active",
  "power_profiles_daemon": true
}
```

Expansion may add `power-profiles-daemon`, its service and an `amd_pstate=active` kernel parameter.

A naive sync would capture all of those low-level resources and return something like:

```json
{
  "cpu": {...},
  "packages": ["power-profiles-daemon"],
  "systemd": {"enable_units": ["power-profiles-daemon.service"]},
  "kernel_cmdline": ["amd_pstate=active"]
}
```

That expresses one policy four times and can drift.

Instead, `subtract_contributions()` removes captured resources already attributable to the original high-level declarations. The high-level block remains the owner of what it derives.

This also applies to things such as systemd-boot's update service and other feature-expanded resources.

### `sync → plan` is the key round-trip invariant

A good capture should be reproducible:

```text
machine --sync--> captured config --plan against same machine--> no changes
```

The project's feature capture tests encode that invariant for supported domains.

### What sync cannot recreate

System reality does not contain every piece of original intent.

Examples:

- plaintext passwords/passphrases are not reconstructable from hashes/LUKS state;
- an optional-package flag is intent and may need to be preserved from the seed rather than inferred;
- custom comments/organizational structure are not machine state;
- a config split across files is presentation/authoring structure, not system state.

Therefore use sync as **capture/reconciliation tooling**, not as a magical source-code round-trip for all human intent.

---

## 6. Split configs and sync

A root containing `$include`, `$include_text`, `$include_line` or `$concat` is refused by `sync`.

Reason: sync's writer emits one complete JSON object. Resolving a split and then overwriting `main.json` would flatten all fragments into one file and destroy the author's structure.

Use a standalone scratch seed for capture:

```bash
echo '{}' > /tmp/live.json
sudo "$(command -v dasik)" sync /tmp/live.json --target /
```

Then compare `/tmp/live.json` with your split tree and update the appropriate fragments manually.

See [Config splitting and secrets](config-splitting.md).

---

## 7. Generations

After convergence, dasik stores snapshots below:

```text
<target>/var/lib/dasik/generations/
├── 1/
│   ├── config.json
│   └── state.json
├── 2/
│   ├── config.json
│   └── state.json
└── current -> 2
```

Each generation couples:

- the config snapshot associated with that convergence;
- the state/ownership manifest;
- whether the generation is partial.

List them with:

```bash
dasik generations --target /
```

The active generation is marked `current`.

### A generation is not a filesystem snapshot

It does not mean dasik captured every byte of `/etc` or package database state. It records declarative config + dasik manifest state needed for the reconciler's generation/rollback model.

If you need byte-level filesystem rollback, use an appropriate filesystem/snapshot mechanism in addition to dasik (for example Snapper on Btrfs where configured).

---

## 8. Partial generations

A failed apply can happen after some changes already succeeded:

```text
packages installed
users created
systemd action fails
boot actions never reached
```

Pretending nothing happened would be dangerous. Pretending the desired system converged would also be wrong.

Dasik records a **partial generation** representing progress:

- completed domains can be recorded as completed/owned;
- failed or unreached domains retain appropriate previous ownership rather than being falsely claimed;
- `generations` labels the generation as partial;
- the next plan sees actual reality and continues from there;
- rollback refuses to target a partial generation because it never represented a fully converged state.

Recommended recovery:

1. inspect the actual error/log;
2. fix the config/environment/source of failure;
3. run `check` again;
4. run `plan` and review what remains;
5. re-run `apply`.

Do not manually try to "undo everything the failed run might have done" unless you understand the domain; the manifest/plan machinery exists specifically to reason from current reality.

---

## 9. Rollback

### Default rollback

```bash
dasik rollback --target /
```

With no generation number, dasik chooses the latest generation **before current that is complete**, skipping partial generations.

### Explicit rollback

```bash
dasik rollback 4 --target /
```

The selected generation must exist and must not be partial.

### What rollback actually does

Rollback restores the saved **config**, then builds a normal reconciliation plan against current reality and applies it.

So it can be destructive:

```text
current config: packages [A, B, C]
old config:     packages [A, B]
rollback -> plan may remove C if owned by dasik
```

Likewise for files, units and other managed domains.

Always inspect the printed rollback plan. Do not think of `rollback` as "change a symlink and instantly rewind the entire OS".

A successful rollback is itself a new convergence and therefore records a new generation.

### Partial targets are forbidden

```bash
dasik rollback <partial-generation>
```

fails with guidance to pick an earlier complete generation or fix the failed apply and converge again.

---

## 10. Practical recipes

### A. Safely edit a running-host config

```bash
# edit JSON/fragments
dasik check config/my-machine.json
dasik plan config/my-machine.json --target /
# read every REMOVE/DELETE/destructive line
dasik apply config/my-machine.json --target /
```

Then verify idempotency:

```bash
dasik plan config/my-machine.json --target /
```

Expected for a fully supported/converged declaration: no changes.

### B. Capture before adopting dasik

```bash
echo '{}' > /tmp/my-machine.json
sudo "$(command -v dasik)" sync /tmp/my-machine.json --target /
dasik check /tmp/my-machine.json
dasik plan /tmp/my-machine.json --target /
```

Review secrets before moving that file into a Git repository.

### C. Recover from failed apply

```bash
dasik generations --target /
# fix cause
dasik check config/my-machine.json
dasik plan config/my-machine.json --target /
dasik apply config/my-machine.json --target /
```

### D. Compare with an earlier declaration

Use the saved generation's `config.json` as a reference and run a plan before deciding whether to invoke rollback. The plan is the useful part: it tells you what reverting declaration would mean **now**.

---

## 11. Safety rules to keep

- Never run `apply` merely to test whether parsing works; use `check`.
- Never run `apply` merely to see what would happen; use `plan`.
- Confirm target root before any destructive invocation.
- Review `wipe_disk`, partition `format`, package removals and user/file deletions carefully.
- Avoid `--yes` until you deliberately accept the destructive plan.
- Keep config backups/version control, but never commit secrets just because they are inside JSON.
- Treat a partial generation as progress evidence, not a known-good restore point.
- After convergence, an additional `plan` is a valuable idempotency sanity check.

See [CLI reference](cli.md) for exact command arguments and [Configuration reference](configuration.md) for every field.
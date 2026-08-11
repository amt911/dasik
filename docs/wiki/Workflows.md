# Workflows and state

How a config becomes a machine, what dasik remembers, and why a re-run is
silent.

```text
config.json
  → resolve $include directives
  → pydantic schema (JsonModel)
  → expand feature blocks           (packages/units/files a toggle implies)
  → preflight                       (cross-field coherence; errors abort here)
  → Reconciler walks the action registry
      → action.plan()    compare declared vs REAL system state
      → action.apply()   carry out exactly those changes
      → manifest + generation recorded
```

---

## Ownership

After every successful apply, dasik writes a **manifest** to
`<target>/var/lib/dasik/state.json`: per domain, the exact set of items it
owns. The next plan is set math over three sets — declared **D**, actual **A**,
managed **M**:

| | |
| --- | --- |
| `INSTALL` | `D \ A` — declared, not present |
| `REMOVE` | `(M ∩ A) \ D` — dasik put it there, you stopped declaring it |
| `MODIFY` | present in both but drifted (shell, hash, file content, record fields) |

The `M` in the removal term is the whole social contract: **a package you
installed by hand is not in the manifest, so dropping it from the config removes
nothing.** dasik only takes back what it put there.

Two consequences worth internalising:

- **A fresh machine with no manifest can never remove anything.** Bootstrapping
  a config from a running system with [`sync`](Sync.md) records ownership as it
  captures, which is why the first plan afterwards is silent rather than a wall
  of removals.
- **`plan` loads the same manifest `apply` loads.** Planning with an empty one
  would hide every removal from the dry run while apply carried them out
  unannounced. A plan that cannot say "this will be removed" is not a dry run.

---

## Execution order

The registry order is load-bearing. `dasik/lib/actions/actions_handler_v2.py`
(`setup_actions()`):

| Phase | Actions | Why here |
| --- | --- | --- |
| **1 — disk & base** | `disks` → `luks_keyfile` → `pacman_hooks` → `base` | the keyfile is enrolled while the volumes are open and *before* an initramfs is built around an `rd.luks.key` that would open nothing; the pacman hooks must exist before the **first** transaction or mkinitcpio clobbers dracut's image |
| **2 — chroot config** | `timezone`, `locales`, `network`, `pacman` | cheap, no dependencies |
| **3 — packages** | `snapper` → `packages` → `users` → `sudo` | snapper's config must exist before the first transaction snap-pac hooks; `useradd -s /bin/zsh -G libvirt` needs the shells and groups packages create; the sudoers fragment needs `visudo` (from the `sudo` package) and a populated `wheel` |
| **4 — services & files** | `systemd`, `firewall`, `files`, `microsoft_fonts`, `zram`, `oomd`/`systemd_system_conf`/`systemd_user_conf`, then the capture-only `cpu`, `reflector`, `plymouth` | |
| **5 — boot (last)** | `initramfs` → `bootloader` → `kernel_cmdline` | the loader must be installed before its entry's parameters are maintained |

Capture-only actions (`cpu`, `reflector`, `plymouth`) have a deliberately empty
`plan()`. They exist so `sync` — which only visits registered v3 actions —
reaches them. Their convergence is delivered by the expand toggles and the
cmdline. See [Sync](Sync.md#capture-only-domains).

---

## Generations

Every successful apply appends a generation under
`<target>/var/lib/dasik/generations`, holding the config and the manifest as
applied.

```bash
dasik generations --target /
```

```text
Generation 1
Generation 2 (current)
Generation 3 (partial — apply failed part-way)
```

`rollback` restores a generation's config and **re-applies it**, so it is as
destructive as `apply`. A successful rollback is recorded as a *new* generation:
history is append-only, and you can roll a rollback back.

Rollback with no number picks the generation immediately before the current one,
**skipping partial generations**.

### Partial generations

An apply that fails part-way has still mutated the machine. dasik persists what
completed as a **partial** generation, and the distinction is enforced:

- ownership of failed or unreached domains is **carried forward** from the
  previous manifest, so nothing is silently disowned;
- `rollback` refuses to restore a partial generation — it never represented a
  state the system converged to;
- the next `plan` still shows the remaining divergence.

It records **progress, never convergence**. Fix the cause and run `apply` again;
completed work is not redone.

---

## The round trips that matter

A green unit suite does not prove any of this. Each verb enters the code through
a different door.

| Verb | What only this verb proves |
| --- | --- |
| `check` | the config still validates — including **one `sync` just produced**. A capture the tool then refuses is a broken capture. |
| `plan` | the domain is visible at all, in both directions, and it converges |
| `apply` | the change lands where it was announced, and a re-run writes nothing |
| `sync` | the feature reads back **as its own block**, and nothing is invented on a machine that lacks it |
| `generations` / `rollback` | the manifest records the domain, and a restored generation re-plans to nothing |

Run them as pairs:

```bash
dasik plan c.json && dasik apply c.json && dasik plan c.json   # must end silent
dasik sync c.json && dasik check c.json && dasik plan c.json   # must end silent
```

Real defects only these round trips caught:

- a domain that planned a change, applied it, and planned the **same** change
  forever — a systemd drop-in another file outranked, with `apply` reporting
  success every time;
- a `sync` that reported the config back instead of the machine, so the capture
  described a setting nobody had applied;
- a `sync` whose output `check` then rejected, because the capture omitted the
  package behind an enabled unit;
- an **undeclared** domain planning a destructive MODIFY —
  `ln -sf /usr/share/zoneinfo/None/None`, or commenting out every locale.

### The empty-config trap

That last one is the general shape. When a previous generation owned a domain
whose block you have now **deleted**, the reconciler hands the action its *empty*
config — and an empty config is not the same thing as "the empty value". So test
each domain three ways: declared, absent, and **removed after having been
owned**.

### Detectability

Every feature must satisfy, in both directions:

| Situation | Expected |
| --- | --- |
| declared, missing on the target | a change is planned |
| declared, present | silence |
| **not** declared, but owned in the manifest | `REMOVE` |
| not declared, present, **not** owned | left alone |

Silence is ambiguous — "already converged" and "dasik ignores this block" look
identical. Hence the matrix in `tests/lib/test_feature_detectability.py`, and
its mirror for capture in `tests/lib/test_feature_sync_capture.py`.

---

## Install vs day-2

Same config, same verbs, different target and a different starting point.

| | Install | Day 2 |
| --- | --- | --- |
| Target | `/mnt` (the default for plan/apply) | `/` (pass `--target /`) |
| Disks | `wipe_disk: true` — the machine is created | converged disks are skipped entirely |
| Chroot | every command via `arch-chroot` | run directly |
| Typical first move | write a config, `check`, `plan`, `apply` | `sync` into a config, `check`, `plan` (silent), then edit |

A day-2 config normally declares **no** `disks` block at all. If it does (from a
`sync`), that block is inert: the disks are converged, so nothing is planned.

---

## Safety model

| Rule | Enforced by |
| --- | --- |
| never reformat a populated disk you did not mark `wipe_disk` | the disk action's plan |
| every destructive change confirmed before it runs | one prompt on `apply`/`rollback`, `--yes` to skip |
| a repartition is flagged even though its op says "install" | `destructive=True` on the change, `** DESTRUCTIVE **` in the render |
| a coherence problem aborts **before** the first mutation | [preflight](Validation.md) |
| an unreachable package source never downgrades to "does not exist" | the resolver |
| a bad sudoers fragment never reaches `/etc/sudoers.d` | `visudo` validation |
| the LUKS keyslot is never killed | the keyfile action reports it and leaves it |

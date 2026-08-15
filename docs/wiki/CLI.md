# CLI reference

```text
dasik <verb> [args] [--target ROOT] [-v] [--log PATH | --no-log]
```

Source of truth: `dasik/__main__.py` (`_build_parser`, `_KNOWN_VERBS`).

## Verbs at a glance

| Verb | Mutates the system? | Mutates the config file? | Default `--target` | Needs root |
| --- | --- | --- | --- | --- |
| [`check`](#check) | no | no | *(no target)* | no |
| [`plan`](#plan) | no | no | `/mnt` | to read some state |
| [`apply`](#apply) | **YES — destructive** | no | `/mnt` | yes |
| [`sync`](#sync) | no | **yes** (writes `.bak`) | `/` | yes |
| [`save`](#save) | no | **yes** — and commits it | `/` | yes |
| [`generations`](#generations) | no | no | `/` | yes |
| [`rollback`](#rollback) | **YES — destructive** | no | `/` | yes |
| [`hash-password`](#hash-password) | no | no | *(no target)* | no |

There is **no** bare `dasik <config>` form. It was removed with the legacy
monolithic handler; it now exits 2 and points you at `plan`/`apply`.

## Global flags

| Flag | Meaning |
| --- | --- |
| `--version` | print `dasik <version>` and exit (read from the installed package, not a literal) |
| `-v`, `--verbose` | echo the live command stream to the console and show errors in red; also adds a traceback to a crash |
| `--log PATH` | write the run log here instead of the default |
| `--no-log` | write no run log at all |
| `-h`, `--help` | help; also per-verb (`dasik apply --help`) |

These work **before or after** the verb: `dasik -v apply c.json` and
`dasik apply c.json -v` are the same run.

### Logging

`plan`, `apply`, `sync`, `rollback` and `generations` write
`./dasik-<verb>-<YYYYmmdd-HHMMSS>.log` in the current directory by default.
`check` and `hash-password` write nothing.

The log holds the full command stream — every `pacman`, `cryptsetup`, `sgdisk`
invocation and its output — which is what makes a failed install diagnosable
after the fact. `-v` mirrors it to your terminal live.

> **A sync log can contain secrets.** It records what was read back, which for a
> WireGuard or NetworkManager keyfile means private keys. Treat
> `dasik-sync-*.log` as sensitive; don't commit it.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | success (including "nothing to do") |
| `1` | a real failure: invalid config, preflight error, unusable target, failed apply, aborted confirmation |
| `2` | usage error — no verb, unknown verb, or the removed `dasik <config>` form |
| `130` | interrupted (Ctrl-C) |

---

## `check`

```bash
dasik check <config>
```

Read-only, no target, no root. Runs, in order:

1. JSON parse;
2. [config-splitting](Config-splitting.md) directives resolved (`$include`, …) —
   so `check` is also how you validate a split config;
3. the pydantic schema (`JsonModel`);
4. [feature expansion](Features.md) (`expand_config`);
5. cross-field [preflight](Validation.md) on the **expanded** config.

Prints `<config>: OK — valid dasik config.` and exits 0, or the reason and 1.

**It does not depend on the machine running it.** `check` validates a *file* —
routinely from another laptop, a container or a CI runner — so the preflight's
environment checks are skipped here. The EFI one is the whole reason: refusing
`sd-boot` because *this* host booted BIOS makes a perfectly good config
unvalidatable. `plan` and `apply` still refuse it; they are about to install
here.

Use it on **both** ends of a round trip: on a config you wrote, and on a config
`sync` just produced. A capture the tool then refuses is a broken capture.

## `plan`

```bash
dasik plan <config> [--target /mnt|/]
```

The dry run. Read-only. It validates exactly as `check` does, then builds the
plan by asking every action to compare declared state against the machine.

Output is one line per change:

```text
  + [disks] install /dev/vda  (empty disk — ERASES /dev/vda)  ** DESTRUCTIVE **
  ~ [timezone] modify Etc/UTC  (set)
  - [packages] remove htop  (no longer declared)
```

| Sign | Ops | Meaning |
| --- | --- | --- |
| `+` | `install`, `create`, `enable` | it is declared and missing |
| `-` | `remove`, `delete`, `disable` | dasik **owns** it and you stopped declaring it |
| `~` | `modify` | it exists but drifted |

`** DESTRUCTIVE **` marks a change whose op does not look dangerous but whose
apply is — a repartition is an `install` that runs `wipefs`/`sgdisk`/`mkfs`.

`plan` loads the **same manifest `apply` loads**. Without it the `-` lines could
not exist, and `apply` would remove things the dry run never announced.

An empty plan means converged. Because silence is also what an *unseen* feature
produces, every feature ships a detectability test — see
[Workflows](Workflows.md#detectability).

## `apply`

```bash
dasik apply <config> [--target /mnt|/] [--yes|-y]
```

**Destructive.** Partitions disks, runs `mkfs`, drives `pacman`, rewrites system
configuration.

1. builds the same plan `plan` prints, and prints it;
2. empty plan ⇒ exit 0, nothing done;
3. destructive changes ⇒ one confirmation prompt (skipped by `--yes`);
4. applies domain by domain in registry order;
5. records a **generation** and writes the manifest.

On failure it prints the error, records what completed as a **partial
generation**, and exits 1. The system *has* been mutated; the next `plan` sees
that reality and resumes from it. See
[Workflows](Workflows.md#partial-generations).

On success: `Applied: now at generation N.`

> `--yes` exists for unattended runs (VM harness, CI). On hardware, read the
> plan.

## `sync`

```bash
dasik sync <config> [--target /]
```

The reverse direction: capture the machine into the config file. Non-destructive
to the system; it **rewrites the config file** and leaves `<config>.bak`.

- The seed config is schema-validated first — `sync` rewrites this file, so
  starting from a config pydantic would reject would launder it into a new one.
- **No preflight.** `sync`'s job is to report reality, including incoherent
  reality.
- An **undeclared** domain is still captured (bootstrap from `{}`).
- A config **assembled from includes is written back through the split**: each
  value returns to the file it came from, and a directive whose value did not
  change is left alone — its file is not opened. Every file written is named in
  the output ([Config splitting](Config-splitting.md#sync-writes-back-through-the-split)).
- Values the seed's own toggles already derive are subtracted again, so the
  captured config keeps the toggle instead of its expansion.
- Newly-added empty keys are dropped, so a bootstrap does not add `"packages": []`
  to a config that never had one.

Prints `Config already matches system reality - nothing to sync.` when nothing
changed. What each domain can capture: [Sync](Sync.md).

## `save`

```bash
sudo dasik save <config> [-m MSG] [--no-push]
```

`sync`, then commit what it wrote to the Git repository the config lives in —
the five-step cycle as one command. The order matters: **`check` runs on the
capture before the commit**, because a config the tool would refuse is a broken
capture and committing it spreads it to every machine that clones the repo.

| Thing | Where it comes from |
| --- | --- |
| repository | the Git work tree containing the config |
| remote | its `origin` (no origin ⇒ commits, says it did not push) |
| author | `$SUDO_USER`'s own Git identity |
| message | `-m`, else `<hostname>: sync <date>` — the hostname the **config** declares |

Nothing is added to the schema: a config describes the *system*, and which
remote it is pushed to is not part of that.

**It runs Git as you, not as root.** `sync` needs root; the commit does not, and
a commit authored by root in your repository with root's (absent) credentials is
not what anybody wants. `save` also hands back ownership of every file the
capture wrote — `sudo dasik sync` leaves them `root:root` in your repo.

**A gitignored file is never staged.** The writeback legitimately rewrites
`secrets/hashed-password`; staging it would commit a password hash on the
strength of a convenience flag. Such files are reported instead:

```text
  wrote /home/andres/config/secrets/hashed-password  (gitignored, not staged)
Committed: torre: sync 2026-08-15
Pushed to origin.
```

The `<config>.bak` that `sync` leaves is **removed once the commit holds the
capture** (the previous commit is a better backup), and kept when nothing was
committed.

Refusals: not a Git repository, running as plain root with no `SUDO_USER`, a
written file outside the work tree, and a capture `check` rejects.

## `generations`

```bash
dasik generations [--target /]
```

Lists what has been applied under this target, from
`<target>/var/lib/dasik/generations`:

```text
Generation 1
Generation 2
Generation 3 (current)
Generation 4 (partial — apply failed part-way)
```

Read-only.

## `rollback`

```bash
dasik rollback [N] [--target /] [--yes|-y]
```

**Destructive** — it restores generation *N*'s config **and re-applies it**.

- `N` omitted ⇒ the generation immediately before the current one, **skipping
  partial generations** (they never represent a converged state).
- Restoring a *partial* generation is refused outright.
- Prints the plan first; an empty plan means the system already matches that
  generation.
- A successful rollback is recorded as a **new** generation — history is
  append-only, so you can roll the rollback back.

## `hash-password`

```bash
dasik hash-password [--method yescrypt|sha512]
```

Prompts twice (no echo) and prints one crypt hash for a user's
`hashed_password`. No target, no root, no log.

- `yescrypt` (default, `$y$…`) — what Arch's own `passwd` writes
  (`ENCRYPT_METHOD YESCRYPT` in `login.defs`), and therefore what `sync` reads
  back out of `/etc/shadow`.
- `sha512` (`$6$…`) — the older sha512crypt; needs libxcrypt for yescrypt to be
  unavailable to be worth choosing.

Exit 1 on empty input, mismatch, or a hashing failure.

---

## Target roots

| | |
| --- | --- |
| `--target /` | the running host (day-2 management) |
| `--target /mnt` | an install target mounted by the ISO |

Any target other than `/` prefixes **every** command with
`arch-chroot <root>`. That binary ships in `arch-install-scripts`. dasik checks
for it *before* the first action and, if it is missing, tells you both how to
install it and that you probably meant `--target /` — instead of dying deep in a
probe with `Binary not found: arch-chroot`.

An empty `/mnt` is not an error: that is exactly the state of a fresh ISO before
the disk actions have mounted anything.

## Recipes

```bash
# validate a split config
dasik check config/split-example/main.json

# preview day-2 changes, quietly, no log file
sudo dasik plan my-system.json --target / --no-log

# unattended VM install with a full log
dasik apply config/vm-btrfs.json --yes -v --log /tmp/install.log

# capture a machine into a scratch file and diff it against the tracked one
cp my-system.json /tmp/s.json && sudo dasik sync /tmp/s.json --target /
diff <(jq -S . my-system.json) <(jq -S . /tmp/s.json)
```

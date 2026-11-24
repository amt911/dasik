# config-saver sync between machines

*config-saver 3.7.0 or newer.* This page is about **config-saver's** `--sync`, which keeps files in your
`$HOME` identical across machines. It is not about `dasik sync`, which captures a machine's state
into its dasik config (see [Sync](Sync.md)).

`config-saver --sync` keeps chosen configurations identical on several machines. Typical uses:

- a desktop and a laptop that share shell, editor and git settings;
- agent sessions (Claude Code, Codex, opencode) started on one machine and resumed on the other.

Everything travels through **one restic repository**, which you can host on any storage restic can
reach: Google Drive or Proton Drive through rclone, an SFTP server, or a disk or NAS mount. The
repository is encrypted end to end, so the storage provider only ever sees ciphertext.

Sync is **not a backup**, and **not real time**:

- It does not replace backups: your backups (archives or the restic backup store) are a separate
  thing, and sync never touches them.
- It is not real time: a timer runs it every 15 minutes, and you can run it by hand whenever you
  want.
- It is built for **sequential** use. You work on one machine, then on the other. Two machines
  editing the same file between two syncs is a **conflict**: sync stops and asks you which version
  to keep. It never guesses.

> **In one sentence:** each machine publishes what only it changed and takes what only the others
> changed. A path changed on both sides stops the run, nothing is written, and you choose.

---

## Contents

1. [Requirements](#requirements)
2. [Choose where the repository lives](#1-choose-where-the-repository-lives)
3. [Mark what to sync](#2-mark-what-to-sync)
4. [Set up the first machine](#3-set-up-the-first-machine)
5. [Join the other machines](#4-join-the-other-machines)
6. [Turn on the timer](#5-turn-on-the-timer)
7. [Daily use](#daily-use)
8. [Conflicts](#conflicts)
9. [Moving to another provider](#moving-to-another-provider)
10. [Pruning and repository size](#pruning-and-repository-size)
11. [Rotating the age key](#rotating-the-age-key)
12. [Limits](#limits)
13. [Command reference](#command-reference)

For problems, see [Sync troubleshooting](Config-saver-sync-troubleshooting.md). For the design, see
[How sync works](Config-saver-sync-internals.md).

---

## Requirements

| What | Why |
|---|---|
| config-saver **3.7.0** or newer | on every machine |
| `restic` **0.17** or newer | the repository format. Sync tells a missing repository apart by restic's exit code 10, which older versions do not give |
| `age` and **one shared age identity** (`~/.config/age/key.txt` by default, `--identity` overrides) | the repository password is derived from it, so nothing else has to be copied between machines |
| `rclone` | only for `rclone:` URLs (Google Drive, Proton Drive, ...) |
| `libnotify` (`notify-send`) | optional: desktop notifications when a timed run fails |

The age identity is a **secret**. Anyone who holds it can read the repository. Copy it to each
machine over a safe channel (a USB stick, `scp`, your password manager), never through the synced
storage itself.

---

## 1. Choose where the repository lives

The URL is any repository URL restic accepts:

| Storage | URL | Notes |
|---|---|---|
| **Google Drive** | `rclone:Google Drive:config-saver-sync` | Run `rclone config` first and create a remote, here named `Google Drive`. |
| **Proton Drive** | `rclone:Proton:config-saver-sync` | A remote of type `protondrive`. Proton throttles requests: add a rate limit with `--sync-option rclone.args="serve restic --stdio --tpslimit 5"`. That option *replaces* restic's default rclone arguments, so it must start with `serve restic --stdio`. |
| **SFTP** | `sftp:user@host:/srv/config-saver-sync` | Use a key without a passphrase, or an agent the timer can reach (see [the timer](#5-turn-on-the-timer)). |
| **Disk, NAS mount** | `/mnt/disk/config-saver-sync` | Any path that every machine can reach. |
| **Other restic back ends** | `s3:...`, `rest:...` | Repository options go through `--sync-option KEY=VALUE` (passed to restic as `-o`). |

Sync uses its **own** restic repository. It never shares one with the backup store.

---

## 2. Mark what to sync

Sync works on whole configurations, the same YAML files you already use for backups. Add `sync: true`
to each one you want shared:

```yaml
# ~/.config/config-saver/configs.d/zsh.yaml
directories:
  - "$HOME/.config/zsh"
  - source: "$HOME"        # only these files of $HOME, not the whole directory
    files:
      - .zshrc
sync: true
```

Agent session directories are big. Raise `sync_max_mb` for those configurations **before the first
sync**: the default limit is 50 MiB per configuration, and a run that goes over it stops with exit `3`.

```yaml
# ~/.config/config-saver/configs.d/codex-conversations.yaml
directories:
  - "$HOME/.codex/sessions"
sync: true
sync_max_mb: 4096   # MiB, minimum 1, default 50
```

Rules worth knowing:

- **Each machine syncs only what its own YAML declares.** If one machine adds a path to a YAML, the
  others do not pick it up until their YAML has it too. A machine never deletes or overwrites a path
  it does not declare. So a machine with a *narrower* YAML never deletes files that a wider one
  manages.
- **Leave per-machine settings unmarked.** KDE, monitors and GPU tweaks stay local because their
  YAMLs have no `sync: true`.
- **Use the same `normalize_content` on every machine** for a synced configuration. Otherwise the same
  file hashes differently on each side and looks changed on every run.
- `sync: true` cannot be combined with `only_root_user`. A synced configuration also cannot declare
  anything under `/etc/config-saver/configs` (exit `3`).

### With dasik and a personal-config repository

When dasik manages your machines, the config-saver documents are versioned in your personal-config
repository. Documents for every machine go under `common/home/<user>/.config/config-saver/configs.d/`;
a document for one machine only goes in that machine's tree. You still edit the live copy:

```text
edit ~/.config/config-saver/configs.d/<name>.yaml (add sync: true)
  →  dasik save  →  commit  →  dasik apply on the other machines
```

Put `sync: true` (and a large enough `sync_max_mb`) in the **common** document, so that every machine
marks the configuration the same way. dasik only writes the YAML. The sync repository, the age
identity and the timer are set up per machine, by hand, as described below.

---

## 3. Set up the first machine

```bash
config-saver --sync-init "rclone:Google Drive:config-saver-sync"
# created
config-saver --sync
# zsh: applied 0, deleted 0, published 12
# codex-conversations: applied 0, deleted 0, published 348
```

`--sync-init` creates the repository and stores its URL and options in
`~/.config/config-saver/sync.json` (mode `0600`). It prints `created` for a new repository, or
`joined` when one already exists at that URL. The first `--sync` publishes everything this machine
has.

---

## 4. Join the other machines

On each other machine:

1. Copy the **same age identity** to `~/.config/age/key.txt`.
2. Run the **same** `--sync-init` command. This time it prints `joined`.
3. Mark the **same configurations** `sync: true` (with the same `sync_max_mb`).
4. Before turning on the timer, look at what the first sync would do:

   ```bash
   config-saver --sync-status
   ```

   A file that already exists on this machine *and* differs from the repository is a conflict on the
   first sync, because sync has no shared history to know which side is newer. Settle those
   conflicts now:

   ```bash
   config-saver --sync-resolve remote            # take the repository's versions for every conflict
   config-saver --sync-resolve local --path ~/.zshrc   # or keep this machine's copy of one path
   ```

5. Run `config-saver --sync`, then turn on the timer.

---

## 5. Turn on the timer

```bash
systemctl --user enable --now config-saver-sync.timer
```

The timer runs `config-saver --sync` 2 minutes after boot and every 15 minutes after that. When a run
fails, `config-saver-notify@.service` shows a desktop notification. The cases are a conflict (exit
`11`), an unreachable repository (exit `10`) and sync not set up (exit `2`).

The timer runs **without your interactive session**, so check that:

- an **rclone** remote has its token saved in `~/.config/rclone/rclone.conf`. A fresh
  `rclone config` does this;
- an **SFTP** remote uses a key without a passphrase. Otherwise, run
  `systemctl --user import-environment SSH_AUTH_SOCK` from your session;
- **notifications** need `DBUS_SESSION_BUS_ADDRESS` in the user manager. A graphical login provides
  it. Otherwise run `systemctl --user import-environment DBUS_SESSION_BUS_ADDRESS`. Without
  `notify-send` nothing else fails: you just get no banner. Then check
  `journalctl --user -u config-saver-sync.service`.

---

## Daily use

Normally there is nothing to do: the timer keeps the machines in step.

Each run prints one line per configuration that changed:

```text
zsh: applied 1, deleted 0, published 2
skipped (no local config): gradle
```

| Word | Meaning |
|---|---|
| `applied` | Files that changed on another machine and were written here. |
| `deleted` | Files that another machine deleted. They were removed here. |
| `published` | Files that changed here and were uploaded. |
| `skipped (no local config)` | The repository has this configuration, but this machine has no YAML for it. It is left alone. |
| `Nothing to sync.` | Both sides were already identical. |

**See what a run would do, without doing it:**

```bash
config-saver --sync-status
# zsh: ~/.zshrc (publish)
# codex-conversations: ~/.codex/sessions/2026/11/24/rollout-....jsonl (take remote)
```

**Before you leave with the laptop:**

1. **Close the agents** (Claude Code, Codex, opencode). Their SQLite databases would otherwise be
   copied mid-write.
2. Run `config-saver --sync` by hand instead of waiting up to 15 minutes for the timer.
3. On the laptop, run `config-saver --sync` before you resume a session.

**Two machines syncing at the same moment** is handled: the earliest publish wins and the other run
retries by itself, up to 3 times. In the worst case you get a conflict to resolve, never a silent
loss.

---

## Conflicts

A **conflict** is a path that changed on this machine *and* on another one since the last sync. This
includes one side editing a file that the other side deleted. When a run finds a conflict:

- it **writes nothing at all**, not even the non-conflicting files, and exits `11`;
- it prints every conflicting path and how to resolve it:

  ```text
  zsh: ~/.zshrc
  git: ~/.gitconfig
  Resolve with: config-saver --sync-resolve local|remote [--path P]
  ```

- the timer shows a notification, and keeps trying every 15 minutes (and failing the same way) until
  you resolve it.

Resolve with:

```bash
config-saver --sync-resolve local     # keep this machine's version of every conflicting path
config-saver --sync-resolve remote    # take the other machine's version of every conflicting path
config-saver --sync-resolve local --path ~/.zshrc --path ~/.gitconfig   # only these paths
```

`--path` accepts the paths exactly as the conflict list prints them, and you can repeat it. Paths you
do not name stay in conflict.

> **Tip.** If you are unsure which version is better, copy the file aside first
> (`cp ~/.zshrc ~/.zshrc.mine`), resolve with `remote`, then merge the two by hand. The next sync
> publishes the merged version.

A machine that has been **away for more than about 4 weeks** may get conflicts on its first run back,
even for files it did not edit. This is deliberate: past that point sync can no longer prove that its
old view of the repository is still valid, so it asks you instead of guessing.
[How sync works](Config-saver-sync-internals.md#machines-that-were-away) explains why.

---

## Moving to another provider

```bash
config-saver --sync-move "rclone:Proton:config-saver-sync" \
    --sync-option rclone.args="serve restic --stdio --tpslimit 5"
```

`--sync-move` does the following:

1. It copies every sync snapshot to the new repository. When the new repository does not exist yet,
   it creates it with the same chunker parameters, so deduplication carries over.
2. It checks that every head can be read back from the new repository.
3. Only then does it rewrite `sync.json`.

If the move is interrupted, the old repository stays in use. Run the same command again to finish it.

Run it on **every** machine. The second and later runs skip the snapshots that are already copied.
History and each machine's recorded state survive the move. The old repository is not deleted: remove
it yourself once every machine has moved.

---

## Pruning and repository size

You do not need to manage the size of the repository:

- after each successful sync, snapshots that no machine needs any more are **forgotten**. A replaced
  snapshot is kept for 24 hours first, and one that lost a race between machines is kept for 30 days;
- a sync run calls `restic prune` at most **once every 7 days**, which frees the space;
- `config-saver --sync-prune` forgets and prunes right now.

`restic prune` needs an exclusive lock on the repository. If another machine is uploading at that
moment, the prune fails: that run exits `10`, even though its sync itself succeeded. Timed runs then
leave prune alone for 24 hours. `--sync-prune` always tries.

---

## Rotating the age key

The repository password is derived from the age identity, so a new identity means re-keying the
repository. Keep the old identity file, then on **one** machine:

```bash
# Derive the password exactly like config-saver does. It strips surrounding whitespace, including a
# Windows \r.
derive() { printf 'config-saver-sync-v1\0%s' "$(sed -n 's/^[[:space:]]*\(AGE-SECRET-KEY-.*[^[:space:]]\)[[:space:]]*$/\1/p' "$1" | head -n 1)" | sha256sum | cut -d' ' -f1; }

umask 077; d=$(mktemp -d)        # a private directory: the file holds the new password
derive new-key.txt > "$d/new-pw"
RESTIC_PASSWORD="$(derive old-key.txt)" restic -r <sync repository URL> key passwd --new-password-file "$d/new-pw"
rm -rf "$d"
```

`key passwd` adds the new key and removes the old one. Add `-o key=value` for every `--sync-option` you
used, for example `-o rclone.args=...`. Then install the new identity on every other machine. The
[README](https://github.com/amt911/config-saver#sync-between-machines) also shows the step-by-step
version (`key list` / `key add` / `key remove`).

---

## Limits

- Definitions stay per machine: each machine's YAML is the only source of what *it* syncs.
- Empty directories are not synced.
- Symlinks are synced as links, with their target text unchanged. An absolute link into another
  user's home dangles on a machine whose home differs.
- A path that exists but **cannot be read**, or a root-owned path that is skipped, makes the run fail
  with exit `10` before anything is written. It is never published as a deletion.
- At most `sync_max_mb` MiB per configuration (default 50).
- Files that change while a run reads them (live databases) can be caught mid-write: close the
  program first.
- The repository grows between prunes, because old versions stay until they are forgotten and pruned.

---

## Command reference

| Command | What it does |
|---|---|
| `--sync-init URL [--sync-option K=V ...]` | Create or join the repository. Prints `created` or `joined`. The same URL again keeps the stored options unless you pass new ones. A different URL is refused (exit `6`): use `--sync-move`. |
| `--sync` | Sync every `sync: true` configuration. |
| `--sync-status` | Show per path what `--sync` would do. Changes nothing. Exits `11` if there are conflicts. |
| `--sync-resolve local\|remote [--path P ...]` | Settle conflicts by keeping this machine's version or taking the other's. |
| `--sync-move URL [--sync-option K=V ...]` | Copy everything to a new repository and switch to it. |
| `--sync-prune` | Forget unneeded snapshots and run `restic prune` now. |
| `--identity FILE` | Use this age identity instead of `~/.config/age/key.txt`. |

Exit codes: `0` done, `2` not set up or a tool is missing, `3` invalid configuration (including over
`sync_max_mb`), `6` invalid combination of options, `10` repository unreachable or I/O error, `11`
conflicts. Details are in [Sync troubleshooting](Config-saver-sync-troubleshooting.md).

# config-saver sync: troubleshooting

Start with these two commands. They answer most questions:

```bash
config-saver --sync-status                       # what a sync would do now, and any conflicts
journalctl --user -u config-saver-sync.service   # what the timer's runs printed
```

See [Sync between machines](Config-saver-sync.md) for setup and daily use, and
[How sync works](Config-saver-sync-internals.md) for the design.

---

## Exit codes

| Code | Meaning | What to do |
|---:|---|---|
| `0` | Done (or `Nothing to sync.`) | Nothing to do. |
| `2` | Sync is not set up, the age identity is missing or unreadable, or `restic` / `age` / `rclone` is not installed | See [Exit 2](#exit-2-not-set-up-or-a-tool-is-missing). |
| `3` | A configuration is invalid: over its `sync_max_mb`, declares something under `/etc/config-saver/configs`, or the sync state or `sync.json` is unreadable | See [Exit 3](#exit-3-invalid-configuration-or-state). |
| `6` | An invalid combination of options | Read the message. For example, `--sync-init` with a different URL needs `--sync-move`. |
| `10` | The repository is unreachable, an I/O error, a path that cannot be read, or the publish lost every retry | See [Exit 10](#exit-10-repository-unreachable-or-io-error). |
| `11` | Conflicts: the same path changed on two machines. Nothing was written | Resolve them: see [Conflicts](Config-saver-sync.md#conflicts). |

---

## Exit 2: not set up or a tool is missing

**`sync is not set up: run config-saver --sync-init <restic repository, ...> (missing .../sync.json)`**

This machine has never run `--sync-init`. Run it with the repository URL the other machines use (it
prints `joined`). On the first machine it prints `created`.

**`sync needs restic, rclone installed and on PATH`**

Install the missing tools. `rclone` is only needed when the URL starts with `rclone:`. Under the
timer, `PATH` is systemd's, so a tool in `~/.local/bin` may not be found. Install it system-wide or
add the directory to the unit's environment.

**`cannot read the age identity ...` / `no AGE-SECRET-KEY-... line in the age identity ...`**

Copy the shared age identity to `~/.config/age/key.txt`, or pass `--identity FILE`. It must be the
**same** identity on every machine, because the repository password is derived from it.

---

## Exit 3: invalid configuration or state

**`sync configuration too large: N bytes over the 50 MiB limit (roots: [...])`**

Raise `sync_max_mb` in that YAML, for example `sync_max_mb: 4096` for agent sessions, on **every**
machine that syncs it. The default limit is 50 MiB per configuration.

**`a sync configuration cannot declare '/etc/config-saver/configs...'`**

The system configuration directory is policy, not data. Remove that path from the `sync: true`
YAML.

**`the sync remote file .../sync.json ...`**

The file was edited by hand and no longer has exactly `repository` (a string) and `options` (a list
of strings). Fix it, or delete it and run `--sync-init` again with the same URL.

**`sync snapshot ... has no valid gen: tag`**

The repository holds snapshots from a development build before 3.7.0. Start a fresh repository with
`--sync-init NEWURL` on a clean `sync.json`, or move to one with `--sync-move`.

---

## Exit 10: repository unreachable or I/O error

**Only the timer fails; a run by hand works.** The timer has no interactive session:

- **rclone:** the token must be saved in `~/.config/rclone/rclone.conf`. Run `rclone lsd "<remote>:"`
  from a terminal. If it asks to log in, run `rclone config reconnect "<remote>:"`.
- **SFTP:** use a key without a passphrase, or run
  `systemctl --user import-environment SSH_AUTH_SOCK` after you log in.

**Proton Drive errors or timeouts.** Proton rate-limits requests. Keep
`--sync-option rclone.args="serve restic --stdio --tpslimit 5"`, or lower the limit further.

**`another machine kept publishing, or every upload took too long to count; try again`**

Three publishes in a row did not count. Either other machines kept publishing at the same moment, or
each upload took more than 6 hours. Run `config-saver --sync` again. The data is already in the
repository, so the next upload is quick.

**A path cannot be read.** A file in a synced configuration is unreadable (permissions), or it is
root-owned and skipped. The run stops before writing anything, so that the path is never published
as a deletion. Fix its permissions, or remove it from the YAML.

**`restic prune` failed after a successful sync.** Another machine held the repository lock. The sync
itself is saved. Timed runs leave prune alone for 24 hours; `config-saver --sync-prune` retries now.

**A stale lock.** If a machine crashed mid-run, restic may report the repository as locked. Make sure
no machine is syncing, then remove the stale lock:

```bash
RESTIC_PASSWORD="$(derive ~/.config/age/key.txt)" restic -r <sync repository URL> unlock
```

`derive` is the shell function from [Rotating the age key](Config-saver-sync.md#rotating-the-age-key).
Add `-o key=value` for each `--sync-option`.

---

## Conflicts that keep coming back

**The same file conflicts on every run.** Each run fails the same way until you run
`--sync-resolve`. The timer also notifies every 15 minutes.

**Files conflict right after joining a machine.** The new machine already had its own copies.
Resolve once with `--sync-resolve remote`, or `local` for paths whose copy on this machine is the one
to keep.

**Many unexpected conflicts after a long time away.** A machine that has not synced for more than
about four weeks cannot prove that its old view is still valid, so it asks instead of merging. Check
the list with `--sync-status`, then resolve. Usually you want `remote` for files you did not touch
while away.

**A file always looks changed.** Check that `normalize_content` is the same in that YAML on every
machine.

---

## No notifications

- Install `libnotify`, which provides `notify-send`.
- The user manager needs your session bus:
  `systemctl --user import-environment DBUS_SESSION_BUS_ADDRESS`. A graphical login usually sets it.
- `systemctl --user list-timers config-saver-sync.timer` shows whether the timer runs at all.
- The repository has `scripts/check-sync-notify.sh`, which checks the failure-notification wiring
  against real systemd.

---

## Where sync keeps its files

| File | Default path | Safe to delete? |
|---|---|---|
| Repository URL and options | `~/.config/config-saver/sync.json` | Yes. Run `--sync-init` with the same URL again. |
| State: base, heads, hash cache | `~/.local/state/config-saver/sync-state.json` | Only as a last resort. The next run treats every difference as new, which can mean conflicts. |
| Manifest cache | `~/.local/state/config-saver/sync-manifests.json` | Yes. It is rebuilt on the next run. |
| Lock | `~/.local/state/config-saver/sync.lock` | Only when no sync is running. |
| Staging | `~/.local/share/config-saver/sync/.staging` | Yes, when no sync is running. |

The `XDG_CONFIG_HOME`, `XDG_STATE_HOME` and `XDG_DATA_HOME` variables move these paths.

# config-saver sync: how it works

This page explains the design of `config-saver --sync`. It covers what is stored, how a run decides
what to do, and why it never loses an edit silently. You do not need it to *use* sync: see
[config-saver sync between machines](Config-saver-sync.md) for that.

The one rule the design is built around:

> **No machine's edit is ever overwritten or deleted without you saying so.** When sync cannot prove
> that a change is safe, it reports a conflict (exit `11`) or retries. It never guesses.

---

## Contents

1. [The pieces](#the-pieces)
2. [What a run does](#what-a-run-does)
3. [The three-way decision](#the-three-way-decision)
4. [Snapshots and manifests](#snapshots-and-manifests)
5. [History: parents, generations and forks](#history-parents-generations-and-forks)
6. [Publishing safely](#publishing-safely)
7. [Forgetting old snapshots](#forgetting-old-snapshots)
8. [Machines that were away](#machines-that-were-away)
9. [Assumptions behind the guarantees](#assumptions-behind-the-guarantees)
10. [Security](#security)
11. [Speed](#speed)
12. [How it is tested](#how-it-is-tested)

---

## The pieces

| Piece | Where | What it holds |
|---|---|---|
| Repository | anywhere restic reaches | encrypted snapshots, one series per configuration |
| `sync.json` | `~/.config/config-saver/` (`0600`) | the repository URL and its options |
| State | `~/.local/state/config-saver/sync-state.json` | for each configuration: the head this machine last synced (its **base**), when that was recorded, generations, parents, and the hash cache |
| Manifest cache | `~/.local/state/config-saver/sync-manifests.json` (`0600`) | manifests already read, by snapshot id |
| Lock | `~/.local/state/config-saver/sync.lock` | one sync per machine at a time |
| Staging | `~/.local/share/config-saver/sync/.staging` (`0700`) | files downloaded and checked before they are moved into place |

The `XDG_*` variables move these as usual. Nothing on the remote is in the clear: restic encrypts
everything, file names included.

---

## What a run does

1. **Lock.** The run takes the machine-local lock. A second run on the same machine fails fast.
2. **Scan.** It fingerprints every local file of each `sync: true` configuration: the sha256 of the
   content, the mode, and the target for a symlink. An unchanged file reuses its cached hash.
3. **List.** It lists the repository's sync snapshots and works out the current **head** of each
   configuration (see [History](#history-parents-generations-and-forks)).
4. **Read the head and the base.** It reads the head's manifest, the full file list of that version.
   The **base** is the file list this machine recorded in its state at its last sync. When that
   record cannot be trusted, the base comes from an older snapshot instead (see
   [Machines that were away](#machines-that-were-away)).
5. **Decide.** For every path it runs the [three-way decision](#the-three-way-decision). If any path
   is a conflict, the run stops here. It **writes nothing** and exits `11`.
6. **Fetch and check.** It downloads the files to take into a private staging directory. Every
   archive member name is checked by the same extraction guard as `--decompress`, and the whole
   batch is checked **before** a single file is moved into place.
7. **Apply.** It moves the staged files into place and applies the deletions. These all come from the
   current head, so they are correct whatever happens next.
8. **Publish.** It uploads one new snapshot per configuration with local changes, then confirms that
   the publish won (see [Publishing safely](#publishing-safely)). If it did not win, it goes back to
   step 3, at most 3 times, then exits `10`.
9. **Record.** It saves the new base and head in the state file. Only after that does it forget the
   snapshots nobody needs, and run the weekly prune.

---

## The three-way decision

For each path there are three versions:

- **base (B):** what the path was at this machine's last sync;
- **local (L):** what it is on disk now;
- **remote (R):** what it is in the current head.

| Situation | Action |
|---|---|
| L = R | nothing to do (also when both sides made the *same* change) |
| L = B, R changed | **take remote**: write R here, or delete the file here if R deleted it |
| R = B, L changed | **publish**: upload L (or record the deletion) |
| L ≠ B and R ≠ B and L ≠ R | **conflict**: stop, write nothing, ask |

"Changed" includes content, mode and symlink target, and also deletion. So *edited here, deleted
there* is a conflict too.

**Scope.** A path counts only if *this* machine's YAML declares it. A path missing from the remote is
a deletion only if the machine that made the head declared that path. If it did not declare it, the
other machine simply never managed the path, and nothing is deleted. That is why a narrower YAML on
one machine never deletes files that a wider YAML manages on another.

**Fail closed.** A file that exists but cannot be read, or a root-owned path that is skipped, is not
treated as absent. Otherwise it would be published as a deletion. The run fails with exit `10`
instead.

---

## Snapshots and manifests

Each publish is **one restic snapshot per configuration**. It holds:

- a tar with **only the files that changed**: a delta, not a full copy;
- a **manifest**, `.config-saver-sync-manifest.json`, at the start of that tar. It lists *every* path
  of the configuration with its fingerprint and the id of the snapshot that holds its bytes. A file
  changed in this snapshot points to the snapshot itself (`@`). An unchanged file points to the older
  snapshot that last stored it.

So restoring a file reads its entry in the head's manifest and fetches the bytes from whichever
snapshot holds them. Restic deduplicates data across all snapshots, so unchanged data is never
uploaded twice anyway.

Snapshot tags carry the history:

| Tag | Meaning |
|---|---|
| `sync` | marks a sync snapshot (the backup store never shares the repository) |
| `parent:<id>` | the head this snapshot was built on (`parent:none` for the first one) |
| `gen:<n>` | its **generation**: 0 for the first, then parent + 1 |
| `host:<name>` | the machine that published it (informational) |

Snapshot ids are **canonical**. A snapshot copied by `--sync-move` gets a new id from restic, but
keeps its original one in restic's `original` field. Sync always reasons with the original id, so
parents, manifests and every machine's recorded base stay valid after a move.

---

## History: parents, generations and forks

Normally the history is a straight line. Each publish builds on the current head:

```text
gen:  0      1      2      3
      P ──▶ Q ──▶ R ──▶ S        head = S
```

**A fork** happens when two machines publish on the same parent at almost the same moment:

```text
      P ──▶ Q ──▶ R   (desktop, published 10:00:03)
             └──▶ R'  (laptop,  published 10:00:05)
```

Snapshots that share a parent are **siblings**. In every sibling group, the **earliest** one (by
time, then by id) wins, and the others are **fork losers**. A loser's descendants lose too. The
laptop sees that it lost, drops R', and redoes its run on top of R. Its own changes are then either
published cleanly or reported as conflicts.

**The head** is the snapshot of the **highest generation** among the snapshots that are not losers.

Generations exist because old snapshots get forgotten (see below), which leaves **gaps** in the
chain. With a parent walk alone, a gap cuts the history in pieces, and the oldest piece could look
like the head. Generations make the answer independent of gaps: the newest line always has the
highest number. A sibling group is recognised by the shared `parent:` tag even when that parent was
already forgotten.

---

## Publishing safely

After uploading, a machine lists the repository again and keeps its new snapshot only if **all** of
these hold:

1. **It won.** The head is the new snapshot, or the head descends from it, because another machine
   already built on top. Not losing a fork is *not* enough. A machine that stalled for a long time
   (a laptop suspended mid-run) can find that its parent was forgotten meanwhile. Its snapshot is
   then alone in its sibling group, while the real head is a higher generation on another line.
   Counting that as a win would make the next run overwrite its own edits.
2. **Nothing it names is gone.** Every snapshot its manifest points to must still be listed. Another
   machine may have forgotten one while this machine was uploading.
3. **It was quick.** A snapshot is dated when its upload **starts**. An upload that took more than
   **6 hours** is dropped and redone. Restic already holds the data, so the redo is quick. This keeps
   every surviving snapshot's date close to the moment other machines could first see it, and fork
   order and the retention windows below rely on that.

If any check fails, the snapshot is forgotten and the run starts again from the new listing. After 3
attempts it exits `10`, and the next timer run tries again. Nothing is recorded as synced until a
publish passes all three checks.

---

## Forgetting old snapshots

Without forgetting, the repository would only grow. After a successful run, a machine forgets
snapshots of the configurations **it** syncs, following rules chosen so that forgetting **never
changes which snapshot is the head** and never removes something a machine may still need:

| Snapshot | Kept while |
|---|---|
| A head, or any snapshot a head's manifest points to | always |
| A superseded snapshot (replaced by a newer generation, not referenced) | until its successor is **24 hours** old, so a slower machine that read it as head can still publish on top of it |
| A fork loser and everything built on it | **30 days**, as one unit. A unit that a head references stays whole |
| A fork winner | as long as a losing sibling is still listed (it is the evidence that the fork happened) |
| The nearest non-loser ancestor of a kept loser | as long as that loser is kept (it is the base a machine that was away needs) |

`restic prune` then frees the space, at most once a week from a timed run, or right away with
`--sync-prune`. A failed prune is not retried by timed runs for 24 hours.

---

## Machines that were away

A laptop that slept for two weeks recorded some head H as its base. When it comes back, H may be:

- **still listed and on the winning line**: the normal case. H is the base, and the three-way
  decision runs against the current head;
- **listed but a fork loser**: the base becomes H's nearest ancestor on the winning line. If there is
  none, the base is empty. An empty base turns every difference into a conflict, never into a
  silent overwrite;
- **gone from the listing**: either it was superseded and forgotten (harmless), or it lost a fork
  and its unit was forgotten after 30 days (then its edits were never part of the head).

The last case needs care. The machine treats a missing H as *superseded*, and keeps it as its base,
only if:

- the current head has a **higher generation** than H, **and**
- the machine recorded H **less than 29 days ago**. That is the 30-day loser retention minus the
  24-hour grace.

Within 29 days a lost fork would still be listed, so "missing" can only mean "superseded". Past 29
days the two cases look the same. The machine then falls back to the nearest ancestor, or to an empty
base, and you get conflicts to resolve instead of a merge. That is why a machine away **for more than
about four weeks** may report conflicts on its first run back.

---

## Assumptions behind the guarantees

The time windows above are only sound if:

- **machine clocks agree to within a few hours.** Snapshot dates come from the machine that
  publishes. A clock that is days off can cause spurious conflicts, or in the worst case make a
  machine trust a base it should not;
- **uploads that count finish within 6 hours.** This one is enforced: a slower upload is dropped and
  redone.

Under those assumptions, a property test checks the core claim against random histories. Those
histories include forks, clock skew and crashes. The claim: after any amount of forgetting, the head
is the same as the head of the full history.

---

## Security

- **Encryption.** Restic encrypts everything. The storage provider sees neither file names nor
  contents.
- **Password.** It is the hex sha256 of `config-saver-sync-v1`, a NUL byte and the
  `AGE-SECRET-KEY-` line of your age identity, so every machine that holds the identity derives the
  same password. It reaches restic only through the environment, never on a command line where other
  users could read it.
- **Untrusted content.** Everything read from the repository is treated as hostile until it is
  checked:
  - manifests are parsed strictly (unknown keys, duplicate keys and malformed paths are refused);
  - archive member names go through the same containment checks as `--decompress`, which refuses
    `../`, absolute names, links that escape the target, and device nodes;
  - a whole batch is checked before any file is written.
- **Permissions.** `sync.json`, the state and the manifest cache are `0600`. Staging is `0700`.
  Restored files keep their recorded modes.

---

## Speed

- **Hash cache.** A file is hashed again only if its size, mtime, ctime, inode or
  `normalize_content` setting changed. A change of `HOME` drops the cache.
- **Manifest streaming and cache.** The manifest sits at the start of the snapshot's tar. Sync stops
  restic as soon as the manifest has been read, instead of downloading the whole snapshot. Manifests
  are then cached by snapshot id, since snapshots never change. A run in which nothing changed only
  lists the repository.
- **Deltas and deduplication.** A snapshot carries only the changed files, and restic stores each
  chunk once across all snapshots.

---

## How it is tested

- **Pure logic.** The three-way decision, manifests, head selection and the forgetting rules are
  pure functions, with one test per branch. A Hypothesis property test simulates random
  multi-machine histories and checks that forgetting never changes the head. A mutation-testing
  gate (93 % or more of mutants killed) checks that the tests actually catch wrong code.
- **Engine.** Two simulated machines share a real local restic repository in a temporary directory.
  Tests cover forks, winner flips, crashes between publish and save, slow machines, publishers that
  stalled, forgotten ancestors, machines idle for just under and just over the 29-day limit, hostile
  snapshots and moves.
- **End to end.** The real `config-saver` command runs as a subprocess for two machines, through
  init, sync, conflict and resolve.

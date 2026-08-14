# Keeping a config in Git without a ritual — design

*2026-08-14*

## The problem

A dasik config is meant to live in a (private) Git repository and be kept in
step with the machine it describes. Today that costs five steps, and the first
one does not work:

| # | Step | State |
| --- | --- | --- |
| 1 | `sudo dasik sync host.json` | **refused** when the config uses any `$include` — "fold the changes back by hand" |
| 2 | `dasik check` the capture | manual |
| 3 | `git commit && push` | manual |
| 4 | `$HOME` archive: `config-saver --compress` → `age` → `gh release upload` | a separate flow |
| 5 | Bump `package_sources[*].ref` / `config_saver.source.ref` | manual, and the one that gets forgotten |

Step 1 is a contradiction the project shipped: `$include_line` is the
documented way to keep a password hash out of the committed config
(`includes.py` says so in its module docstring), and `uses_includes` is
recursive, so **using it disables `sync` on that config forever**.

The goal: one command, `sudo dasik save host.json`, with the whole cycle behind
two opt-in flags.

## A — `sync` writes back through `$include`

### Why it refuses today

`_cmd_sync` (`dasik/__main__.py:436`) aborts because `ConfigWriter.write`
emits **one** document: every directive would be replaced by its resolved value
and the split silently undone. The check is correct; what is missing is a
writer that can put a value back where it came from.

### The writeback

New module `dasik/lib/json_parser/writeback.py`, one entry point:

```python
def write_back(root_path: Path, new_config: dict) -> list[Path]:
    """Persist new_config through the directive tree rooted at root_path.
    Returns every file written, root first."""
```

It walks the **raw** tree (directives intact, re-read from disk) and the
captured tree in parallel:

| Raw node | Captured value | Action |
| --- | --- | --- |
| directive, resolves to the same value | unchanged | leave the directive untouched — the file is not rewritten at all |
| `{"$include": "f.json"}` | changed | recurse into `f.json`: its own directives are preserved the same way, and the residue is written there |
| `{"$include_text": "f.conf"}` | changed | write the string to `f.conf` **verbatim** |
| `{"$include_line": "secrets/x"}` | changed | write the string + `"\n"` to `secrets/x` |
| `{"$concat": [a, b, c]}` | changed | see below |
| plain value | changed | write in the file currently being emitted |
| key absent from the raw tree | present | write into the **root** file |

Two invariants make this safe to run unattended:

- **A file whose content did not change is not opened for writing.** `sync` on a
  converged machine touches nothing, which is what makes it safe to put in a
  `save` verb that commits afterwards.
- **Every path stays inside the config's own directory** — `_resolve_path`
  already refuses absolute paths and `..`, and the writer resolves through the
  same function rather than re-implementing it.

### `$concat`

`{"$concat": [{"$include": "packages/base.json"}, …]}` is how a 172-entry
package list stays readable. When the captured list differs there is no way to
know which theme a *new* package belongs to, so the rule is the predictable one
rather than the clever one:

- entries that already exist stay in the member they are in (order preserved);
- entries that disappeared are removed from whichever member holds them;
- **new entries are appended to the last member**;
- a member that is a literal list (not an include) is written in place in its
  own file.

`save` prints where they landed (`+3 packages → packages/dev.json`) so moving
them is a deliberate edit, not archaeology.

### Secrets keep working — by design, not by luck

`users_action.import_state` captures `hashed_password` from the target's
`/etc/shadow` (`users_action.py:187`). On a converged machine that equals the
value behind `$include_line`, so the directive compares equal and the secret
file is never rewritten. When the password *did* change on the machine, the new
hash is written to the **secret file** (gitignored) instead of being inlined
into the committed config. Both directions are right.

### Acceptance

- a config with `$include`, `$include_text`, `$include_line` and `$concat`
  round-trips: `sync` → `check` → `plan` is silent, and `git status` in the
  config repo shows only files whose content genuinely changed;
- a converged machine writes **zero** files;
- a config with no directives behaves exactly as today (`ConfigWriter.write`);
- an unreadable/absent included file fails loudly before anything is written —
  a partial writeback across N files is not acceptable, so the plan is computed
  fully, then applied.

## B — `dasik save`

```text
sudo dasik save <config> [-m MSG] [--no-push] [--home] [--refs]
```

`sync` → `check` → `git add` the written files → `commit` → `push`. Nothing
else; if `sync` wrote nothing, it says so and exits 0 without an empty commit.

### Privileges

`sync` needs root (it reads `/etc/shadow`, runs `cryptsetup luksDump`,
`pacman -Qqm`). The commit is *not* root's: it belongs to the invoking user,
whose `gh`/SSH credentials and `user.email` are the ones that work.

- the capture runs with the privileges dasik already has;
- every file the writeback touched is `chown`ed back to the invoking user
  (`SUDO_USER`), fixing a real defect that exists today: `sudo dasik sync` in a
  Git repo leaves the config `root:root`;
- `git` runs as `SUDO_USER` via the same positional-argv `su` pattern the AUR
  builder uses (`config_saver_action._restore_one`) — never a shell string.

Running as plain root with no `SUDO_USER` is an error with a readable message,
not a commit authored by root.

### Ignored files are never staged

The writeback can legitimately rewrite a file Git is told to ignore: that is
exactly what `secrets/hashed-password` is. `save` stages only files Git does not
ignore (`git check-ignore`), **never** `git add -f`, and reports the rest:

```text
  wrote   secrets/hashed-password  (gitignored, not staged)
```

Staging it would commit a password hash to a repository on the strength of a
convenience flag. The report exists so a file that is ignored *by accident*
still surfaces.

### Zero configuration

Nothing is added to the schema. A config file describes the *system*; which Git
remote it is pushed to does not. Everything is derived:

| Thing | Where it comes from |
| --- | --- |
| repository | the Git work tree containing the config file (`git -C <dir> rev-parse`) |
| remote | its `origin` |
| author | `SUDO_USER`'s own `git config` |
| message | `-m`, else `<hostname>: sync <ISO date>` |

Not a Git repository → error naming the directory. No `origin` → commits and
says it did not push.

### Acceptance

- on a converged machine: "nothing to sync", no commit, exit 0;
- after a change: exactly one commit, containing exactly the files the
  writeback wrote, authored by the invoking user, config still owned by them;
- `--no-push` commits and does not reach the network;
- `check` runs **after** `sync` and a failure aborts before the commit — a
  capture the tool would reject must never be committed.

## C — `--home` and `--refs`

Both opt-in. Plain `save` never touches releases or SHAs.

### `--home`

The `$HOME` payload that config-saver produces cannot live in the config file,
and it should not live in Git history either (browser profiles, gigabytes,
binary). It travels as a release asset of the same private repository:

1. `config-saver --export-config <name> --output <tmp>` — the latest archive the
   timer already produced (no new compression run). `<name>` is the single
   entry in `config_saver.configs`; with several declared, `--home NAME` says
   which, and omitting it is an error that lists them rather than guessing;
2. `age -p` — passphrase, prompted; the archive is your `$HOME`, and
   config-saver's own README says a plain `.tar.gz` is compressed, not
   encrypted;
3. `gh release upload home-<hostname> --clobber` on `origin`.

A fixed tag plus `--clobber` means there is always exactly one archive and it
is always the latest — no pile of releases nobody prunes. The config's
`config_saver.restore[].archive` names where it lands on the target; `--home`
only publishes it.

### `--refs`

For each `package_sources[*].url` and `config_saver.source.url`, resolve the
remote default branch with `git ls-remote <url> HEAD`, update `ref` when it
moved, and print what changed:

```text
dasik-aur         3fcec13 → 8a1f0c2
config-saver-aur  941b647 (unchanged)
```

This is step 5 of the table, the one that silently installs a stale version of
your own tooling. The updated `ref` goes through the same writeback, so a
`package_sources` block that lives in an included file is edited *there*.

### Acceptance

- `--home` with no config-saver archive: a named error, no release touched;
- `--refs` with every remote unmoved: no file written, no commit;
- both are inert without their flag.

## D — config-saver stops shipping configs into `/etc`

Found while designing the above, and it breaks a fresh machine on its own.

`default_config_dir()` (config-saver `cli.py:121`) returns the **first
candidate that exists**:

1. `/etc/config-saver/configs`
2. `<sys.prefix>/share/config-saver/configs`
3. `~/.config/config-saver/configs.d`

The AUR package installs four example YAMLs into candidate 1, so candidate 1
always exists and always wins. `find_config_files` then globs
`("*.yaml", "*.yml", "*.json")` over the whole directory. On a machine dasik
just installed, the timer therefore backs up `default-config`, `etc-files`,
`wallpapers` and `zsh` — the package's examples — rather than what the user
declared. Candidate 3 is unreachable by construction.

It also makes dasik's capture lossy on purpose: `_discover_configs` skips
pacman-owned files (correctly — re-encoding the distro's defaults into the
user's config would be wrong), so those four are invisible to `sync`.

**Fix, in `config-saver-aur`'s PKGBUILD:**

```diff
-    install -d "$pkgdir/etc/config-saver/configs"
-    cp -r configs/* "$pkgdir/etc/config-saver/configs/"
+    install -d "$pkgdir/usr/share/config-saver/configs"
+    cp -r configs/* "$pkgdir/usr/share/config-saver/configs/"
```

`sys.prefix` is `/usr` for a system install, so candidate 2 is exactly that
path: with no user configs the tool behaves as it does today, and the moment
dasik writes `/etc/config-saver/configs/*.json` the user's `/etc` wins outright.
Nothing in config-saver changes, `/etc` becomes the administrator's again, and
`pacman -Syu` stops reintroducing examples into it.

Verified on the author's machine: the four files are owned by `config-saver
3.1.1-1` and `pacman -Qkk` reports them unmodified, so moving them loses no
local edit.

**Optional upstream follow-up** (config-saver, 2 lines): pick the first
candidate that *contains* config files rather than the first that exists —
otherwise an empty `/etc/config-saver/configs` (a plausible intermediate state)
wins and compresses nothing instead of falling back.

## Order

A → B → C, one PR each; D is independent and lands in `config-saver-aur`.

A is the prerequisite: without it `save` would refuse every config that keeps
its secret in a file, which is the shape the documentation recommends.

## Out of scope

- Any `git` behaviour beyond add/commit/push of the config repo. dasik does not
  learn to resolve conflicts, rebase, or manage branches.
- Storing credentials. `save` uses the invoking user's existing Git/`gh` setup
  and stores nothing.
- Encrypting the config itself. The private repository is the boundary; the
  password hash is `/etc/shadow`-grade exposure, and the LUKS passphrase stays
  in a gitignored file.

# Config splitting and secrets

**This is what a config for a machine you actually keep looks like.** One file
is how you start; a directory is how it ends up, because a real config is
dominated by two things that do not belong inline — a long package list, and
verbatim file bodies (PAM snippets, udev rules, YAML documents) that would have
to be JSON-escaped onto one line.

Four directives and two directory trees fix that:

| Mechanism | For |
| --- | --- |
| [`$include`](#include--parsed-json) | a block in its own file |
| [`$include_text`](#include_text--raw-text) · [`$include_line`](#include_line--the-first-line-stripped) | one file body · one secret |
| [`$concat`](#concat--flatten-lists) | a package list split by theme |
| [`etc_tree`](#etc_tree--a-directory-that-mirrors-etc) | **every** `/etc` file, as a directory |
| [`home_tree`](#home_tree--the-same-for-home) | the same for users' homes |

`sync` writes back through all of them, so the shape survives a capture: values
return to the file they came from and file bodies land in the trees rather than
in the JSON.

```json
{
  "hostname": "split-example",
  "packages": { "$concat": [
    { "$include": "packages-base.json" },
    { "$include": "packages-desktop.json" }
  ]},
  "disks": { "$include": "disks.json" },
  "files": [
    { "path": "/etc/pam.d/sudo", "content": { "$include_text": "parts/pam-sudo" } }
  ],
  "users": [
    { "username": "andres",
      "hashed_password": { "$include_line": "secrets/hashed-password" } }
  ]
}
```

Working example in the repository: `config/split-example/`. Validate it with
`dasik check config/split-example/main.json`.

Source: `dasik/lib/json_parser/includes.py`.

---

## The four directives

### `$include` — parsed JSON

```json
"disks": { "$include": "disks.json" }
```

Replaced by the **parsed** contents of that file. Any JSON value: object, list,
string, number. This is how a section becomes a file.

### `$include_text` — raw text

```json
{ "path": "/etc/pam.d/sudo", "content": { "$include_text": "parts/pam-sudo" } }
```

Replaced by the file's contents as a **string, unparsed and verbatim** —
including the trailing newline, which a PAM or ini file needs. The snippet lives
in a real file your editor highlights, instead of
`"#%PAM-1.0\nauth sufficient …"`.

### `$include_line` — the first line, stripped

```json
"hashed_password": { "$include_line": "secrets/hashed-password" }
```

The file's **first line**, whitespace-stripped. This is the one for secrets, and
the reason it exists is subtle: `$include_text` is verbatim by design, so it
would yield `"$y$j9T$…\n"`, and `usermod -p '$y$…\n'` sets a hash **nobody can
log in with** while nothing complains. An empty file is an error rather than an
empty secret.

### `$concat` — flatten lists

```json
"packages": { "$concat": [
  { "$include": "packages-base.json" },
  { "$include": "packages-desktop.json" },
  ["one-off-package"]
]}
```

The lists inside it, flattened into one. A 172-entry package block becomes
base + desktop + dev, split by theme. Every member must resolve to a list.

---

## `etc_tree` — a directory that mirrors `/etc`

`$include_text` moves **one** body out of the JSON. A real machine's `/etc` is
not one body, so there is a shorthand for all of them at once:

```json
"etc_tree": "etc"
```

```text
config/
├── main.json
└── etc/
    ├── pam.d/sudo                    → /etc/pam.d/sudo
    ├── profile.d/dasik.sh            → /etc/profile.d/dasik.sh
    └── udev/rules.d/99-qudelix.rules → /etc/udev/rules.d/99-qudelix.rules
```

Every file under the tree becomes a [`files`](Configuration.md) entry whose
`path` is its position under `/etc`. Nothing else to declare, and the tree reads
like the `/etc` it produces — a reviewer recognises it without learning a
schema. It also covers what the snippet sections cannot: `/etc/pam.d` has no
section of its own, so today it *must* be a `files` entry.

The expansion happens in the loader, so `check`, `plan` and `apply` see ordinary
entries. **Deleting a file from the tree removes it from the machine** on the
next apply — the tree is a declaration, not a pile of leftovers.

### Modes

Git carries exactly one permission bit, so:

| The file | Becomes |
| --- | --- |
| executable in your working tree | `mode: "0755"` |
| anything else | no mode (the umask), as today |

Everything else is declared, and one case makes that non-negotiable: a WireGuard
or NetworkManager keyfile that is world-readable is **ignored in silence**.

```json
"etc_tree": "etc",
"etc_tree_modes": { "wireguard/wg0.conf": "0600" }
```

A mode naming a file the tree does not hold is an error, not a no-op — a typo
there is a secret left readable.

### Precedence

An explicit `files` entry **wins** over a tree file for the same path: the
config says it in the file you are reading. `preflight` warns, because two
declarations of one path is a smell even when the winner is well-defined.

### `sync` extracts into it

With a tree declared, a captured file under `/etc` is written **into the tree**
and the JSON keeps no body at all:

```text
sync captures /etc/pam.d/sudo   →  writes etc/pam.d/sudo, the JSON does not grow
```

That is the other half of the writeback below. Without it, a capture would undo
the split from the other direction — every PAM snippet back as an escaped
one-line string. Paths outside `/etc` stay inline.

### Refusals

The tree is read at load time, so a bad one fails before anything runs: a
**symlink** inside it (it would publish whatever it points at), a file that is
**not UTF-8 text** (a body is a string; a binary belongs in a package), a tree
outside the config's own directory, and a missing tree.

---

## `home_tree` — the same, for `$HOME`

A captured `home_files` entry has the same problem an `/etc` file had: a YAML
document with comments becomes one escaped line in JSON. `home_tree` is
`etc_tree` with one extra level, because a home file is addressed as **(user,
path)** rather than by absolute path — the machine decides where a home lives,
and dasik reads its `/etc/passwd` to find out.

```json
"home_tree": "common/home"
```

```text
common/home/
└── andres/                                        ← a USER name, not a path
    └── .config/config-saver/configs.d/zsh.yaml    → ~andres/.config/…/zsh.yaml
```

Everything else matches `etc_tree`: the executable bit becomes `0755`,
`home_tree_modes` declares the rest, an explicit `home_files` entry wins, and
`sync` **extracts into the tree** instead of inlining bodies. A file directly in
the tree root is an error — the first level is a user, so there is nobody to
own it.

What it is for, concretely: `sync` captures the config-saver documents under
`~/.config/config-saver/configs.d`, and with a tree declared they land in Git as
the YAML files they are, comments and all, instead of as JSON strings.

---

## Rules

| Rule | Why |
| --- | --- |
| A directive must be the **only key** in its object | otherwise half the object is a directive and half is data, and neither reader knows which |
| Paths are **relative to the file that names them** | a directory of fragments can be moved as a unit |
| No absolute paths, no `..` segments | a config may only pull in files at or below its own directory |
| Nested directives resolve against the **included file's** directory | a fragment can have its own fragments and still move as a unit |
| Cycles are detected and reported | `include cycle: main.json -> a.json -> main.json` |
| A missing or unreadable file is an error naming the path | |
| An included file that is not valid JSON is an error (`$include` only) | |

Resolution happens **before** the schema sees the config, so pydantic validates
the finished document, and error messages point at fields, not at directives.
`check`, `plan`, `apply` and `sync` all resolve identically.

---

## Secrets

Keep them in files that are gitignored, referenced with `$include_line`:

```text
config/laptop/
├── main.json            ← committed
├── packages-base.json   ← committed
├── disks.json           ← committed
└── secrets/             ← gitignored
    ├── hashed-password
    └── luks-passphrase
```

```json
"users": [{ "username": "andres",
            "hashed_password": { "$include_line": "secrets/hashed-password" } }],
```

and, inside a partition:

```json
"luks_password": { "$include_line": "secrets/luks-passphrase" }
```

The same file can be referenced from several places — both LUKS partitions
usually want the same passphrase.

```gitignore
config/*/secrets/
```

Things that are secret in a dasik config: `users[].hashed_password` (a hash, but
crackable), `disks[].partitions[].luks_password` (**plaintext**),
`wireguard.config_content` and any `files` entry holding a WireGuard or
NetworkManager keyfile (private keys).

Give secret files `"mode": "0600"` — NetworkManager and `wg-quick` **ignore**
world-readable keyfiles, silently.

> A `dasik-sync-*.log` records what was read back, secrets included. Do not
> commit run logs.

---

## `sync` writes back through the split

`sync` used to refuse a split config outright: the only writer emitted **one**
document, so syncing in place would have replaced every directive with its
resolved value — the split undone and every secret inlined into the file you
were keeping them out of. Since the writeback landed it does the right thing
instead:

```bash
sudo dasik sync main.json --target /
```

```text
Synced system reality into main.json (backup: main.json.bak).
  wrote main.json
  wrote packages.json
```

The rules, in order of how often they matter:

| What | Happens |
| --- | --- |
| a directive whose value **did not change** | left alone — **its file is not even opened** |
| `$include` whose value changed | written to *that* file, its own nested directives preserved |
| `$include_text` / `$include_line` changed | that file is rewritten (`_text` verbatim; `_line` replaces the first line and keeps whatever you wrote below it) |
| `$concat` gained entries | existing ones stay in their member, **new ones go to the last member** |
| a key no fragment declares | lands in the root |

**A converged machine writes nothing at all** — no file, no timestamp. That is
what makes `sync` safe to run from a script that commits afterwards.

**Your secret survives on purpose.** `hashed_password` is captured from the
target's `/etc/shadow`, so on a converged machine it equals what is behind
`{"$include_line": "secrets/hash"}` and the file is untouched. If you *changed*
the password on the machine, the new hash is written to the secret file — the
gitignored one — instead of being inlined into the config you commit.

Two values cannot live in a file and are written inline instead, because the
file would not read back as the same string: anything with a carriage return
(`Path.read_text` translates newlines), and, for `$include_line`, a value that
is empty, padded with whitespace, or contains any line break. dasik prefers an
ugly inline value to a wrong one.

The one thing it cannot guess: which member of a `$concat` a **new** entry
belongs to. It goes to the last one, and moving it is a normal edit.

---

## Several machines, one repository

The layout is not a matter of taste — one rule decides it. **`$include`
resolves relative to the file that names it and refuses `..`**: a config may
only pull in files at or below its own directory.

```text
Error: include path '../common/pkgs.json' must not contain '..' — a config may
only pull in files at or below its own directory
```

So a directory per machine *cannot* reach `../common/`. The config that pulls
from both its own fragments and the shared ones has to sit **above** them,
which means the machine configs live at the repository root:

```text
dasik-personal-config/
├── thinkpad.json             ← one config per machine, at the root
├── desktop.json
├── common/                   ← identical on every machine
│   ├── locales.json
│   └── packages-base.json
├── thinkpad/                 ← only this machine
│   ├── disks.json  packages.json  users.json …
│   ├── etc/                  ← its /etc tree
│   │   └── pam.d/sudo
│   └── secrets/              ← gitignored
└── desktop/
```

```json
{
  "hostname": "thinkpad",
  "locales": { "$include": "common/locales.json" },
  "users":   { "$include": "thinkpad/users.json" },
  "packages": { "$concat": [
    { "$include": "common/packages-base.json" },
    { "$include": "thinkpad/packages.json" }
  ]},
  "etc_tree": "thinkpad/etc"
}
```

That config assembles to the shared packages **followed by** the machine's own,
`/etc/pam.d/sudo` from `thinkpad/etc/`, and everything else per machine.

### The part that surprises people

A fragment resolves its own includes against **its own** directory, not the
root's. So `thinkpad/users.json` keeps saying:

```json
"hashed_password": { "$include_line": "secrets/hashed-password" }
```

— not `thinkpad/secrets/…`. Fragments move as a unit, which is what makes a
machine directory copy-able into another repository.

### What belongs in `common/`

Only what you want **identical** everywhere, because that is what it means: a
change there changes every machine on its next apply. Locales, keyboard layout,
a package base, the config-saver documents. Not disks, not users, not hostname,
and not the packages that exist for one machine's hardware.

Splitting packages is worth doing early — `$concat` is exactly for it — but
splitting them *wrong* is worse than not splitting: a "base" that quietly
carries `thinkpad-acpi-utils` will install it on the desktop too.

### Adding a machine

```bash
# on the new machine
sudo dasik sync newhost.json --target /    # captures it from {}
dasik check newhost.json
```

Then move machine-specific blocks into `newhost/`, point the includes at the
new paths, set `etc_tree` to `newhost/etc`, and re-run `dasik check`. Each
machine keeps its own `secrets/` (gitignored) and its own generations; the
repository is shared, the state is not.

`dasik save` works from any of them: it commits to the same repository, as the
user who ran it, without staging anything Git is told to ignore.

---

## When to split

| Signal | Split |
| --- | --- |
| the package list scrolls | `$concat` by theme |
| a `files` entry is an escaped multi-line body | `$include_text` |
| a password, hash or passphrase is in the file | `$include_line` + gitignore |
| two machines share 80% of a config | shared fragments, two thin `main.json` |
| the disks block dwarfs everything else | `$include` |

A config that fits on a screen does not need any of this.

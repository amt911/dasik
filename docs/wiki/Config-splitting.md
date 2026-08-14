# Config splitting and secrets

A real config is dominated by two things that do not belong inline: a long
package list, and verbatim file bodies (PAM snippets, udev rules, ini
fragments) that have to be JSON-escaped into one line. Four directives fix both.

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

## When to split

| Signal | Split |
| --- | --- |
| the package list scrolls | `$concat` by theme |
| a `files` entry is an escaped multi-line body | `$include_text` |
| a password, hash or passphrase is in the file | `$include_line` + gitignore |
| two machines share 80% of a config | shared fragments, two thin `main.json` |
| the disks block dwarfs everything else | `$include` |

A config that fits on a screen does not need any of this.

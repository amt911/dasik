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

## `sync` refuses a split config

```text
main.json is assembled from includes ($include/$include_text/$concat) and sync
would flatten it into a single file.
Sync into a scratch copy instead and fold the changes back by hand:
  cp main.json /tmp/synced.json && dasik sync /tmp/synced.json
```

`ConfigWriter` emits **one** document. Syncing in place would replace every
directive with its resolved value: the split silently undone, and every secret
inlined into the file you were keeping them out of. So dasik refuses, and the
workflow is:

```bash
cp main.json /tmp/synced.json
sudo dasik sync /tmp/synced.json --target /
diff <(jq -S . /tmp/synced.json) <(jq -S . <(python - <<'EOF'
# or simply read the diff by eye
EOF
))
# fold the interesting changes back into the right fragment by hand
dasik check main.json
```

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

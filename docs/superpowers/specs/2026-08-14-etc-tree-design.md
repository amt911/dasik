# `etc_tree` — declare files as files — design

*2026-08-14*

## The problem

A managed file's body lives in the JSON. `$include_text` already moves it out,
but one entry at a time and with a slug for a name:

```json
"files": [
  {"path": "/etc/pam.d/sudo", "content": {"$include_text": "files/etc-pam.d-sudo"}}
]
```

Two files and it is fine; a real machine's `/etc` is not two files. And `sync`
makes it worse from the other direction: it captures bodies **inline**, so a
capture turns a tidy split config into one with PAM snippets escaped into single
lines.

## The shape

One new key: a directory mirroring `/etc`.

```text
config/
├── main.json          "etc_tree": "etc"
└── etc/
    ├── pam.d/sudo                    → /etc/pam.d/sudo
    ├── profile.d/dasik.sh            → /etc/profile.d/dasik.sh
    └── udev/rules.d/99-qudelix.rules → /etc/udev/rules.d/99-qudelix.rules
```

Every file under the tree becomes a `files` entry whose `path` is its position
under `/etc`. Nothing else to declare. The tree reads like the `/etc` it
produces, which is the whole point — a reviewer recognises it without learning a
schema.

It covers what the seven snippet sections (`udev_rules`, `sysctl_d`, …) cannot:
`/etc/pam.d/sudo` has no section of its own and must be a `files` entry today.

### Where it is expanded

In the **loader**, next to `resolve_includes`: only the loader knows where the
config file is, and therefore where the tree is. After expansion every action,
`plan`, `check` and the preflight see ordinary `files` entries and need no
changes at all.

The key stays in the config after expansion, because `sync` has to know the tree
exists to write back into it.

### Modes

Git preserves one permission bit, so the tree can carry `0755` and nothing else:

- a file executable in the working tree ⇒ `mode: "0755"`;
- otherwise no mode (the umask), as today;
- exceptions are declared, because a WireGuard keyfile that is world-readable is
  ignored *silently* by NetworkManager and that failure is invisible:

```json
"etc_tree": "etc",
"etc_tree_modes": { "wireguard/wg0.conf": "0600" }
```

Keys are paths relative to the tree.

### Precedence and collisions

An explicit `files` entry **wins** over a tree file for the same path: the
config says it in the file you are reading, so the file you are reading is
right. `preflight` warns, because two declarations of one path is a smell even
when the winner is defined.

Deleting a file from the tree removes it from `files`, which the existing
set-math then removes from the machine. That is the correct behaviour and worth
stating: the tree is a declaration, not a pile of stuff.

### What `sync` does

With `etc_tree` declared, a captured `files` entry under `/etc` is **extracted**:
its body is written to the tree and the JSON keeps no content at all. Paths
outside `/etc` stay inline as they are today.

This is the other half of the `$include` writeback: the capture stops growing
the JSON and starts growing a directory that looks like `/etc`. Extraction hands
its writes to `write_back`, so the all-or-nothing guarantee still holds across
both.

## Refusals

The tree is read at load time, so a bad tree must fail loudly before anything
runs:

| Case | Behaviour |
| --- | --- |
| a symlink inside the tree | refused — it would silently publish whatever it points at |
| a file that is not UTF-8 text | refused, naming the file: `files.content` is a string |
| a path escaping the tree, or the tree escaping the config directory | refused (same rule `_resolve_path` already enforces for includes) |
| an empty directory | ignored |
| `etc_tree_modes` naming a file the tree does not hold | refused — a mode nobody applies is a typo, and this one protects secrets |

## Acceptance

- a tree of three files produces three `files` entries with the right paths, and
  `plan` shows them exactly as hand-written entries would;
- an executable file gets `0755`; a declared exception wins over both;
- an explicit `files` entry for the same path wins, and `preflight` warns;
- **`sync` extracts**: a captured `/etc/pam.d/sudo` lands in `etc/pam.d/sudo`
  and the JSON gains no body;
- round trip: `sync` → `check` → `plan` silent, with the tree in place;
- removing a file from the tree plans its removal from the machine;
- every refusal above fails at load, before any action runs.

## Out of scope

- Trees for anything other than `/etc` (`/usr/lib`, `/var`) — `files` still
  takes any absolute path, and no evidence yet that a second tree earns its
  keep.
- Ownership (`chown`) of tree files: `files` has no owner field today, and
  adding one belongs to its own change.
- Binary files. `files.content` is a string; a binary belongs in a package.

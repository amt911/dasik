# Config splitting and secrets

Large machine configs become hard to review when a 150-package list, PAM snippets, udev rules and password/LUKS material all live in one JSON file. Dasik can assemble a config from multiple files **before Pydantic validation**.

There are four directives:

| Directive | Result |
| --- | --- |
| `{"$include": "path.json"}` | Parse that file as JSON and replace the directive object with the resulting value. |
| `{"$include_text": "path.conf"}` | Read the whole file verbatim as one string, including its trailing newline. |
| `{"$include_line": "secrets/hash"}` | Read the first line, strip surrounding whitespace, and use it as one string. |
| `{"$concat": [ ... ]}` | Resolve each member as a list and flatten the lists in order. |

They can be used anywhere a JSON value could appear.

## `$include`: JSON fragments

Split a whole section:

```json
{
  "hostname": "archlinux-p14s",
  "locales": {"$include": "locales.json"},
  "disks": {"$include": "disks.json"},
  "users": {"$include": "users.json"}
}
```

`locales.json` can simply be:

```json
{
  "selected_locales": ["es_ES.UTF-8 UTF-8"],
  "desired_locale": "es_ES.UTF-8",
  "desired_tty_layout": "es"
}
```

An included JSON file may itself contain directives. Nested paths resolve relative to the **included file that names them**, not always relative to the top-level `main.json`.

## `$include_text`: verbatim file bodies

Use this for multiline files whose newlines and final newline matter:

```json
{
  "files": [
    {
      "path": "/etc/pam.d/sudo",
      "content": {"$include_text": "files/etc-pam.d-sudo"}
    }
  ],
  "sysctl_d": [
    {
      "name": "99-dasik.conf",
      "content": {"$include_text": "sysctl/99-dasik.conf"}
    }
  ]
}
```

The text is not parsed as JSON and is not trimmed. This is intentional for real configuration file bodies.

## `$include_line`: one-line secrets

Use this for values where an accidental newline changes the value:

```json
{
  "users": [
    {
      "username": "andres",
      "hashed_password": {"$include_line": "secrets/hashed-password"},
      "groups": ["wheel"]
    }
  ]
}
```

Likewise for a LUKS passphrase:

```json
"luks_password": {"$include_line": "secrets/luks-passphrase"}
```

Why not `$include_text`? Because `$include_text` preserves the trailing newline. A value like a crypt hash with `\n` appended is no longer the same hash even though the file looked visually correct.

`$include_line` fails if the referenced file has no non-empty first line.

### Practical secret layout

A useful split directory looks like:

```text
config/my-machine/
├── main.json
├── users.json
├── disks.json
├── packages.json
├── files/
│   └── etc-pam.d-sudo
└── secrets/
    ├── hashed-password.example
    ├── luks-passphrase.example
    ├── hashed-password          # gitignored
    └── luks-passphrase          # gitignored
```

The repository already ignores secret payloads under split config secret directories while allowing `.example` templates. Check your actual `git status` before committing any machine config containing credentials.

A hash is still sensitive authentication material even though it is not plaintext. WireGuard/NetworkManager configs can contain **actual private keys**, and `sync` may capture them verbatim into JSON. Treat machine configs according to the secrets they contain.

## `$concat`: split long lists

Packages are a common use:

```json
{
  "packages": {
    "$concat": [
      {"$include": "packages-base.json"},
      {"$include": "packages-desktop.json"},
      {"$include": "packages-dev.json"}
    ]
  }
}
```

Each member must resolve to a list. If one member resolves to an object/string/etc., assembly fails before schema validation.

The same technique works for any list-valued config field.

## Path rules

The resolver intentionally prevents a config from quietly reaching arbitrary paths outside its own tree.

### Paths must be relative

Allowed:

```json
{"$include": "parts/packages.json"}
```

Rejected:

```json
{"$include": "/home/user/packages.json"}
```

### `..` is forbidden

Rejected:

```json
{"$include": "../shared.json"}
```

A config may pull in files at or below the directory of the file naming the include, not escape upward.

### Empty/non-string paths fail

These fail during include resolution:

```json
{"$include": ""}
{"$include": null}
```

## A directive object may contain only the directive

Valid:

```json
{"$include": "packages.json"}
```

Invalid:

```json
{"$include": "packages.json", "comment": "desktop packages"}
```

Combining directives in one object is also invalid:

```json
{"$include": "a.json", "$include_text": "b.txt"}
```

This rule makes it obvious that the entire object is replaced during assembly.

## Include cycles are detected

If `a.json` includes `b.json`, which includes `a.json`, dasik reports an include cycle rather than recursing forever.

Including the same fragment independently from different branches is fine; the restriction is a recursive cycle in the current include chain.

## What each verb does with a split config

### `check`

Assembles first, then validates the final object:

```bash
dasik check config/split-example/main.json
```

This is the safest way to test that all paths, JSON fragments and final field types line up.

### `plan` / `apply`

They use the same assembled, validated config. Relative includes are resolved from the config tree before the normal expand/preflight/reconcile path.

### `sync` deliberately refuses it

`sync` writes a complete JSON document using `ConfigWriter`. If it accepted a split root, it would replace `main.json` with the **fully resolved** object and silently destroy the split structure.

Therefore dasik checks for any of the four directives at any depth and refuses the sync with guidance.

A practical workflow is:

```bash
# Keep the real split untouched.
cp config/my-machine/main.json /tmp/synced.json

# A top-level copy alone will not contain its referenced fragments if paths are
# relative, so for a real split either copy the whole directory to /tmp or use a
# separate monolithic seed for capture.
```

The simplest robust pattern is to capture into a standalone scratch config:

```bash
echo '{}' > /tmp/current-system.json
sudo "$(command -v dasik)" sync /tmp/current-system.json --target /
```

Then compare that result with the split tree and fold relevant changes into the appropriate fragment by hand.

## Working examples in the repository

### `config/split-example/`

Small teaching example showing `$include`, `$include_text` and `$concat`:

```bash
dasik check config/split-example/main.json
```

### `config/test-config-split/`

Larger tracked split corresponding to the test configuration. Tests compare the assembled output with its single-file counterpart to catch drift.

### `config/laptop-p14s-split/`

Realistic ThinkPad configuration. It demonstrates:

- section includes (`users.json`, `packages.json`, `disks.json`, …);
- verbatim PAM/config files with `$include_text`;
- one gitignored password hash via `$include_line`;
- one gitignored LUKS passphrase referenced by multiple encrypted partitions so the secrets cannot drift independently.

For example, its `users.json` contains:

```json
{
  "username": "andres",
  "hashed_password": {"$include_line": "secrets/hashed-password"},
  "shell": "/bin/zsh",
  "groups": ["libvirt", "wheel"]
}
```

and both encrypted partitions can reference the same passphrase file.

## Recommended split strategy

Do not split merely for the sake of many files. A useful boundary is something you can review independently:

```text
main.json           identity + feature switches + includes
packages.json       package intent
users.json          accounts/groups, secret references only
disks.json          destructive storage declaration, secret references only
systemd.json        service policy
firewall.json       firewall policy
files/              real verbatim files with editor syntax highlighting
secrets/            gitignored one-line secret payloads
```

Run this after edits:

```bash
dasik check config/my-machine/main.json
```

and inspect the plan separately before any apply:

```bash
dasik plan config/my-machine/main.json --target /
```

See [Configuration reference](configuration.md) for the final assembled schema and [Workflows and state](workflows.md) for safe plan/apply/sync usage.
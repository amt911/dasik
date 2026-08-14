# Issue #173 — block D: the last implementable items

Date: 2026-08-12
Issue: [#173](https://github.com/amt911/dasik/issues/173)
Blocks A / B / C: PRs #174, #175, #183–#187 (all merged).

Block D is what remains of #173 that can be built **without asking anything
first**. Everything else in the issue (profiles/environments, podman, docker,
the partitioning TUI) is undefined and is parked in "block E" until the answer
exists.

| Item in #173 | This block |
| --- | --- |
| paquetes públicos pero no subidos a la AUR | PR A — `package_sources` for any HTTPS host, and a `sync` that keeps it |
| dotfiles de `$HOME` · notificaciones de AppArmor (`aa-notify`) | PR B — `home_files`, the primitive both need, and `aa-notify` as its first consumer |
| integración con config-saver | PR C — the `config_saver` block |
| job que resuelve los nombres de paquete | PR D — a scheduled workflow that resolves every declarable name |

---

## PR A — packages that were never uploaded to the AUR

### What exists

`package_sources` + `PkgbuildGitInstaller` already clone a PKGBUILD repo at an
exact commit, build it as an unprivileged user and install it. Two limits, both
deliberate at the time:

1. `GitPackageSourceModel._validate_url` refuses any host but `github.com`
   ("first version").
2. `PackagesAction.import_state` returns `{packages: [...]}` and **nothing
   else**. `package_sources` is never captured.

### Why (2) is the real bug

The user's own `config-saver` is not in the AUR (`aur.archlinux.org/rpc` returns
`resultcount: 0`); its PKGBUILD lives in the public repo `amt911/config-saver-aur`.
So today:

```
apply   → config-saver is cloned, built, installed        ✅
sync    → packages: [..., "config-saver", ...]             ← and no source
apply the captured config on a fresh machine
        → the resolver finds config-saver in no repo, no group, no AUR
        → package_policy.unknown = warn-and-skip (the default)
        → the package silently disappears
```

That is precisely the one-way street the repo rule warns about: a feature
`apply` converges and `sync` cannot read back. The manifest already records
`action_state.pacman.source_refs = {name: sha}` — the commit, but not the URL,
so even the state cannot rebuild the declaration.

### Design

- **Host** — accept any `https://<host>/…​.git`, with the host validated as a
  DNS name. The URL never reaches a shell (it is a positional `$1` to the build
  user's `sh`), so this widens *which server you trust*, not the attack surface.
  Still refused: non-HTTPS (no integrity), no `.git` suffix, credentials in the
  URL (`user:pass@`) — a secret in the config that would land in every synced
  copy.
- **State** — `state_metadata()` records the whole source (`url`, `ref`,
  `subdir`), not just the ref. Old manifests carrying only `source_refs` keep
  working (the ref-drift check reads either shape).
- **Capture** — `PackagesAction.import_state` re-emits `package_sources` for
  every installed package that has a source, taking the declared one when the
  config declares it and the manifest's otherwise. A machine dasik installed
  therefore syncs into a config that can rebuild itself.

### Detectability matrix (both directions)

| Situation | `plan` must say |
| --- | --- |
| declared with a source, not installed | `+ [packages] install <name>` |
| declared with a source, installed at that ref | nothing |
| declared with a *different* ref | `~ [packages] modify <name> (source ref changed)` |
| no longer declared, owned by the manifest | `- [packages] remove <name>` |

### Sync matrix

| Machine | `sync` must produce |
| --- | --- |
| package installed, source in the manifest | the `package_sources` entry, verbatim |
| package installed, source only in the config | the declared entry (intent survives) |
| package not installed | no invented source |
| captured config | `check` rc=0 and `plan` silent |

---

## PR B — `home_files`, and `aa-notify` as its first user

### Why a new primitive

dasik can write anywhere under `/etc` and nowhere under `$HOME`. Two items of
#173 are blocked on exactly that: the dotfiles (mangohud, lsd, autoeq) and the
AppArmor desktop notifications, whose autostart entry lives in
`~/.config/autostart/apparmor-notify.desktop`.

### Shape

```json
"home_files": [
  {"user": "andres", "path": ".config/autostart/apparmor-notify.desktop",
   "content": "…", "mode": "0644"}
]
```

- `path` is **relative** to the user's home: no leading `/`, no `..` segment.
  The absolute path is resolved from the target's own `/etc/passwd`, so it is
  the machine that says where the home is, not the config.
- Ownership is not optional: a file written by root into `$HOME` that stays
  `root:root` is a file the desktop cannot rewrite. `apply` chowns the file
  **and every directory it had to create**.
- Runs after `UsersAction` — the home has to exist first.

### Why `sync` does not scan `$HOME`

Discovery over a whole home directory would capture gigabytes and every secret
in it. So `home_files` captures **only** paths that are already declared or that
the manifest owns (`import_state(managed)`), which is honest: dasik reports back
what it put there, and invents nothing on a machine it never touched.

### `aa-notify`

`apparmor.desktop_notifications: true` adds `apparmor-notify`'s dependencies
(`python-notify2`, `python-psutil`, `tk`) and one `home_files` entry per user in
the `adm` group — the group that can read the denial log, so the group that has
anything to be notified about. Off by default.

---

## PR C — `config_saver`

`config-saver` reads YAML **and JSON** from `/etc/config-saver/configs`, and
ships a templated unit `config-saver@<user>.timer`. So the block is:

```json
"config_saver": {
  "source": {"url": "https://github.com/amt911/config-saver-aur.git", "ref": "<sha>"},
  "configs": {"dotfiles": { …the config-saver document… }},
  "timer_users": ["andres"]
}
```

- `source` is optional sugar over PR A: it becomes the `package_sources` entry
  for `config-saver`, because the package is not in the AUR and nothing else
  could install it.
- `configs` are written as **JSON** (`/etc/config-saver/configs/<name>.json`),
  not YAML: config-saver accepts both, and JSON needs no new runtime dependency
  and compares semantically for free.
- `timer_users` enables `config-saver@<user>.timer` through the existing
  `systemd` domain.
- `sync` captures the whole block: the configs are discovered from the directory
  (skipping the ones the package itself ships — `pacman -Qo`), and the timers
  from the enabled units.

---

## PR D — the job that resolves every declarable package name

Both bugs in #187 (`nvidia`, `libva-mesa-driver`) were invisible because the
package names dasik derives are **data nobody resolves**. A scheduled GitHub
Actions job on an `archlinux` container walks every name the expand toggles and
the driver tables can produce, resolves each with `pacman -Si` (and the AUR RPC
for the rest), and fails when one disappears.

It is a scheduled job, not a merge gate: an upstream rename must not block an
unrelated PR, but it must not stay silent for months either.

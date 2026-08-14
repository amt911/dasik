# Packages

```json
"packages": ["base", "linux", "linux-firmware", "firefox", "yay",
             {"name": "linux-headers", "reason": "dep"},
             {"name": "clonehero-ptb", "optional": true}]
```

Declare **real package names**. There is no `aur-` prefix and no per-source
list: dasik works out where each name comes from at apply time. The same name
keeps working when a package moves from the AUR into a repo — the repo simply
wins on the next apply.

---

## How a name is resolved

Precedence: **configured repo → pacman group → AUR → `package_sources` →
unknown**.

| Step | Probe |
| --- | --- |
| repo | `pacman -Slq` over the configured repositories |
| group | `pacman -Sgq` |
| AUR | aurweb RPC v5 `info`, **exact name**, batched under the ~4.4 KB URI cap |
| explicit Git | a matching `package_sources` entry |

Two failure modes, deliberately different:

- **Unknown** — the name exists nowhere. Governed by `package_policy.unknown`:
  `warn-and-skip` (default) skips it with a visible warning, installs the rest,
  and exits 0, so the next apply retries it; `error` aborts (useful in CI).
- **Unreachable** — the AUR could not be *queried* (DNS, timeout, HTTP 5xx).
  Always a blocking error, whatever the policy: "we could not look" must never
  be silently downgraded to "it does not exist".

Package names are validated against the Arch grammar
(`[a-zA-Z0-9][a-zA-Z0-9@._+-]*`, no leading `-`) before they ever reach a
pacman argv.

## Install reasons

| Form | pacman reason |
| --- | --- |
| `"firefox"` | explicit |
| `{"name": "linux-headers", "reason": "dep"}` | dependency — prunable as an orphan once nothing needs it |

Set-math ownership works on the explicit set: `pacman -Qqe`. Transitive
dependencies are never captured and never removed by dasik.

## Optional packages

```json
{"name": "some-huge-aur-app", "optional": true}
```

A failed optional package is reported and **left out of the manifest** — it is
never claimed as installed, so the divergence stays visible and the next apply
retries it. A required package's failure aborts the apply. Use it for peripheral
software whose upstream can break independently of you: a vendor printer driver,
a large AUR application.

`optional` is intent, so `sync` keeps it. Losing it would make the next apply
abort on the very package you marked non-blocking.

---

## AUR packages

Nothing special to declare — put the name in `packages`. Two install paths:

**With a helper.** Declare `yay` or `paru` in `packages` and dasik installs the
helper first (from source), then hands it the rest.

**Without one.** dasik reads each `.SRCINFO`, resolves the dependency graph
topologically, and `makepkg`s each node in order as an unprivileged builder
user. A dependency discovered in the AUR is built too; repo dependencies are
left to `makepkg -s`.

Either way:

- builds run as a **non-root** user created for the purpose (makepkg refuses
  root), with a temporary sudoers entry so it can sync repo deps, both removed
  afterwards;
- clone and build output is streamed into the run log, so a failed build is
  diagnosable after the fact;
- a package built as a discovered dependency is corrected to reason `dep`.

## Packages from a Git PKGBUILD

For something in neither the repos nor the AUR — your own repository, say:

```json
"packages": ["config-saver"],
"package_sources": {
  "config-saver": {
    "type": "pkgbuild-git",
    "url": "https://github.com/amt911/config-saver.git",
    "ref": "3f2b1c0d4e5f60718293a4b5c6d7e8f901234567",
    "subdir": "."
  }
}
```

| Key | Rule |
| --- | --- |
| `type` | `pkgbuild-git` |
| `url` | `https://<host>/….git` — any forge, https only. Credentials in the URL are refused: a synced config would carry the secret |
| `ref` | a **full 40-char commit SHA**. A branch name is not reproducible, so it is refused |
| `subdir` | relative, may not escape the clone root |

dasik clones at that exact commit, builds the PKGBUILD under `subdir` as the
unprivileged builder, and **verifies the built `pkgname` matches the declared
package** before installing it. The applied SHA is recorded in the manifest and
carried verbatim across a `sync` — dasik never fabricates a SHA it did not
apply.

`sync` captures the whole source, not just the name. It has to: a package built
this way exists in no repo and in no AUR, so a capture that named only
`config-saver` would re-plan into "unknown package", `warn-and-skip` would drop
it, and the machine's own config would no longer describe the machine. What the
capture reports is what the last `apply` actually built (the manifest), unless
the config declares a source itself — intent wins, so a `ref` you just bumped
survives the round trip.

Every `package_sources` key must appear in `packages`; the schema rejects a
source nobody declares.

---

## pacman itself

```json
"pacman": {
  "options": { "Parallel": true, "Color": true, "VerbosePkgLists": false },
  "multilib": false
}
```

| Key | Default | `/etc/pacman.conf` |
| --- | --- | --- |
| `Parallel` | `true` | `ParallelDownloads` |
| `Color` | `true` | `Color` |
| `VerbosePkgLists` | `false` | `VerbosePkgLists` |
| `multilib` | `false` | the `[multilib]` block |

Bidirectional: a flag set back to `false` is commented out again, and
`multilib: false` re-comments the repository block. `multilib` also decides
whether the [`drivers`](Features.md#gpu-drivers) toggle adds the `lib32-*`
packages — the ones 32-bit applications like Steam need.

## Mirrors

```json
"reflector": { "countries": ["ES", "France"], "protocols": ["https"],
               "latest": 20, "sort": "rate" }
```

Installs `reflector`, enables `reflector.timer` (the timer, not the one-shot
service it triggers) and writes `/etc/xdg/reflector/reflector.conf`. See
[Features](Features.md#reflector).

---

## What `sync` captures

| Captured | Not captured |
| --- | --- |
| every **explicit** package (`pacman -Qqe`), AUR ones included, as plain names | transitive dependencies |
| a declared package installed as a dependency, re-emitted as `{name, reason: "dep"}` | |
| the package **behind an enabled unit**, as `{name, reason: "dep"}` — so the captured config still validates when the unit is enabled | |
| `optional: true`, preserved as intent | |

A declared `aur-` prefix (the deprecated spelling) is dropped on the way back;
the plain name is re-emitted, because apply resolves the source itself.

That third row exists because a capture whose `systemd.enable_units` names
`sddm.service` while no package provides it is a config `check` then **rejects**
— a broken capture. See [Sync](Sync.md).

---

## Removal

A package dasik installed and you stop declaring shows up as:

```text
- [packages] remove htop  (no longer declared)
```

A package **you** installed by hand is not in the manifest, so it is never
touched. That is the ownership rule: dasik takes back only what it put there.
See [Workflows](Workflows.md#ownership).

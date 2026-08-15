# dasik

**d**eclarative **a**rch linux **s**cript **i**nstaller **(k**inda**)**.

Describe the machine you want in one JSON file. Run `dasik apply config.json`.
Run it again and nothing happens, because nothing has to. That second property —
idempotency — is the whole point: dasik is a *converger*, not a script that
replays.

```text
config.json  ──  check → plan → apply ──▶  the machine
config.json  ◀──────────  sync  ─────────  the machine
```

It installs Arch from the live ISO onto `/mnt`, and it manages the machine you
are already running (`--target /`). Same config, same verbs.

---

## Pick your entry point

| I want to… | Page |
| --- | --- |
| Install dasik and know what it needs | **[Installation](Installation.md)** |
| Install a machine from the ISO, start to finish | **[Quickstart](Quickstart.md)** |
| Do the whole thing from zero: private repos, `$HOME`, install, day two | **[From zero](From-zero.md)** |
| Turn a machine you built by hand into a reproducible one | **[Adopt an existing machine](Adopt-an-existing-machine.md)** |
| Look up a verb, a flag, an exit code | **[CLI reference](CLI.md)** |
| Look up **every** JSON field there is | **[Configuration reference](Configuration.md)** |
| Partition, encrypt with LUKS, lay out btrfs subvolumes | **[Disks and encryption](Disks.md)** |
| Choose a bootloader, an initramfs, kernel parameters, a splash | **[Boot chain](Boot.md)** |
| Declare packages, AUR packages, a Git PKGBUILD, mirrors | **[Packages](Packages.md)** |
| Know what each feature block actually installs | **[Feature blocks](Features.md)** |
| Split a 400-line config into readable files, keep secrets out | **[Config splitting](Config-splitting.md)** |
| Understand plan/apply/ownership/generations/rollback | **[Workflows and state](Workflows.md)** |
| Know exactly what `sync` can and cannot capture | **[Sync](Sync.md)** |
| Understand a preflight error before it aborts an install | **[Validation](Validation.md)** |
| Copy a working config and adapt it | **[Recipes](Recipes.md)** |
| Fix a boot that hangs, an apply that failed | **[Troubleshooting](Troubleshooting.md)** |
| Hack on dasik itself | **[Development](Development.md)** |

---

## The mental model in one minute

dasik owns **domains**, not files. A domain is one slice of the machine —
`packages`, `users`, `disks`, `kernel_cmdline`, `files`, `bootloader`, … Each
domain has one action that can do three things:

| Method | Question it answers |
| --- | --- |
| `plan()` | What differs between what you declared and what the machine actually has? |
| `apply()` | Make exactly those differences go away. |
| `import_state()` | What does the machine actually have? (this is `sync`) |

`plan()` reads real state — `pacman -Qq`, `/etc/shadow`, `lsblk`,
`/boot/loader/entries`, `cryptsetup luksDump` — never a cached belief. So a
converged machine plans nothing, and a re-run is silent.

### Ownership: why `plan` can say "remove"

After every successful `apply`, dasik records a **manifest** of what it owns, at
`/var/lib/dasik/state.json` under the target. The next plan is set math:

```text
INSTALL = declared \ actual          # you asked for it, it isn't there
REMOVE  = (owned ∩ actual) \ declared # dasik installed it, you stopped asking
```

That middle term is what keeps dasik from fighting the rest of the system: a
package you installed by hand is not in the manifest, so dropping it from the
config removes nothing. dasik only takes back what it put there.

### Two directions, never one

Every feature must be **visible to `plan`** (missing ⇒ planned, present ⇒
silent, owned-but-undeclared ⇒ removed) *and* **capturable by `sync`** (the
machine's reality reads back as its own config block). A feature that applies
but cannot be captured is a one-way street: capture the machine, re-apply the
capture, and the feature silently disappears. See [Sync](Sync.md).

---

## Target roots: the one thing everybody trips on

dasik was built to install from the Arch ISO, where the new system is mounted at
`/mnt`. So the defaults differ per verb:

| Verb | Default `--target` |
| --- | --- |
| `plan`, `apply` | `/mnt` — the install target |
| `sync`, `generations`, `rollback` | `/` — the running host |

Any target other than `/` runs every command through `arch-chroot`, which lives
in `arch-install-scripts` (on the ISO; rarely on an installed system). To manage
the machine you are booted into, always pass it explicitly:

```bash
dasik plan my-system.json --target /
```

A bare `dasik plan my-system.json` targets `/mnt` and, on a normal host, stops
immediately with an actionable message rather than half-probing a chroot that
does not exist.

---

## Safe first commands

None of these mutate anything:

```bash
dasik check config/install-megamix.json      # JSON + schema + coherence
dasik plan  config/install-megamix.json --target /   # the dry run
dasik generations --target /                 # what has been applied here
dasik --help
```

`apply` and `rollback` **are** destructive: they partition disks, run `mkfs`,
and drive `pacman`. Never point them at hardware you care about while learning —
use a VM ([Development](Development.md#testing-in-a-vm)).

---

## Where truth lives

This wiki documents the `dasik/` package at the repository root. When prose and
code disagree, the code wins — and the doc is the bug. The sources behind each
page:

| Page | Code it documents |
| --- | --- |
| [CLI](CLI.md) | `dasik/__main__.py` |
| [Configuration](Configuration.md) | `dasik/lib/models/` |
| [Features](Features.md) | `dasik/lib/expand/toggles.py` |
| [Config splitting](Config-splitting.md) | `dasik/lib/json_parser/includes.py` |
| [Validation](Validation.md) | `dasik/lib/validation/preflight.py` |
| [Workflows](Workflows.md), [Sync](Sync.md) | `dasik/lib/reconciler/`, `dasik/lib/state/`, `dasik/lib/actions/` |

`archinstall/` and the legacy shell scripts in the repo root are historical
reference. They do not define the current config format.

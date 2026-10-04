# Resources (local reference — bind-mounted, not committed)

> Moved verbatim out of `AGENTS.md` on 2026-10-04 so that file fits the 32 KiB Codex reads
> by default. Its rules still bind: `AGENTS.md` lists the hard ones inline and says when to
> read this file. Edit the rule here, not a copy of it.

## Resources (local reference — bind-mounted, not committed)

`resources/` holds two read-only references plus the script that mounts them. They are **not tracked** (`git ls-files resources/` is empty) — recreate them on a new machine with [`resources/bind-mount.sh`](../../resources/bind-mount.sh):

| Path | What it is | Use it for |
| --- | --- | --- |
| `resources/archlinux-script-installer/` | The **old** personal install scripts (bind-mount of `~/repos/archlinux-script-installer`, its own git repo). | The imperative original that dasik reimplements declaratively. Same target system: LUKS encryption (+ pendrive unlock), ext4 or btrfs+subvolumes, snapper snapshots, GRUB/systemd-boot, KDE Plasma, NVIDIA passthrough. Read a step here to see how it was done before porting it to an idempotent Action — its own `TODO` already asks for "incremental changes on already installed systems", i.e. dasik's idempotency goal. |
| `resources/arch-wiki/` | Arch Wiki **offline HTML** mirror (bind-mount of `/usr/share/doc/arch-wiki/html/en`, from the `arch-wiki-docs` package). ~2,500 pages named by title: `Btrfs.html`, `Dm-crypt.html`, `Mkinitcpio.html`, `Systemd-boot.html`, … | Authoritative reference for the exact procedure an Action must reproduce (mkinitcpio hook order, dm-crypt cmdline flags, btrfs subvolume layout, …). gitignored via the `arch-wiki/` pattern. |

**Never enumerate or bulk-read `resources/`, and never graphify it** — the two subtrees are >12k files (~2,500 arch-wiki HTML pages, each bloated with markup). A `Glob resources/**`, a repo-wide grep that ingests them, or reading many pages at once swamps context (and the graph). Graph scope is the `dasik/` package only (~52 files); these dirs are read-only reference, not source.

**Consulting the arch-wiki (`resources/arch-wiki/`) IS fine when targeted:**

- **Read a known page** — pages are named by title, so open the exact file: `Read resources/arch-wiki/Btrfs.html`. A targeted `Read` works despite the gitignore.
- **Search for which page covers X** — scope the search to the wiki, never the whole repo: `Grep` with `path: resources/arch-wiki/`, or (the subtree is gitignored, so a plain repo grep skips it) `rg --no-ignore-vcs '<pattern>' resources/arch-wiki/` via Bash. Both return only matching lines; then `Read` only the page(s) the search points to.
- **Never** `Glob resources/**`, never read pages in bulk, never let `/graphify` ingest the tree.

Mechanism: the heavy subtrees are gitignored (`arch-wiki/`, `resources/archlinux-script-installer/`), so Grep skips them by default and Glob does too via `CLAUDE_CODE_GLOB_NO_IGNORE=false` (set in `.claude/settings.json`). Intentional, path-scoped lookups still work; only accidental mass enumeration is blocked.

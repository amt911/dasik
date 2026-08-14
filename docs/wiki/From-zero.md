# From zero — a new machine, your config, your `$HOME`

The complete path: a live ISO that knows nothing about you, two private
repositories, and a machine that comes back the way you left it. Every command
here is real; nothing is elided.

You need three repositories. Two are yours and private; the third is the
packaging, and it is public:

| Repository | Holds | Visibility |
| --- | --- | --- |
| `dasik-personal-config` | your machines' configs, their `/etc` trees, their secrets | **private** |
| `dasik-aur` | the PKGBUILD that installs dasik | public |
| `config-saver-aur` | the PKGBUILD that installs config-saver | public |

The `$HOME` archive is **not** a repository. It is a release asset on the
private one — Git is bad at gigabytes of browser profile.

---

## 0. What lives where

```text
dasik-personal-config/            ← private
├── p14s/
│   ├── main.json                 the config, assembled from the rest
│   ├── packages.json  disks.json  systemd.json  …
│   ├── config-saver-configs.json what config-saver backs up
│   ├── etc/                      ← every /etc file, as a real file
│   │   ├── pam.d/sudo
│   │   ├── profile.d/dasik.sh
│   │   └── udev/rules.d/99-qudelix.rules
│   └── secrets/                  ← gitignored
│       ├── hashed-password
│       └── luks-passphrase
└── .gitignore                    */secrets/*  and  !*/secrets/*.example
```

Three mechanisms do this, and they are worth telling apart:

- **`$include`** puts a block in its own file (`"disks": {"$include": "disks.json"}`).
- **`etc_tree`** turns a directory into `files` entries, so a PAM snippet is a
  PAM snippet and not an escaped string. See [Config splitting](Config-splitting.md).
- **`$include_line`** keeps a secret out of the committed JSON — the file it
  reads is gitignored.

`config/laptop-p14s-split/` in the dasik repo is exactly this shape, and is kept
provably equivalent to the single-file `config/laptop-p14s.json` by a test.

## 1. Seed the private repo from the machine you have

Do this **before** you wipe anything. On the old machine:

```bash
sudo dasik sync ~/config/p14s/main.json --target /
dasik check ~/config/p14s/main.json
```

`sync` reads the live system — disks, LUKS layout, packages, units, `/etc`
snippets — and writes it back through the split: each value returns to the file
it came from, bodies land in `etc/`, and the secret files are rewritten only if
the secret actually changed. Then make it generic before reusing it on new
hardware (drop data disks, turn `wipe`/`format` on, swap `luks_uuid` for a
passphrase): [the capture guide](https://github.com/amt911/dasik/blob/main/docs/copy-your-config-and-test.md).

Commit and push it. The secrets stay behind, ignored.

## 2. Capture `$HOME`, encrypt it, publish it

config-saver already runs daily if you declared `timer_users`. Take the latest
archive, encrypt it, and attach it to a release on the **private** repo:

```bash
config-saver --export-config dotfiles --output ~/home.tar.gz
age -p -o ~/home.tar.gz.age ~/home.tar.gz          # passphrase, prompted twice
gh release create home-p14s ~/home.tar.gz.age \
    -R amt911/dasik-personal-config -n '$HOME capture'
```

Later captures replace it in place:

```bash
gh release upload home-p14s ~/home.tar.gz.age --clobber -R amt911/dasik-personal-config
```

One tag, always the newest archive, no pile of releases nobody prunes.

> **A plain `.tar.gz` is compressed, not encrypted** — config-saver says so
> itself, and this one holds your browser profiles. `age -p` needs no key file:
> nothing to lose on a pendrive, one thing to remember.

**Make the archive self-sufficient.** An archive brings back your data; it
brings back *what to back up* only if some configuration archives the directory
the configurations live in. Declare it — config-saver ships `own-configs` as an
example, and since 3.3.0 examples are never active, so on a dasik machine it
arrives from no package:

```json
"config_saver": {
  "configs": { "$include": "config-saver-configs.json" },
  "timer_users": ["andres"]
}
```

```json
{
  "own-configs": {
    "normalize_content": false,
    "directories": ["$CONFIG_DIR/config-saver/configs.d"]
  }
}
```

## 3. Boot the ISO and get dasik

UEFI mode, networking up (`iwctl` for wifi), then:

```bash
curl -fsSL https://raw.githubusercontent.com/amt911/dasik-aur/main/iso-bootstrap.sh -o bootstrap.sh
bash bootstrap.sh
```

It installs dasik from the published package, logs into GitHub with a **device
code** — eight characters you type on your phone, no token typed on the ISO, no
SSH key on a pendrive — and clones the private repo to `/root/config`.

> Download the script rather than piping it into `bash`: the login step needs a
> terminal.

## 4. Put the secrets back

They are the one thing the repo does not carry:

```bash
cd /root/config/p14s
dasik hash-password > secrets/hashed-password     # prompts twice
printf '%s\n' 'your-luks-passphrase' > secrets/luks-passphrase
dasik check main.json                              # OK
```

## 5. Install

```bash
dasik plan  main.json          # read it. every change, in full
dasik apply main.json          # DESTRUCTIVE from here
```

The config carries dasik and config-saver as packages built from their PKGBUILD
repositories, so the installed machine has both:

```json
"packages": ["dasik", "config-saver"],
"package_sources": {
  "dasik": {"type": "pkgbuild-git", "url": "https://github.com/amt911/dasik-aur.git",
            "ref": "<40-char commit sha>", "subdir": "."}
},
"config_saver": {
  "source": {"url": "https://github.com/amt911/config-saver-aur.git",
             "ref": "<40-char commit sha>", "subdir": "."}
}
```

Both `ref`s are full commit SHAs — a branch name is not reproducible, so the
schema refuses one.

## 6. Reboot, then restore `$HOME`

**Not during the install.** `restore.archive` is a path *inside the target*, and
the target does not exist until `apply` has partitioned it — there is nowhere to
put the file beforehand. So, on the new machine, logged in as yourself:

```bash
gh auth login                                       # device code again
gh release download home-p14s -R amt911/dasik-personal-config
age -d -o /tmp/home.tar.gz home.tar.gz.age
sudo mv /tmp/home.tar.gz /root/home.tar.gz          # the path the config declares
sudo dasik apply --target / main.json               # does only the restore
```

```json
"config_saver": {
  "restore": [{"user": "andres", "archive": "/root/home.tar.gz"}]
}
```

dasik marks the restore by the archive's **content hash** under
`~/.local/state/dasik/config-saver/`: re-running restores nothing, and dropping a
newer capture at the same path restores again — which is the whole point of a
file whose job is to change.

Offline instead? Same thing from a pendrive: mount it, copy to
`/root/home.tar.gz`, run the second `apply`.

## 7. Day two

```bash
sudo dasik plan  --target / /root/config/p14s/main.json    # what drifted
sudo dasik apply --target / /root/config/p14s/main.json    # converge
sudo dasik save  /root/config/p14s/main.json               # capture + commit + push
```

`save` is `sync` plus the commit: it validates the capture before committing it,
runs Git as **you** rather than as root, never stages a gitignored file (your
secrets stay out), and does nothing at all when the machine already matches.

---

## The five things that bite

1. **`--target`.** `plan`/`apply` default to `/mnt` (install time). On a running
   machine you must pass `--target /` or you are describing an empty directory.
2. **The restore is a second apply, after the first boot.** See step 6.
3. **`sudo dasik sync` writes as root** — the config lands in your repo owned by
   `root:root`. `dasik save` hands it back to you; plain `sync` does not, so
   `sudo chown -R $USER:` after it.
4. **A sync log holds secrets.** It records what was read back, WireGuard
   private keys included. Do not commit `dasik-sync-*.log` — and prefer
   `--no-log` when syncing into a repository.
5. **config-saver needs a configuration to exist.** Since 3.3.0 there is no
   fallback to the examples: with nothing in `/etc/config-saver/configs` and
   nothing in `~/.config/config-saver/configs.d`, the timer exits 6 on every
   fire. `dasik plan` warns about exactly that combination.

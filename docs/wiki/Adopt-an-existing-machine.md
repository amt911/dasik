# Adopting a machine you already have

The starting point here is the common one: **an Arch install you built by hand,
with neither dasik nor config-saver on it**. By the end you can wipe that
machine and get it back — the system from one repository, your `$HOME` from an
encrypted archive.

Nothing below touches a disk until step 7, and step 7 says so loudly.

> Already running both, and just want the install path? That is
> [From zero](From-zero.md). This page is how you get *to* that point.

## What you will end up with

| | Holds | Visibility |
| --- | --- | --- |
| `dasik-personal-config` | the machine's config, its `/etc` tree, its secrets | **private** |
| `config-saver-personal-config` | the archives themselves, as **release assets** — one release per machine | **private** |
| [`dasik-aur`](https://github.com/amt911/dasik-aur) · [`config-saver-aur`](https://github.com/amt911/config-saver-aur) | the two PKGBUILDs | public |

Two private repositories rather than one, because they hold different things
and change at different times: a config is text you review in a diff, an
archive is 2 GB of browser profile you never read. **Archives are release
assets, never commits** — Git keeps every version of a binary forever.

---

## 1. Install the two tools on the machine you have

Neither is in a pacman repo or the AUR; both are a PKGBUILD in a Git
repository:

```bash
git clone https://github.com/amt911/dasik-aur.git ~/build/dasik-aur
cd ~/build/dasik-aur && makepkg -si

git clone https://github.com/amt911/config-saver-aur.git ~/build/config-saver-aur
cd ~/build/config-saver-aur && makepkg -si
```

Nothing is configured yet and nothing runs: config-saver has no configuration
(it exits 6 and says so, rather than backing up examples nobody chose), and
dasik has no config to apply.

## 2. Capture the machine into a config

This is the step that makes the machine reproducible, and it is one command
against an empty seed:

```bash
mkdir -p ~/config/$(hostname) && cd ~/config/$(hostname)
echo '{}' > main.json
sudo dasik sync main.json --target /
dasik check main.json
```

`sync` reads the live system and fills that file in: disks and their LUKS
layout, every explicitly-installed package (AUR included), users, enabled
units, the snippets under `/etc`, firewall zones, locales, the bootloader,
zram, snapper. **From `{}`** — it does not need to have installed the machine
to describe it.

Read what it produced. It reports **reality**, which is not always what you
meant:

```bash
sudo dasik plan main.json --target /     # must be silent: the config IS the machine
```

Two things to fix by hand before this config is worth keeping:

- **`wipe_disk` and `format` come back `false`** (a capture must never arm a
  reinstall by accident). For a config you intend to *reinstall from*, that is
  backwards — see step 6.
- `luks_uuid` describes *this* disk. A new disk gets a new UUID, so a config
  meant for reinstalling declares a passphrase instead.

> `sudo dasik sync` writes as root, so the files land `root:root`. `sudo chown
> -R $USER: .` — or use `dasik save` from step 9, which hands them back for you.

## 3. Make it a repository

```bash
cd ~/config
git init -b main .
```

`.gitignore` **first**, before any secret exists:

```gitignore
*/secrets/*
!*/secrets/*.example
dasik-*.log
*.bak
```

That file is load-bearing twice over: it keeps a plaintext LUKS passphrase out
of the history, and `dasik save` decides what to stage by asking Git what is
ignored — with no rule, it would stage them too.

> A `dasik-sync-*.log` records everything the capture read, WireGuard private
> keys included. Never commit one.

Then split the config so it stays readable — the mechanisms are in
[Config splitting](Config-splitting.md):

```text
~/config/archlinux-p14s/
├── main.json                 "disks": {"$include": "disks.json"}, …
├── disks.json  packages.json  systemd.json  …
├── etc/                      every /etc file, as a real file
│   ├── pam.d/sudo
│   └── udev/rules.d/99-qudelix.rules
└── secrets/                  gitignored
    ├── hashed-password       {"$include_line": "secrets/hashed-password"}
    └── luks-passphrase       one file, both encrypted partitions
```

Declare `"etc_tree": "etc"` and the directory becomes the `files` entries; from
then on `sync` writes captured `/etc` bodies **into that directory** instead of
inlining them into JSON.

```bash
dasik check archlinux-p14s/main.json
git add -A && git commit -m "capture archlinux-p14s"
gh repo create dasik-personal-config --private --source=. --push
```

> **More than one machine?** They share one repository, and the layout is
> forced by a rule rather than chosen: `$include` refuses `..`, so a machine
> directory cannot reach `../common/` and the configs live at the root. See
> [Several machines, one repository](Config-splitting.md#several-machines-one-repository).

## 4. Decide what config-saver saves

config-saver reads **documents** that name directories and files. Since 3.3.0
there are three levels, two of them active and merged by file name (the user's
wins):

| Level | Owner |
| --- | --- |
| `/usr/share/config-saver/configs` | the package — examples, **never active** |
| `/etc/config-saver/configs` | the administrator — **dasik writes this** |
| `~/.config/config-saver/configs.d` | you, per user |

A document looks like this (`dotfiles.yaml`):

```yaml
normalize_content: true

directories:
  - source: "$HOME"
    files: [.zshrc, .gitconfig]
  - "$CONFIG_DIR/kdeglobals"
  - "$CONFIG_DIR/konsolerc"
```

And one document earns its place on every machine — the one that backs up the
documents themselves:

```yaml
# own-configs.yaml — without this, a restored archive brings back your data but
# not the rules that say what to back up.
normalize_content: false
directories:
  - "$CONFIG_DIR/config-saver/configs.d"
```

### Where the documents live

Two arrangements. They differ in one thing: whether dasik owns them.

**A — declared in the dasik config** *(recommended)*. One source of truth, and
a fresh machine has them before you log in, because `apply` writes them:

```json
"config_saver": {
  "source": {"url": "https://github.com/amt911/config-saver-aur.git",
             "ref": "<40-char commit sha>", "subdir": "."},
  "configs": { "$include": "config-saver-configs.json" },
  "timer_users": ["andres"]
}
```

`config-saver-configs.json` is the same documents in JSON (config-saver reads
both; JSON round-trips exactly, which is why `sync` captures them that way).

**B — kept in the `config-saver-personal-config` repository**. Useful when the same
documents must work on machines that do *not* run dasik: clone it into
`~/.config/config-saver/configs.d` and let the user level pick them up. The
cost is a second place to keep in step, and dasik will not put them on a fresh
machine for you.

You can do both: the system-wide policy from dasik, personal extras in the user
level. They merge, and the user's copy wins on a name collision.

## 5. Set up encryption before the first archive

**A `.tar.gz` is compressed, not encrypted**, and this one holds browser
profiles, SSH config, whatever else you listed.

### age in one table

age has **two modes**, and that is the whole confusion:

| Mode | You encrypt with | You decrypt with | Works unattended |
| --- | --- | --- | --- |
| **Public key** | the **public** key (`age1qz…`, safe to publish) | the **private** key (`AGE-SECRET-KEY-1…`, a file) | **yes** |
| **Passphrase** (`age -p`) | a password it prompts for | the same password | no |

The asymmetry is the point: **encrypting does not need the secret**. That is
what lets config-saver encrypt your backup at 03:00 without you there — it
carries the *public* key in its configuration, and that key can sit in a Git
repository in plain sight. Only decrypting needs the private key, and you do
that by hand, once, when you reinstall.

### Three commands

```bash
# 1. create the pair (once, ever)
mkdir -p ~/.config/age && age-keygen -o ~/.config/age/key.txt
#    prints:  Public key: age1qz9…      <- this is what goes in the config
chmod 600 ~/.config/age/key.txt

# 2. encrypt by hand (config-saver will do this for you; this is to see it work)
age -r age1qz9… -o test.age test.txt

# 3. decrypt
age -d -i ~/.config/age/key.txt -o test.txt test.age
```

`-r` is the *recipient* (public key); `-i` is the *identity* (the file holding
the private key).

### Wire it into config-saver — in the document the MACHINE reads

`encrypt` goes in the configuration document, per configuration. The catch is
that there are two places a document can come from, and editing the wrong one
changes nothing:

| Where the document lives | Edit this when | Format |
| --- | --- | --- |
| `~/.config/config-saver/configs.d/*.yaml` | you wrote it by hand, on this machine | YAML |
| `/etc/config-saver/configs/*.json` — **written by `dasik apply`** | it is declared in your dasik config | edit the config, then apply |

```yaml
# ~/.config/config-saver/configs.d/dotfiles.yaml
normalize_content: true
encrypt:
  method: age
  recipients:
    - age1qz9…           # the public key printed above
directories: [ … ]
```

```json
// the same thing declared in dasik: common/config-saver-configs.json
"dotfiles": {
  "normalize_content": true,
  "encrypt": { "method": "age", "recipients": ["age1qz9…"] },
  "directories": [ "…" ]
}
```

**Declaring it in the dasik config does nothing until `dasik apply` runs** —
that is what writes `/etc/config-saver/configs/`. On the machine you are
capturing *from*, which by definition dasik has not installed, the documents
that count are the ones in your `~/.config`. Both levels are read and merged,
so the two can coexist; the user's copy wins on a name collision.

### The one thing that can go wrong

**Lose `key.txt` and every archive is gone.** There is no recovery, and it
cannot live only inside the thing it decrypts. Copy it into a password manager
the moment you create it — it is a two-line file:

```bash
cat ~/.config/age/key.txt      # paste into your password manager
```

A passphrase instead (`age -p archive.tar.gz`) leaves nothing to custody, and
that is its whole appeal — but an unattended timer cannot type one, so a
plaintext archive of your `$HOME` sits on disk until you get round to it.
Choose deliberately.

## 6. First archive, and publish it

```bash
config-saver --show-configs      # THE NAMES ARE YOURS — one per document it found
#   - claude
#   - kvm-switch
#   - own-configs
#   - wallpapers

config-saver --compress          # runs every document; encrypted because they say so
```

Each run writes
`~/.config/config-saver/configs/<name>/<timestamp>/<name>-<timestamp>.tar.gz.age`.
Publish that set as **one release per machine** on the private data repository:

```bash
gh repo create config-saver-personal-config --private     # once

cd ~/.config/config-saver/configs
gh release create archlinux-p14s $(ls -1 */<timestamp>/*.age) \
    -R amt911/config-saver-personal-config \
    -n 'Encrypted $HOME archives. The private age key is NOT here.'
```

Later runs replace the assets in place — one tag per machine, always the newest:

```bash
gh release upload archlinux-p14s <new>.tar.gz.age --clobber \
    -R amt911/config-saver-personal-config
```

Or let `save` do both halves at once — the capture and the archives:

```bash
sudo dasik save archlinux-p14s.json --home amt911/config-saver-personal-config
```

It picks the newest archive of **each** configuration (skipping the
`description.txt` each run leaves beside them), refuses to publish anything
that is not encrypted, and updates the machine's release in place.

A real run, for scale: seven documents, **275 MB** of assets, of which
`wallpapers` was 65 MB and `claude-conversations` 210 MB. That is why they are
release assets and not commits.

> **A document that needs root is skipped**, with a note naming it. Anything
> under `/etc` is in that category — and on a machine dasik manages, `/etc` is
> dasik's job, so archiving it means two sources of truth for the same files.

> **Nothing prunes the archives.** Every run keeps its own timestamped
> directory, so a daily timer grows without limit: a real machine had **114
> copies of `wallpapers`, 7.2 GB**, on a disk at 96%. Until config-saver learns
> retention, prune them yourself:
> ```bash
> find ~/.config/config-saver/configs -mindepth 2 -maxdepth 2 -type d |
>     sort | head -n -3 | xargs -r rm -rf     # keep the newest 3 per document
> ```

Enable the timer so this keeps happening:

```bash
sudo systemctl enable --now config-saver@$USER.timer
systemctl list-timers 'config-saver*' --all       # NEXT must show a date
```

…and declare it in the config (`"timer_users": ["andres"]`) so a reinstall
brings it back.

### Arm the config for a reinstall

The capture describes the machine as it *is*; a reinstall needs it to describe
what to *build*. In `disks.json`:

- `"wipe_disk": true` and `"format": true` on the partitions you want created;
- replace `luks_uuid` with `luks_password` (a `$include_line` to
  `secrets/luks-passphrase`) — the same file for both encrypted partitions, so
  they cannot drift apart and the initrd asks once;
- drop disks that only hold data you are not reinstalling.

Full detail: [the capture guide](https://github.com/amt911/dasik/blob/main/docs/copy-your-config-and-test.md).

```bash
dasik check archlinux-p14s/main.json
git commit -am "arm archlinux-p14s for reinstall" && git push
```

## 7. Reinstall — this is the destructive part

Boot the Arch ISO in **UEFI** mode, get networking up (`iwctl`), then:

```bash
curl -fsSL https://raw.githubusercontent.com/amt911/dasik-aur/main/iso-bootstrap.sh -o bootstrap.sh
bash bootstrap.sh --config-repo amt911/dasik-personal-config
```

Download it rather than piping it into `bash`: the GitHub login is a device
code and needs a terminal. It installs dasik and clones the private repo to
`/root/config`.

The secrets are the one thing the repo does not carry, so put them back:

```bash
cd /root/config/archlinux-p14s
dasik hash-password > secrets/hashed-password
printf '%s\n' 'your-luks-passphrase' > secrets/luks-passphrase
dasik check main.json
```

Then:

```bash
lsblk                          # CONFIRM the device disks.json names
dasik plan  main.json          # read it. every change, in full
dasik apply main.json          # DESTRUCTIVE: partitions, formats, pacstraps
```

The config carries both tools as packages built from their PKGBUILDs, so the
installed machine has dasik and config-saver already.

## 8. Reboot, then bring `$HOME` back

**Not during the install.** `restore.archive` is a path *inside the target*, and
the target does not exist until `apply` has partitioned it.

```bash
gh auth login
gh release download archlinux-p14s -R amt911/config-saver-personal-config
```

**An age-encrypted archive has to be decrypted first.** dasik's restore runs
`config-saver --decompress --input <path>` with no `--identity`, which age
requires — so decrypt it yourself and point the config at the plain archive:

```bash
age -d -i ~/.config/age/key.txt -o /tmp/home.tar.gz home.tar.gz.age
sudo mv /tmp/home.tar.gz /root/home.tar.gz
```

```json
"config_saver": {
  "restore": [{"user": "andres", "archive": "/root/home.tar.gz"}]
}
```

```bash
sudo dasik apply --target / /root/config/archlinux-p14s/main.json
```

That second apply does only the restore. dasik marks it by the archive's
**content hash**, so re-running restores nothing and a newer capture at the
same path restores again.

Restoring by hand works too, and takes the identity directly:

```bash
config-saver --decompress -i home.tar.gz.age --identity ~/.config/age/key.txt
```

Then put the age key back where it belongs (`~/.config/age/key.txt`, mode
`0600`) so the timer can encrypt again.

## 9. From now on

```bash
sudo dasik plan --target / ~/config/archlinux-p14s/main.json   # what drifted
sudo dasik save ~/config/archlinux-p14s/main.json              # capture + commit + push
```

`save` validates the capture *before* committing it, runs Git as you rather
than as root, hands back ownership of what it wrote, and never stages a
gitignored file — your secrets stay out by construction. On a machine that
already matches, it does nothing at all.

The archive keeps itself: the timer runs daily. Re-upload when you want the
published one refreshed (`gh release upload … --clobber`).

## The traps, all of them observed

1. **`--target` defaults to `/mnt`** for `plan`/`apply` — the install target. On
   a running machine, pass `--target /` or you are describing an empty
   directory. `sync`, `save`, `generations` and `rollback` already default to `/`.
2. **The restore is a second apply, after the first boot.** There is nowhere to
   put the archive before the disk exists.
3. **An encrypted archive needs decrypting before dasik's restore** — it passes
   no `--identity`. Decrypt, then point `restore.archive` at the plain file.
4. **A capture comes back with `wipe_disk: false`** and this machine's
   `luks_uuid`. Arming it for a reinstall is a deliberate edit (step 6).
5. **config-saver with no document anywhere exits 6.** Since 3.3.0 there is no
   fallback to the shipped examples — they reach `~/.ssh` and `~/.config/rclone`,
   and installing a package must not start a daily timer that archives
   credentials nobody chose. `dasik plan` warns when `timer_users` is declared
   with an empty `configs`.
6. **Upgrading config-saver leaves the timer inert.** A `.timer` that changed is
   reloaded but not re-armed, so `systemctl list-timers` shows an empty `NEXT`
   and no backup ever runs. `sudo systemctl restart config-saver@$USER.timer`
   (the package names the affected timers on upgrade).
7. **A configuration's name is its file name**, and declaring one in the dasik
   config does not create it until `apply` runs. On the machine you are
   capturing *from*, the documents that exist are the ones in
   `~/.config/config-saver/configs.d`. `config-saver --show-configs` is the
   answer to "what can I export?" — there is no `dotfiles` unless you made one.
8. **Nothing prunes the archives.** A daily timer keeps every run forever;
   114 copies of one document filled 7.2 GB on a machine already at 96%.
9. **Never commit a run log or an archive.** The `.gitignore` in step 3 covers
   the logs; archives are release assets and never enter Git.

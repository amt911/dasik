# Issue #249 — a declarative VPN, `/etc/hosts` by default, and a pass over the procedures

Date: 2026-08-16
Issue: [#249](https://github.com/amt911/dasik/issues/249)

## What the issue asks

> me refiero a que se puede poder declarar la vpn que se quiere para instalarse con
> networkmanager de forma nativa, que sale en la wiki y lo tengo asi, asi como con el otro
> de systemd (es que no me acuerdo como se llama ahora)
>
> ademas, se debe configurar el archivo /etc/hosts tambien, por defecto, como en la wiki
> recomienda.
>
> Ademas, se debe hacer una pasada de todos los procedimientos, para ver si hay cambios,
> como en sd boot, que ya no hace falta el hook de pacman para actualizarse, ya que hay
> servicio oficial, cosas del estilo.

Three deliverables, shipped as three PRs.

## A. The `wireguard` block half-works, and the half that works leaks the private key

`expand_wireguard` (`dasik/lib/expand/toggles.py:73`) does deliver a wg-quick tunnel:
`wireguard-tools`, `wg-quick@<iface>.service`, and the conf written to
`/etc/wireguard/<iface>.conf`. What is missing is what #249 asks for — and two defects,
both measured, not guessed.

**Defect 1 — the tunnel is written world-readable.** The toggle contributes no `mode`:

```python
$ python -c "from dasik.lib.expand import expand_config; ..."
expanded files: [{'path': '/etc/wireguard/wg0.conf', 'content': '[Interface]\nPrivateKey = …'}]
mode present?  [None]
```

and `DropFilesAction._write_content` with `mode=None` falls to a plain `open(path, "w")`,
i.e. `0644`. The writer's own docstring is the indictment:

> A declared mode is there because the content IS a secret — a WireGuard or NetworkManager
> private key.

`EtcFile.mode` has existed since #162 and this is the one caller that most needs it and does
not use it. wg-quick warns and carries on, so nothing ever failed loudly.

**Defect 2 — `sync` captures the same private key twice.** `DropFilesAction._discover_wireguard`
reports the conf with `mode: "0600"`; the toggle contributes the same file *without* a mode, so
`subtract_contributions` compares two unequal dicts and strips nothing:

```
after subtract, files kept: [{'path': '/etc/wireguard/wg0.conf', 'content': '…', 'mode': '0600'}]
```

The captured config then carries the tunnel in the `wireguard` block **and** as a `files`
entry. Turning the block off afterwards no longer removes the tunnel: the orphan `files` entry
keeps writing it.

**What is missing outright:** the NetworkManager backend (#249's actual request), a tunnel that
lives in a file next to the config instead of as an escaped one-liner inside the JSON, and a
capture that comes back as the block rather than as a raw file. Three tracked configs declare
the block — `install-chunga.json`, `vm-chunga-full.json`, `install-megamix.json` — so all three
ship a world-readable key today.

A comment in `drop_files_action.py:312` claims the capture is already handled —

```python
# captures it verbatim (as the `wireguard` config block already does)
```

— which is what Defect 2 disproves.

### The decisions taken (dialogue, 2026-08-16)

| Question | Answer |
| --- | --- |
| Scope | WireGuard only, and **wg-quick counts as a backend** |
| How the tunnel is declared | a **wg-quick-style file next to the config**, named from the JSON |
| Translation | **dasik never translates** ("me parece tontería") |
| systemd-networkd | served by wg-quick, which is manager-independent |
| Backend choice | derived, with a per-tunnel override |
| Idempotency | by the **content of the source file** |

One derivation moved as a consequence of "never translate": the backend is derived from **the
source file's own format**, not from `network.type`. With no conversion, the file *is* the
backend — a `.nmconnection` can only be served by NetworkManager. `network.type` is still
consulted, but only to warn (an NM keyfile on a `systemd-networkd` machine is a file nobody
reads).
| `sync` | writes the tunnel file **next to the config** |

### The shape

```jsonc
// config/main.json
"wireguard": [
  { "name": "eu-mad", "source": "wg/eu-mad.conf" },          // backend: auto
  { "name": "work",   "source": "wg/work.nmconnection",
    "backend": "networkmanager", "enable": false }
]
```

```
config/
├── main.json
└── wg/
    ├── eu-mad.conf            [Interface] / [Peer]  — wg-quick's own format
    └── work.nmconnection      [connection] type=wireguard — NM's own format
```

**The source file is in the backend's native format, and dasik moves it verbatim.** That is
the whole design decision: the two formats the Arch wiki documents (WireGuard §5.1 wg-quick,
§5.5.1 NMConnection file) are both files a tool already reads, so dasik places them with the
right owner and mode and gets out of the way.

Model (`wireguard_model.py`, replacing the dead `WireguardModel`):

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name` | str | — | interface / connection id. `[A-Za-z0-9_=+.-]{1,15}` (IFNAMSIZ) |
| `source` | str | — | path **relative to the config that names it**, no `..` |
| `backend` | `auto\|wg-quick\|networkmanager` | `auto` | `auto` reads the file's format |
| `enable` | bool | `true` | wg-quick: enable `wg-quick@<name>.service` |

`JsonModel.wireguard: Optional[List[WireguardTunnel]]`. The old dict shape (`enable`,
`interface_name`, `config_content`) is **rejected with a message that names the new shape** —
it never did anything, so nothing can regress, and a silent re-interpretation of a block that
holds a private key is worse than an error. The three sample configs are migrated.

### How it is delivered: the existing expand toggle, rewritten

No new file-writing domain: `expand_wireguard` already is one, and it is rewritten rather than
replaced — now per tunnel, with the `mode` that Defect 1 is missing:

| Backend | Contributions |
| --- | --- |
| `wg-quick` | `files` += `/etc/wireguard/<name>.conf` mode `0600` · `packages` += `wireguard-tools` · `systemd.enable_units` += `wg-quick@<name>.service` (when `enable`) |
| `networkmanager` | `files` += `/etc/NetworkManager/system-connections/<name>.nmconnection` mode `0600` · `packages` += `networkmanager` |

Consequences, all of them the point of doing it this way:

- **It works offline**, during a fresh install, for *both* backends. NM's keyfile plugin
  reads `/etc/NetworkManager/system-connections/` at startup, so an install-time write is
  enough; no daemon, no `nmcli`, no first-boot unit. (`nmcli --offline connection import` does
  not exist — verified: *"command doesn't support --offline mode"*.)
- **`plan` shows it** through `[files]`, `[packages]` and `[systemd]`, like every other
  toggle-delivered feature, and `apply` converges in one pass.
- **The mode is load-bearing**: wg-quick and NetworkManager both *ignore a world-readable
  keyfile in silence*. `EtcFile.mode` (#162) already carries `"0600"`.
- `subtract_contributions` already strips toggle-derived `files`/`packages`/`units`, so a
  `sync` of a config that declares tunnels does not duplicate them.

The loader resolves `source` (`dasik/lib/json_parser/wireguard_source.py`), with the guards
`etc_tree` already uses: relative only, no `..`, must exist, must be UTF-8. Resolution happens
in the loader because only the loader knows where the config file is — so `dasik check`
catches a missing tunnel file with no target and no root.

### Backend detection, and the refusal to translate

`auto` sniffs the source: a `[Interface]` section ⇒ `wg-quick`; a `[connection]` section with
`type=wireguard` ⇒ `networkmanager`. Neither ⇒ error.

A **declared backend that disagrees with the file's format is a preflight error**, not a
conversion:

```
wireguard tunnel 'work' declares backend networkmanager, but wg/work.conf is in
wg-quick format. dasik does not convert between the two. Either use
backend "wg-quick", or import it yourself and declare the result:
    nmcli connection import type wireguard file wg/work.conf
```

Other preflight checks: duplicate `name`s; a name over 15 characters; `backend:
networkmanager` while `network.type` is `systemd-networkd` (**warning** — the keyfile will sit
there unread).

### Capture: `WireguardAction`, capture-only

`plan()` deliberately empty (the CAPTURE-ONLY pattern of `CpuAction`/`ReflectorAction`), so
`Reconciler.sync` reaches it. `import_state` scans the target:

- `/etc/wireguard/*.conf` (not symlinks) ⇒ a `wg-quick` tunnel; `enable` from `systemctl
  is-enabled wg-quick@<name>`.
- `/etc/NetworkManager/system-connections/*.nmconnection` whose body has `type=wireguard` ⇒ a
  `networkmanager` tunnel.

It emits the block, and the **body is written next to the config** by
`extract_to_wireguard_dir()` — the same mechanism `extract_to_etc_tree` uses (`write_back`'s
`extra_writes` + `modes`, so the JSON and the files are one all-or-nothing step, chmod'ed
`0600`). Default directory `wg/`; a tunnel already declared keeps **its own** `source` path, so
re-syncing rewrites the file in place instead of moving it.

`DropFilesAction` **yields both paths** (`_discover_wireguard`, `_discover_nm_wireguard` are
removed): with the block owning them, keeping the `files` discovery would capture every tunnel
twice on a bootstrap (`{}`) seed. This moves where an already-captured tunnel lands in the
user's config repo (from `etc_tree`/`files` to `wg/` + a `wireguard` block) — that migration is
the feature, and it is called out in the PR and the wiki page.

### Evidence required (the repo's own rules)

`tests/lib/test_feature_detectability.py` and `tests/lib/test_feature_sync_capture.py` grow a
row each, per backend:

- missing on the target ⇒ planned; present ⇒ silent; declared off but owned ⇒ REMOVE
  (the unit disabled, the keyfile deleted);
- machine has a tunnel ⇒ captured as its own block with its file written; machine has none ⇒
  nothing invented; **`sync` → `check` → `plan` silent**, `plan` → `apply` → `plan` silent;
- the domain driven with the block **removed** (the empty-config trap).

## B. `/etc/hosts` by default

`NetworkModel.add_default_hosts` defaults to **`false`** today; the block it writes is exactly
what `Network_configuration.html` recommends:

```
127.0.0.1 localhost
::1 localhost
127.0.1.1 <hostname>
```

The wiki's reason is not cosmetic: `nss-myhostname` covers most software, but some reads
`/etc/hosts` directly and would otherwise **resolve the local hostname over the network**.

Change: **default `true`**. A machine that lacks the block still captures `false` (sync reports
reality), so no capture is falsified; what changes is that a config which says nothing now gets
the wiki's file. Behaviour change, called out in the PR, the wiki and
`docs/config-reference.md`.

## C. A pass over the procedures

A spike: read what dasik does against the current Arch wiki, page by page, for every procedure
it automates — the `systemd-boot-update.service` case (a pacman hook that stopped being
necessary) is the shape of what is being looked for. Output is a report,
`docs/procedures-audit-2026-08.md`, plus one issue per real divergence. No code in that PR.

Pages in scope: Systemd-boot, GRUB, Dm-crypt (all subpages), Mkinitcpio, Dracut, Btrfs,
Snapper, Swap/Power management/Hibernation, Microcode, Pacman, Reflector, Users_and_groups,
Sudo, PAM, AppArmor, Audit framework, Firewalld, Uncomplicated_Firewall, NetworkManager,
Systemd-networkd, WireGuard, Zram, Systemd-timesyncd, Locale, Plymouth, Silent_boot,
Podman/Docker, Fstab, EFI_system_partition.

## Out of scope

- Translating between tunnel formats, in either direction.
- OpenVPN/IPsec as blocks: an `.nmconnection` of any type is already a `files`/`etc_tree` entry.
- Generating keys (`wg genkey`): dasik does not invent secrets, it places declared ones.
- `nmcli connection import` at apply time: needs a live daemon, and the keyfile write covers it.

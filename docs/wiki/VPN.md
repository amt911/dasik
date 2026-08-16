# VPN (WireGuard)

A tunnel is declared as **the file its backend already reads**, kept next to the
config, and named from the JSON:

```json
"wireguard": [
  { "name": "eu-mad", "source": "wg/eu-mad.conf" },
  { "name": "work",   "source": "wg/work.nmconnection" }
]
```

```
config/
├── main.json
└── wg/
    ├── eu-mad.conf            [Interface] / [Peer]        — wg-quick's format
    └── work.nmconnection      [connection] type=wireguard — NetworkManager's
```

**dasik never converts between the two formats.** Both are what a tool already
parses — the Arch wiki documents each one — so dasik places the file where that
tool looks for it, at mode `0600`, and gets out of the way.

| Backend | File goes to | Also |
| --- | --- | --- |
| `wg-quick` | `/etc/wireguard/<name>.conf` | installs `wireguard-tools`, enables `wg-quick@<name>.service` |
| `networkmanager` | `/etc/NetworkManager/system-connections/<name>.nmconnection` | nothing else: NM's keyfile plugin reads the directory itself |


## A wg-quick conf served by NetworkManager

The two formats are not interchangeable — a `.conf` is read by
`wg-quick@<name>.service`, a `.nmconnection` by NetworkManager's keyfile
plugin — so normally the file you have decides the backend, and `backend:
auto` says exactly that.

One direction converts, because a tool exists that does it properly:

```json
{"name": "vpn-es", "source": "wg/vpn-es.conf",
 "backend": "networkmanager", "enable": true}
```

dasik runs `nmcli --offline connection add` **on the target** and writes what it
prints to `/etc/NetworkManager/system-connections/<name>.nmconnection`, mode
0600. `--offline` is the whole trick: it needs no running daemon and no D-Bus,
so it works inside the chroot an install runs in, where `nmcli connection
import` cannot (`import` refuses offline mode outright). nmcli — not dasik —
reshapes the private key, which was the real objection to converting at all.

`enable` becomes `connection.autoconnect`, and the connection id, uuid and
interface name all come from `name`. The uuid is derived from that name rather
than left to nmcli, which invents a random one per call: without that the
keyfile differs on every run and the domain never converges.

**Why you would want it.** A `.conf` is what a provider hands you and is the
portable format — it works under either network manager. A keyfile only works
under NetworkManager, but it is the only one that appears in the KDE applet
with a switch. This lets the repository keep the portable file and the machine
get the native one.

**The other direction is still refused.** nmcli cannot emit a wg-quick conf and
wg-quick cannot read a keyfile, so a `.nmconnection` declared `wg-quick` is an
error rather than an invention.

**On a fresh install the plan is quiet about it.** The conversion needs `nmcli`,
which arrives with the `networkmanager` package the same apply installs, so at
plan time against an empty target there is nothing to ask. The apply does it,
and every later plan reports it normally.

## The fields

| Field | Default | Meaning |
| --- | --- | --- |
| `name` | — | the interface, and the file's name on the machine. 1–15 characters of `[A-Za-z0-9_=+.-]` — `IFNAMSIZ`, or `ip link add` fails after the config is already written |
| `source` | — | path to the tunnel file, **relative to the config that names it**. No `..`, no absolute paths, no symlinks: the file holds a private key and is copied verbatim |
| `backend` | `auto` | `auto` reads the file's own format. Say it explicitly and dasik checks you agree with the file |
| `enable` | `true` | wg-quick only: whether `wg-quick@<name>.service` is enabled |

## Which backend, and why wg-quick is the portable one

`auto` decides from the source file, not from `network.type`, because with no
conversion the file **is** the backend: an `.nmconnection` can only be served by
NetworkManager.

**wg-quick works under either network manager** — it is a systemd unit that runs
`wg` and `ip`, not a NetworkManager or systemd-networkd feature. So on a
`systemd-networkd` machine, a wg-quick conf is the way to declare a tunnel.

Declaring a NetworkManager keyfile on a `systemd-networkd` machine is a
**warning**: the file is written and nothing reads it.

## Declaring a backend that disagrees with the file

`.conf` + `networkmanager` is the convertible pair and is handled above. The
other way round is an error, because nothing can carry it out:

```
wireguard tunnel 'work' declares backend wg-quick, but its source file is a
NetworkManager keyfile. dasik does not convert that direction: nmcli cannot
emit a wg-quick conf, and wg-quick cannot read a keyfile. Use backend
"networkmanager", or declare the wg-quick conf the tunnel came from.
```

A file in neither format is an error too — a tunnel nobody can read is worth
proving early rather than at boot.

## The mode is not decoration

Both files hold the interface's `PrivateKey`. wg-quick warns about a
world-readable conf and carries on; NetworkManager **ignores** such a keyfile in
silence. dasik writes `0600` with the mode on the descriptor before any content
reaches it, so the key is never briefly readable, and `plan` reports a tunnel
someone left at `0644` as a change to repair.

## It works during an install

Both backends, in the chroot, with no daemon running: NetworkManager reads
`/etc/NetworkManager/system-connections/` at startup, so writing the keyfile is
the whole of what configuring it takes. (`nmcli` needs a live daemon and
`nmcli --offline connection import` does not exist — the command refuses offline
mode.)

## What `sync` brings back

The tunnels a machine holds come back as the block, with each body written to a
file beside the config — `wg/<name>.conf` or `wg/<name>.nmconnection` — chmod'ed
`0600` in the same all-or-nothing write as the JSON. A tunnel the config already
declares keeps **its own** `source` path.

Inline, a tunnel would be an escaped one-liner holding a private key: unreadable
in a diff, and a JSON string cannot carry the mode the file had.

`sync` reads both `/etc/wireguard/*.conf` and the NetworkManager keyfiles whose
type is `wireguard`; other NM connections (wifi, ethernet) are not touched.

> **Migrating.** A machine synced with an older dasik carries its tunnel as a
> plain `files` entry (or inside `etc_tree`). The first `sync` after this change
> captures it as a `wireguard` block in `wg/` instead. Delete the old entry —
> keeping both means two owners for one file.

## Keys

dasik places declared secrets; it does not invent them. Generate a keypair the
way the wiki does:

```bash
wg genkey | (umask 0077 && tee peer.key) | wg pubkey > peer.pub
```

A repository holding tunnel files holds private keys. Keep it private.

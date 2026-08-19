# Tailscale

A tailnet node is declared as **the file the daemon already reads**:
`/etc/tailscale/tailscaled.conf`, the conffile `tailscaled --config` takes.

```json
"tailscale": {
  "accept_routes": true,
  "accept_dns": true,
  "hostname": "archbox",
  "operator": "andres",
  "auth_key_file": "/etc/tailscale/authkey"
}
```

That block is a package, a unit, two files, and a node that is logged in by the
first boot. It is also a trade, stated plainly further down: while the conffile
is in use, `tailscale set` no longer moves these keys.

## Why a file and not `tailscale set`

Preferences otherwise live in `/var/lib/tailscale/tailscaled.state` — a database
no `plan` against an unmounted `/mnt` can read, so the domain would need a unit
converging on every boot instead of a diff dasik can show you. The conffile is a
plain file: readable, comparable and capturable **with the target cold**. Same
reasoning as [Firewall](Firewall.md), which reaches for `firewall-offline-cmd`
rather than the running daemon.

## What declaring the block does

| | What | Who writes it |
| --- | --- | --- |
| package | `tailscale` | the block's expansion |
| unit | `tailscaled.service` (enabled, not started — dasik never starts daemons) | the block's expansion |
| file | `/etc/default/tailscaled` — `PORT` and `FLAGS="--config=/etc/tailscale/tailscaled.conf"` | the block's expansion, as an ordinary [`files`](Configuration.md#files-dropped-into-etc) entry |
| file | `/etc/tailscale/tailscaled.conf` — the preferences themselves | the `tailscale` domain |

**The conffile alone changes nothing.** What actually points the daemon at it is
`/etc/default/tailscaled`, the `EnvironmentFile` the vendor unit reads.

**A `tailscaled.service.d` drop-in setting `FLAGS` does not work** — measured in
a guest: the EnvironmentFile wins, with or without `systemctl daemon-reload`.
pacman lists `/etc/default/tailscaled` under `Backup Files`, so owning it is the
vendor-sanctioned route: an upgrade writes a `.pacnew` instead of clobbering it.
`PORT` has to be written too, because the unit's `ExecStart` interpolates
`${PORT}` and an empty one is not a working command line — hence the `port`
field, which is the one field that is *not* a conffile key.

## The trade: the CLI loses these keys

While the conffile is in use, the daemon answers:

```
can't reconfigure tailscaled when using a config file; config file is locked
```

That is the ownership model, stated by tailscaled itself — and it is what makes
the domain visible to `plan` and capturable by `sync`. It also means
`tailscale set --accept-routes` is no longer the place to change your mind: the
config is.

Drop the block and dasik removes **both** files (the conffile explicitly, the
EnvironmentFile as an owned-but-no-longer-declared file), and the CLI works
again. Blanking the conffile would not do: an empty one locks the CLI out just
the same.

## Unset is not the same as declared-`false`

A field you leave out leaves the preference to tailscale. A field set to `false`
is **dasik's**, and locked. So declare what you mean to own, not everything.

## The fields

Every field maps to one key of the conffile's `alpha0` schema. The map is
explicit — see [the names that are not what they look
like](#the-schema-is-alpha0-and-was-verified-against-the-binary).

| Field | Type | Conffile key | Notes |
| --- | --- | --- | --- |
| `accept_routes` | bool | `AcceptRoutes` | accept subnet routes other nodes advertise |
| `accept_dns` | bool | `AcceptDNS` | accept the DNS config from the admin panel |
| `ssh` | bool | `RunSSHServer` | Tailscale SSH |
| `web_client` | bool | `RunWebClient` | the local web UI |
| `shields_up` | bool | `ShieldsUp` | block incoming connections from the tailnet |
| `exit_node` | string | `ExitNode` | IP, base name, or `auto:any`; a single token |
| `exit_node_allow_lan_access` | bool | `AllowLANWhileUsingExitNode` | reach the LAN while using an exit node |
| `advertise_routes` | list of CIDR | `AdvertiseRoutes` | **prefix length required** — `10.0.0.0/8`, not `10.0.0.0` |
| `advertise_exit_node` | bool | `AdvertiseExitNode` | offer this machine as an exit node |
| `hostname` | string | `Hostname` | a DNS label: letters, digits, hyphens, not leading or trailing |
| `operator` | string | `OperatorUser` | Unix user allowed to run `tailscale` without sudo |
| `netfilter_mode` | `on`\|`nodivert`\|`off` | `NetfilterMode` | |
| `posture_checking` | bool | `PostureChecking` | let the control plane collect device posture |
| `server_url` | URL | `ServerURL` | only for a self-hosted coordinator (Headscale) |
| `auth_key_file` | absolute path | `AuthKey` (`file:` form) | [logging in](#logging-in), below |
| `port` | 1–65535 | *(none)* | the daemon's listening port; goes to `/etc/default/tailscaled`, default `41641` |

An unknown field is a **config error**, not a silent drop: `accpet_routes` would
otherwise validate, render to nothing, converge, and never route a packet.

`advertise_routes` is validated as real CIDR because tailscaled refuses the whole
file on a bad prefix — that is a daemon that will not start, so it is worth
catching while it is still a config error.

## Logging in

**Conffile mode has no interactive login.** `tailscale up` and `tailscale login`
both answer `can't reconfigure tailscaled when using a config file`. The way in
is `auth_key_file`: the **absolute path, on the target**, of a root-owned `0600`
file holding a tailnet auth key. It renders as the conffile's `AuthKey` in its
`file:` form, so only the *path* ever reaches a config that `dasik save` commits
to Git.

There is deliberately **no `auth_key` field**. A key pasted into the field is
refused (`tskey-…`), and so is a relative path.

### On a machine with no key file yet

```
warning: tailscale.auth_key_file declares '/etc/tailscale/authkey', which does
not exist on the target — writing the conffile WITHOUT AuthKey (a dangling
file: reference stops tailscaled from starting)
```

This is the design, not a fault. A `file:` reference pointing at nothing stops
`tailscaled` from starting **at all** — measured in the guest oracle — so a key
you have not provisioned yet must not take the daemon down. dasik leaves the
entry out, converges the rest, and the next `plan` after the file appears shows
the `MODIFY` that adds `AuthKey`.

### Provisioning it

```bash
# 1. https://login.tailscale.com/admin/settings/keys -> Generate auth key
#    Ephemeral OFF   — an ephemeral node disappears when it goes offline
#    Pre-approved ON — only if the tailnet requires device approval
#    Tags            — only if your ACL demands them

# 2. the file, readable by root alone
sudo install -d -m 0755 /etc/tailscale
printf '%s' 'tskey-auth-…' | sudo tee /etc/tailscale/authkey >/dev/null
sudo chmod 0600 /etc/tailscale/authkey

# 3. now the plan has something to say
sudo dasik plan  --target / config/main.json      # + AuthKey
sudo dasik apply --target / config/main.json
sudo systemctl restart tailscaled   # dasik enables units, it never restarts them
tailscale status
```

`printf`, not `echo`: the file holds the key and nothing else.

**During an install**, the same file goes to `/mnt/etc/tailscale/authkey` — but
`/mnt` does not exist until `apply` has partitioned it, so the install's own
apply always warns. Write the file from the ISO and run `apply` a second time, or
do it after the reboot. The long version, with the rest of the secrets a capture
only points at, is in [Adopt an existing
machine](Adopt-an-existing-machine.md#last-the-secrets-the-config-only-points-at).

**Afterwards** the key has done its job: the node's identity is in
`/var/lib/tailscale/tailscaled.state`, not in the auth key, so you may revoke it.
Leave the *file* in place though — delete it and every `plan` warns again, and
the next `apply` takes `AuthKey` back out of the conffile.

## What `sync` captures

The conffile is read back into the block, key by key. Three deliberate limits:

- **keys dasik does not model are dropped**, rather than surfaced as bogus config
  fields;
- **a literal `AuthKey`** somebody wrote into the conffile by hand is **never**
  captured — only the `file:` path form is. Copying a tailnet credential into a
  config bound for Git is the exact leak the field exists to avoid;
- **a declared block the machine does not have comes back empty** (`{}`), not
  omitted: `sync` reports reality, and a merge that only overwrites keys would
  otherwise leave the stale declaration standing.

Both sides are rendered to canonical JSON (sorted keys) before comparing, so
re-applying an unchanged config is a no-op whatever order the keys sit in on
disk.

## The schema is `alpha0` and was verified against the binary

The conffile schema ships **no documentation**. Every key above was pinned by
`scripts/vmtest/guest-tsspike.sh`, using tailscaled's own behaviour as the
oracle: an unknown key is a hard error (`json: unknown field`), so a candidate
that *starts* the daemon is a real key and one that does not is not.

Three plausible names turned out to be wrong — each would have produced a daemon
that refuses to start:

| Guessed | Actual |
| --- | --- |
| `ExitNodeAllowLANAccess` | `AllowLANWhileUsingExitNode` |
| `SSH` | `RunSSHServer` |
| `NoSNAT` | `DisableSNAT` |

So the map is explicit and never mechanical case conversion: `accept_dns` does
not titlecase into `AcceptDNS` by rule, it is written down.

The `version` field is `alpha0` and nothing else: tailscaled refuses `v1alpha1`
(`unsupported "version" value … want "alpha0" for now`) and an absent one
(`no "version" field defined`).

## When it goes wrong

| Symptom | Cause |
| --- | --- |
| `tailscaled` fails to start after a hand edit | an unknown key, or a `file:` reference to a file that is not there |
| `tailscale set …` answers `config file is locked` | the block is declared; change the config, not the CLI |
| the node is offline after a reinstall, `plan` warns about `auth_key_file` | the key file was never put back — [provision it](#provisioning-it) |
| the config changed but the daemon did not | dasik writes files and enables units; it does not restart daemons. `systemctl restart tailscaled` |
| `advertise_routes entry '10.0.0.0' needs an explicit prefix length` | tailscaled parses these with `ParsePrefix`; give it `/8` |

## See also

- [VPN (WireGuard)](VPN.md) — the other tunnel, declared as its own backend's file
- [Feature blocks](Features.md#tailscale) — what the block expands into
- [Configuration reference](Configuration.md#tailscale-in-detail) — the fields, in the whole-config table

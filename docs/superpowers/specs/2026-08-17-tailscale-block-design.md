# A declarative `tailscale` block

Status: design approved, **not yet implemented**.

## The problem

`tailscale` the package and `tailscaled.service` the unit are already
declarable, and both `torre-amd` and `laptop-p14s` declare them. What is not
declarable is a single preference. Turn on `--accept-routes` by hand and dasik
cannot see it, cannot plan it, and `sync` cannot capture it — the one-way street
[CLAUDE.md](../../../CLAUDE.md) warns about, waiting for the first pref anyone
sets.

## Mechanism: the conffile, not `tailscale set`

`tailscaled` accepts `--config <file>`. That file is the declarative surface,
and it makes dasik's ownership model exact: while it is in use the daemon
**refuses** CLI reconfiguration of the keys it defines.

Rejected alternative: `tailscale set` driven by a oneshot unit. It keeps the
stable CLI and lets the user keep poking prefs by hand, but the state then lives
in `tailscaled.state` — so `plan` against `/mnt` cannot see a divergence (no
daemon), and the domain needs a unit running on every boot to converge. The
precedent is `FirewallAction`, which uses `firewall-offline-cmd` for exactly
this reason: a mechanism that works with the target unmounted beats one that
needs the service up.

## Schema, pinned empirically

Read from the binary, not from documentation, because `alpha0` ships none. Probe
transcript: `scripts/vmtest/guest-tsspike.sh`, run in a guest on tailscale
**1.102.2** (the host runs 1.98.10; both accept only `alpha0`).

| Question | Answer |
| --- | --- |
| `"version"` | `"alpha0"` is the only accepted value, and the field is **mandatory** — `{}` is rejected with `no "version" field defined`, `"v1alpha1"` with `unsupported "version" value` |
| key spelling | Pascal (`AcceptRoutes`) and camel both parse; **kebab does not** — `json: unknown field "accept-routes"` |
| unknown key | **hard error**, `json: unknown field "totalNonsenseKey"` — the file uses `DisallowUnknownFields` |
| wrong type | **hard error**, `invalid opt.Bool value "\"yes\""` |
| `tailscale set` on an owned key | refused: `can't reconfigure tailscaled when using a config file; config file is locked` |

**The silent-failure class does not exist here**, which is the happy surprise: a
typo in dasik's writer cannot produce a converged plan and no effect, because
`tailscaled` refuses to start at all. The failure is loud by construction.

### conffile → prefs is NOT the identity mapping

This is what `import_state` must encode, and what intuition gets wrong:

| conffile key | `tailscale debug prefs` key |
| --- | --- |
| `AcceptRoutes` | **`RouteAll`** |
| `AcceptDNS` | **`CorpDNS`** |
| `ShieldsUp` | `ShieldsUp` |
| `Hostname` | `Hostname` |

Verified live in the guest: a conffile of
`{AcceptRoutes: true, AcceptDNS: false, Hostname: "spike-host", ShieldsUp: true}`
produced prefs `RouteAll: true, CorpDNS: false, Hostname: "spike-host",
ShieldsUp: true`.

## Files dasik owns

Two, both plain text, neither owned by pacman:

1. **`/etc/tailscale/tailscaled.conf`** — the conffile. Owned by the new action.
2. **`/etc/systemd/system/tailscaled.service.d/10-dasik.conf`** — points the
   daemon at it.

The vendor unit is
`ExecStart=/usr/sbin/tailscaled --state=… --socket=… --port=${PORT} $FLAGS`
with `EnvironmentFile=/etc/default/tailscaled`. So the drop-in sets the variable
rather than rewriting the command:

```ini
[Service]
Environment=FLAGS=--config=/etc/tailscale/tailscaled.conf
```

`Environment=` in a later-applied drop-in wins over the `FLAGS=""` the
`EnvironmentFile` supplies. **`/etc/default/tailscaled` is never touched** — it
is pacman's and, on the real machines, byte-identical to the package
(`pacman -Qkk tailscale` → 0 altered files).

**A guest must prove the drop-in actually outranks the EnvironmentFile.** The
repo has already shipped "a systemd drop-in another file outranked: planned,
applied, planned again, forever". Asserting the file exists is not the same as
asserting the daemon got the flag; read `/proc/<pid>/cmdline`.

## Config surface

```json
"tailscale": {
  "accept_routes": true,
  "accept_dns": true,
  "ssh": false,
  "shields_up": false,
  "exit_node": null,
  "exit_node_allow_lan_access": false,
  "advertise_routes": [],
  "advertise_exit_node": false,
  "hostname": null,
  "operator": "andres",
  "auto_update": null
}
```

Snake_case in dasik (repo convention), rendered to the conffile's Pascal keys by
an explicit map — never by mechanical case conversion, since `accept_dns` →
`AcceptDNS` is not what a naive titlecase produces, and a wrong key is a daemon
that will not start.

**`auth_key` is deliberately absent.** The conffile accepts one, and it is a
tailnet credential; putting it in a config that `dasik save` commits is how a
secret reaches Git. Login stays a manual `tailscale up` — the node key in
`/var/lib/tailscale/tailscaled.state` is the machine's identity and is not
portable between machines by design.

## What must be true before this is called done

- **Detectable by `plan`**: absent on the target ⇒ change planned; present ⇒
  silent; declared off but owned in the manifest ⇒ REMOVE (both files taken
  back). An unowned conffile someone else wrote is left alone.
- **Capturable by `sync`**: the conffile reads back as a `tailscale` block; a
  machine without one invents nothing; `sync` → `check` → `plan` ends silent.
- **The block REMOVED**, not merely off: the reconciler hands the action its
  *empty* config when a previous generation owned the domain. An empty conffile
  is not the same as no conffile — an empty one would still lock the CLI out.
- **Every verb**, as round trips, per CLAUDE.md.
- **A guest**: `config/vm-tailscale.json` + `scripts/vmtest/guest-tailscale.sh`,
  asserting the daemon's real `/proc/<pid>/cmdline`, that `debug prefs` matches
  what was declared, and that dropping the block removes both files and frees
  the CLI again.

## Then, and only then

`accept_routes: true` in `torre-amd.json` and `laptop-p14s.json`. Note the
consequence for the user: with the block declared, `tailscale set
--accept-routes=false` stops working on those machines and answers
`config file is locked`.

There is also a live finding worth acting on independently: `tailscale status`
on `torre-amd` reports *"Some peers are advertising routes but --accept-routes
is false"*, which is why this came up.

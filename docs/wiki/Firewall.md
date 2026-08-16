# Firewall

One block, two backends. `backend` decides which tool is installed and driven;
the default is `firewalld`, so every config written before this existed keeps
its meaning.

```json
"firewall": {
  "enable": true,
  "backend": "firewalld",
  "allowed_services": ["samba", "samba-client", "syncthing"],
  "remove_services": ["ssh"],
  "rich_rules": ["rule service name=\"ssh\" accept limit value=\"2/m\""]
}
```

```json
"firewall": {
  "enable": true,
  "backend": "ufw",
  "rules": ["limit 22/tcp", "allow 22000/tcp", "allow 21027/udp"]
}
```

**Never both.** firewalld and ufw are each a front-end to netfilter, and each
one owns the whole rule set: whichever starts last wipes the other's rules, so
the machine's actual policy would depend on unit ordering. preflight refuses a
config that declares one backend while the other's package is also declared.

## More than one zone

The top-level fields **are** the `public` zone. A machine that also customises
`home`, `work` or `internal` declares those under `zones`:

```json
"firewall": {
  "enable": true,
  "allowed_services": ["samba", "syncthing"],
  "remove_services": ["ssh"],
  "rich_rules": ["rule service name=\"ssh\" accept limit value=\"2/m\""],
  "zones": {
    "home": {"allowed_services": ["ssh", "mdns", "samba"]}
  }
}
```

An extra zone's `allowed_services` is its **complete** service list, not a diff:
naming a zone is already the whole statement, so nothing is merged in
underneath it — firewalld's own `home` allows `mdns` and `dhcpv6-client`, and a
config that did not ask for them does not get them. `remove_services` therefore
has no meaning inside a zone and the schema refuses it there. `public` is
refused too: it is the top level, and two ways to say it would let one config
contradict itself.

Each declared zone is a **separate item** in the plan and in the manifest, so
dropping one from the config deletes its file — a zone nothing declares any
more must stop enforcing rules:

```text
~ [firewall] modify home  (zone rules)
- [firewall] remove work  (no longer declared)
```

`sync` captures every zone that has a file in `/etc/firewalld/zones` — which is
exactly the set somebody customised, since firewalld keeps the untouched ones in
`/usr/lib/firewalld/zones`. Before this, only `public` was captured, so a
machine carrying a customised `home` lost it the moment its capture was
re-applied.

## Which fields belong to which backend

| Field | firewalld | ufw |
| --- | --- | --- |
| `allowed_services` | a service firewalld knows (`samba`, `syncthing`) | an application profile from `/etc/ufw/applications.d` |
| `remove_services` | ✅ | ❌ — ufw denies all incoming by default, so there is nothing to remove |
| `rich_rules` | ✅ | ❌ |
| `rules` | ❌ | ✅ |

The wrong field for a backend is a **validation error**, not a silent drop. A
`rich_rule` quietly ignored under ufw would widen access without saying so — the
rate limit in `accept limit value="2/m"` is precisely the clause that keeps such
a rule narrow.

## firewalld: dasik owns the zone file

dasik writes the complete `/etc/firewalld/zones/public.xml`:

> (firewalld's upstream `public` defaults − `remove_services`) + `allowed_services` + `rich_rules`

Owning the file rather than driving `firewall-offline-cmd` sidesteps a real
quirk: `--remove-service` does not strip a built-in default, and
`--list-services` reports defaults as if they were set, so a `remove_service`
re-fired on every single apply. As a file, convergence is just "does the
content match".

`sync` reads the live permanent zone back through `firewall-offline-cmd` (not
`firewall-cmd`, which needs a running daemon and a D-Bus session, and fails
headless with *"Did not receive a reply"*). Rich rules round-trip verbatim.

## ufw: dasik reads the machine and drives the CLI

ufw's state lives in `/etc/ufw/user.rules`, which is *generated*. Writing it
directly would fight the tool, so this backend:

* **reads** convergence from `ufw status`, and
* **writes** through `ufw allow …` / `ufw --force enable`.

`ufw allow` is itself idempotent ("Skipping adding existing rule"), but a plan
that proposed the same rule forever would be a lie — hence the status parsing.
When the status cannot be read at all (install time: there is no running
firewall yet), the rules are planned rather than assumed present. Re-applying an
existing rule costs nothing; skipping a missing one would leave the machine
open.

`--force enable` rather than plain `enable`: the latter asks for confirmation
and would hang an unattended apply.

### Write rules the way ufw reports them

```json
"rules": ["allow 22/tcp", "limit 22/tcp", "allow 6000:6007/udp", "allow Syncthing"]
```

`allow ssh` is **rejected by the model**. ufw resolves the service name and then
prints the rule as `22/tcp`, so dasik could never tell an applied rule from a
missing one, and the plan would propose it forever. Write the port.

## smb and syncthing

The services the old imperative installer opened, in both dialects:

| | firewalld | ufw |
| --- | --- | --- |
| Samba | `"allowed_services": ["samba", "samba-client"]` | `"rules": ["allow 445/tcp", "allow 139/tcp"]` |
| Syncthing | `"allowed_services": ["syncthing"]` | `"rules": ["allow 22000/tcp", "allow 21027/udp"]` |
| SSH, rate-limited | `remove_services: ["ssh"]` + a rich rule with `limit value="2/m"` | `"rules": ["limit 22/tcp"]` |

Full examples: `config/vm-firewalld.json` and `config/vm-ufw.json`.

## What `sync` captures

Whichever firewall the machine actually runs. ufw is checked first, and only
when it is installed **and** reports live rules — on a machine carrying both
packages, the one with rules is the one describing reality. Otherwise the
firewalld zone is read as before.

## Related

- [Feature blocks](Features.md) — every optional block
- [Validation](Validation.md) — the backend-conflict check

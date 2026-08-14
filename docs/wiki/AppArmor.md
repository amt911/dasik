# AppArmor

Mandatory access control: each confined program gets a profile listing the files
and capabilities it may touch, and anything not listed is denied.

```json
"apparmor": {
  "enable": true,
  "audit": true,
  "extra_profiles": [
    {"name": "usr.bin.foo", "content": "profile foo /usr/bin/foo {\n  #include <abstractions/base>\n}\n"}
  ]
}
```

Declaring the block **is** the declaration — `enable` defaults to `true`.
`enable: false` exists so the block can stay in the config while switched off,
and because that is what `sync` reports for a machine carrying the package
without the kernel parameter.

## The package is not what turns it on

This is the part that catches people. `pacman -S apparmor` and
`systemctl enable apparmor.service` leave you with a machine where

```
$ aa-enabled
No - not available on this system.
```

and every profile inert. AppArmor is a Linux Security Module, and a LSM has to
be named on the kernel command line at boot. dasik derives it from the block:

```
lsm=landlock,lockdown,yama,integrity,apparmor,bpf
```

Two details in that list are load-bearing. AppArmor must be the first **major**
module — a different major module ahead of it takes the slot and AppArmor never
initialises. And `capability` is deliberately absent: the kernel always includes
it, and listing it is an error.

An explicit `lsm=` in your own `kernel_cmdline` wins, as always. `sync`
subtracts the derived parameter by name, so it comes back as the `apparmor`
block rather than as a parameter that looks hand-written.

## What dasik installs

| Declared | Package | Unit | Kernel parameters | Files |
| --- | --- | --- | --- | --- |
| `enable: true` | `apparmor` | `apparmor.service` | `lsm=…apparmor…` | — |
| `audit: true` (also) | `audit` | `auditd.service` | `audit=1`, `audit_backlog_limit=8192` | `/etc/tmpfiles.d/audit.conf` |

### Why `audit` is worth turning on

Without the audit daemon, denials go to the kernel ring buffer and are gone on
the next reboot; with it they land in `/var/log/audit/audit.log`, which is what
`aa-logprof` reads when you build a profile. `audit_backlog_limit=8192` keeps
early-boot records from being dropped before `auditd` is up to collect them.

Two things have to be true for a non-root user to read the log, and dasik does
both — VM-proven, because doing only the first leaves the directory root-only:

1. **`log_group = adm` in `/etc/audit/auditd.conf`.** auditd sets the mode of
   `/var/log/audit` itself at start, and with no `log_group` it enforces
   `0700 root:root` on every boot. dasik owns that single line and leaves the
   rest of the file alone (it is a pacman backup file, so an upgrade leaves a
   `.pacnew`). Dropping `audit` removes the line again.
2. **The tmpfiles override.** Arch ships `z /var/log/audit 700 root root`, and
   `systemd-tmpfiles` re-applies it on every upgrade — so without
   `z /var/log/audit 750 root adm` the log would go back to root-only after the
   next `pacman -Syu`.

Every declared user is added to `adm`.

**Why `adm` and not an `audit` group:** nothing on Arch creates an `audit`
group. The wiki tells you to run `groupadd -r audit` by hand, and dasik never
creates groups — declaring a group no package provides would make
`useradd -G audit` fail *after* the disk had been partitioned. The wiki's own
tip is to reuse an existing system group, and `adm` is the traditional
log-reading one.

## Profiles

`extra_profiles` are copied verbatim into `/etc/apparmor.d/<name>`. The name is
a file name, not a path — a `/` in it is rejected by the model.

They load **at the next boot**, or on `systemctl reload apparmor` on a running
system. dasik does not run `apparmor_parser` during an install: AppArmor is not
running inside the chroot, so there is nothing to load them into.

To write one, put the profile in complain mode first (`aa-complain`), exercise
the program, and let `aa-logprof` turn the denials into rules. The Arch wiki's
[AppArmor](https://wiki.archlinux.org/title/AppArmor) page and
`apparmor.d(5)` cover the syntax.

## What `sync` captures

| On the machine | In the captured config |
| --- | --- |
| no `apparmor_parser` binary | nothing — the block never appears |
| installed, `lsm=` names apparmor | `"apparmor": {"enable": true, …}` |
| installed, no `lsm=` naming it | `"apparmor": {"enable": false, …}` — the truth: it enforces nothing |
| `auditd` installed **and** `audit=1` set | `"audit": true` |
| files in `/etc/apparmor.d/` that pacman does not own | `extra_profiles` |

The profiles the package ships are skipped (they are implied by the package),
as are the `abstractions/`, `tunables/` and `local/` subdirectories — that is
AppArmor's own machinery, not something anybody wrote for this machine.

## Desktop notifications

```json
"apparmor": { "enable": true, "audit": true, "desktop_notifications": true }
```

A denial that only reaches `/var/log/audit/audit.log` is a denial nobody sees.
`desktop_notifications` runs the notifier on login: it adds `python-notify2`,
`python-psutil` and `tk` (aa-notify's optional dependencies — without them it
exits instead of notifying) and writes, for every declared non-root user,

```ini
# ~/.config/autostart/apparmor-notify.desktop
[Desktop Entry]
Type=Application
Name=AppArmor Notify
TryExec=aa-notify
Exec=aa-notify -p -s 1 -w 60 -f /var/log/audit/audit.log
```

The file lands through the [`home_files`](Configuration.md#home_files--inside-a-users-home)
domain, so it is planned, owned and removed like any other managed file — turning
the flag off deletes it, which matters for a file in `$HOME` that nothing else
would ever clean up.

**It needs `audit: true`** and the schema enforces that: aa-notify reads the
audit log and nothing else, so without the framework it would start on every
login and show nothing forever.

`sync` reports it from the machine — the block is asked whether any user's home
carries the entry — rather than letting it come back as an anonymous home file.

## Related

- [Feature blocks](Features.md) — every optional block
- [Boot chain](Boot.md) — where derived kernel parameters end up
- [Sync](Sync.md) — how capture decides what is yours

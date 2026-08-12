# PAM hardening

Three independent policies: lock an account after repeated failures, cap how
many processes one user can run, and enforce a password policy.

```json
"pam": {
  "faillock":  {"deny": 5, "fail_interval": 900, "unlock_time": 600, "persistent": true},
  "limits":    {"nproc_soft": 100, "nproc_hard": 200},
  "pwquality": {"enable": true, "minlen": 10, "difok": 6, "retry": 2}
}
```

Every sub-block is optional and independent — declare only what you want. An
absent sub-block is *not* the empty one: dasik simply does not manage that file.

## Where each one is written, and why there

| Sub-block | File | Mechanism |
| --- | --- | --- |
| `faillock` | `/etc/security/faillock.conf` | the whole file — it has no `.d` directory |
| `limits` | `/etc/security/limits.d/10-dasik.conf` | a real drop-in |
| `pwquality` | `/etc/security/pwquality.conf.d/10-dasik.conf` **and** `/etc/pam.d/passwd` | drop-in + the one PAM stack file dasik writes |

All of them are pacman **backup files**, so an upgrade leaves a `.pacnew`
alongside rather than clobbering dasik's version.

## faillock: no PAM edit needed

`pam_faillock.so` is already in Arch's `/etc/pam.d/system-auth` — since
pambase 20200721.1-2 it locks an account for 10 minutes after 3 failures. So
this is purely a matter of configuration, and dasik never touches the login
stack for it.

The default of 3 is easy to burn through with a long passphrase and the wrong
keyboard layout, hence dasik's `deny: 5`. `deny: 0` is rejected outright:
pam_faillock reads it as "disable the lockout entirely", which is the opposite
of what declaring this block means. Drop the sub-block instead.

**`persistent: true` is the one worth understanding.** By default the failure
records live in `/run/faillock`, which is a tmpfs — a reboot clears every
lockout, and an attacker with physical access to the power button can arrange a
reboot. With `persistent`, dasik writes `dir = /var/lib/faillock` and the
lockout survives.

> If you make lockouts persistent and use polkit ≥127, its helper agent may need
> `ReadWritePaths=/var/lib/faillock` in a drop-in — see the Arch wiki's Security
> page.

To clear a lockout yourself: `faillock --user <name> --reset`.

## limits: a ceiling for fork bombs

`* soft nproc 100` / `* hard nproc 200` in a drop-in. The soft limit is what a
process starts with and can raise up to the hard one with `prlimit`; the hard
limit is the wall. On a single-user desktop this buys little, which is why it is
off unless declared.

## pwquality: the only one that edits a PAM stack

`pam_pwquality.so` is **not** in Arch's stack, so a policy file alone would be
read by nobody. dasik therefore rewrites `/etc/pam.d/passwd`:

```
#%PAM-1.0
# Managed by dasik: the policy itself lives in
# /etc/security/pwquality.conf.d/10-dasik.conf.
auth		include		system-auth
account		include		system-auth
password	required	pam_pwquality.so retry=2
password	required	pam_unix.so use_authtok yescrypt shadow
```

`use_authtok` is load-bearing: it makes `pam_unix` accept the password
pwquality just validated instead of prompting for a new one. Without it the
policy is enforced and then bypassed in the same transaction.

The blast radius is deliberately small. `/etc/pam.d/passwd` is what the `passwd`
command reads — not `login`, not `sudo`, not the display manager. The worst
outcome of a mistake here is that you cannot *change* a password, which is
recoverable; editing `system-auth` or `system-login` can produce a machine
nobody can log into, which is why dasik does not.

The credits (`dcredit`, `ucredit`, `lcredit`, `ocredit`) use pwquality's own
convention: a **negative** value means "require at least this many characters of
that class". `-1` each is the usual intent — one digit, one uppercase, one
lowercase, one symbol.

`enforce_for_root` is off by default: root deliberately setting a weak temporary
password for somebody else is a legitimate thing to do.

## What removing the block does

Dropping a sub-block a previous generation owned is a change, and `plan` shows
it as `- [pam] remove <item>`:

| Item | Undo |
| --- | --- |
| `faillock` | the file is reduced to dasik's header, so pam_faillock falls back to its compiled-in defaults — the same meaning as the stock all-commented file |
| `limits` | the drop-in is deleted |
| `pwquality` | the drop-in is deleted and `/etc/pam.d/passwd` is restored to the four lines `shadow` ships |

Hardening dasik never wrote is left alone: ownership, not presence, is what
authorises an undo.

## What `sync` captures

The machine, never the config. A hand-written `faillock.conf` is captured just
as readily as dasik's own — whoever wrote it, it is what the machine enforces.

`pwquality` is captured **only** when `/etc/pam.d/passwd` actually loads the
module; a drop-in with no module in the stack is inert, and reporting it would
describe an enforcement that does not happen. A declared item the machine does
not have comes back cleared, not echoed.

## Deliberately not included

- **`pam_faildelay`** in `system-login`. It must be the *first* line of that
  file, and faillock already makes brute force infeasible. Editing the login
  stack for a marginal gain is a bad trade.
- **`/etc/security/access.conf`**. `pam_access` is already in the stack, so a
  wrong rule there takes effect immediately — and the classic wrong rule locks
  you out of your own console.

## Related

- [Feature blocks](Features.md) — every optional block
- [Validation](Validation.md) — what preflight checks before anything is written
- [Sync](Sync.md) — how capture decides what is yours

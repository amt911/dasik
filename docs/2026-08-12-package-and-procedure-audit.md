# Package and procedure audit — 2026-08-12

Issue [#173](https://github.com/amt911/dasik/issues/173) asks:

> Comprobar si ha habido cambios de paquetes o de procedimientos, similar a lo
> que ha pasado con el hook de sd-boot, que ahora tiene un servicio que se
> activa oficial.

That is a real class of bug rather than a hypothetical: dasik hard-codes package
names and procedures learned at a point in time, and upstream keeps moving. This
is a sweep of every package name the tool can declare, checked against today's
repositories.

## Method

Every name `dasik/lib/expand/toggles.py` can contribute was extracted from the
actual constants (not by grepping strings — that produces dozens of false
positives from config keys) and resolved with `pacman -Si`:

```python
names = set(_KVM_PKGS) | set(_HWACCEL_COMMON)
for v in _HWACCEL_DRIVER_PKGS.values(): names |= set(v)
for spec in _DRIVER_PKGS.values():      names |= set(spec["base"]) | set(spec["lib32"])
# plus every toggle's contribution for a config that enables it
```

51 distinct packages. Two of them no longer exist.

## Findings

### 1. `nvidia` — retired upstream (**live bug, fixed**)

```
$ pacman -Si nvidia
error: package 'nvidia' was not found
$ pacman -Si nvidia-open | grep Replaces
Replaces        : nvidia<=580.119.02-2
```

NVIDIA stopped shipping the proprietary kernel module, and Arch dropped
`nvidia`, `nvidia-dkms` and `nvidia-lts` with it. `nvidia-open` is what remains.

**Impact:** a config with `"drivers": ["nvidia"]` — including
`config/install-chunga.json`, the user's own desktop — reaches
`pacman -S nvidia …`, which aborts the entire transaction with *target not
found*. That happens in phase 3, after the disk has already been partitioned.

**Fix:** `_DRIVER_PKGS["nvidia"]["base"]` now names `nvidia-open`. The `nvidia`
key stays, because that is what existing configs say and it can only mean one
thing now.

### 2. `libva-mesa-driver` — folded into `mesa` (**live bug, fixed**)

```
$ pacman -Si libva-mesa-driver
error: package 'libva-mesa-driver' was not found
$ pacman -Si mesa | grep -E 'Provides|Replaces'
Provides        : libva-mesa-driver=1:26.1.6-1  mesa-libgl=1:26.1.6-1  …
Replaces        : libva-mesa-driver<1:24.2.7-1  mesa-libgl<17.0.1-2
```

The same thing that happened to `mesa-vdpau` (already handled, with a comment in
the source) has since happened to `libva-mesa-driver`.

**Impact:** identical to the above, for `"drivers": ["amd"]` and for the AMD
branch of `hardware_acceleration`.

**Fix:** dropped from both lists. `mesa` was already declared in the driver
list and is what actually ships the VA-API driver; the hwaccel AMD branch now
names `mesa` too.

Both names are now pinned by tests (`test_drivers.py`) that assert they are
*never* declared, so a future edit cannot reintroduce them silently.

### 3. `iptables-nft` — gone too (no action needed)

Not in the repos either; `iptables` provides it. dasik only mentions it in a
comment explaining why it is deliberately *not* declared, so nothing breaks —
but the comment now describes a package that does not exist. Left as is: the
reasoning it records (a conflict that `--noconfirm` cannot resolve) is still the
reason not to declare it.

### 4. Procedures — nothing new since block A

The sd-boot case that prompted this item was fixed in
[#174](https://github.com/amt911/dasik/pull/174): the AUR
`systemd-boot-pacman-hook` was replaced by systemd's own
`systemd-boot-update.service`. Re-checked this round:

| Procedure | Status |
| --- | --- |
| `systemd-boot-update.service` vs the AUR hook | already native (#174) |
| `plymouth` from the AUR | already in `extra` (#175) |
| `reflector.timer` | still the supported mechanism |
| `zram-generator` | still the supported mechanism |
| `power-profiles-daemon` | still current; `tuned` is an alternative, not a replacement |
| `snap-pac` pacman hooks | unchanged |
| `pam_faillock` in `system-auth` | still shipped by pambase, so PAM lockout needs no stack edit (relied on by #185) |
| `pwquality.conf.d/` | exists — the drop-in path #185 uses |

## What this suggests for the future

The two live bugs were both *silent until an install*: the package lists are
plain data, no test asserted the names resolve, and nothing on this machine ever
declared `drivers: ["nvidia"]` after the rename. A cheap guard would be a
periodic (not per-commit — it needs network and a synced pacman database) job
that resolves every name in `_DRIVER_PKGS` / `_KVM_PKGS` / the toggles with
`pacman -Si` and fails when one disappears. Worth its own issue.

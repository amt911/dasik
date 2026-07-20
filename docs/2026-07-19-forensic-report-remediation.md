# Remediation ledger — forensic report of 2026-07-19

Status of every finding in
[`2026-07-19-install-failure-forensic-report.md`](2026-07-19-install-failure-forensic-report.md),
what was changed, and what is deliberately **not** changed here.

Branch: `fix/forensic-2026-07-19`. All gates green at the end of the work:
`pytest` 1180 passed, coverage 90.9 % (gate 80 %), `mypy` clean (80 files),
`bandit` clean, mutation gate clean (112 killed, 2 documented equivalents).

Nothing destructive was executed: no `apply`, no `rollback`, no partitioning, no
real install. Every `execute()`/`apply()` path is exercised through mocks.

## Fixed in this branch

| ID | Finding | Fix |
| --- | --- | --- |
| F-04, F-17 | One failing AUR package aborts the whole install | `{"name": …, "optional": true}` package spec. Optional packages install in their own batch after the required ones; a failure is reported, excluded from the manifest and retried next apply. Unknown optional names skip even under `unknown="error"`. |
| F-06 | `systemctl enable/disable` ignored its exit code | `check=True`; a unit no package provides now aborts instead of being recorded as managed. |
| F-08 | Firewall rich rule silently lost `limit value="2/m"` | `_rich_rule_to_xml` is a consuming parser: the rate limit is emitted inside the action element and an unrepresentable clause (`log`, `audit`, `masquerade`, `NOT`) raises `ConfigValidationError` instead of being approximated. |
| F-09 | Dracut looked converged without an image | `actual_value()` additionally requires an initramfs image for every target kernel, no older than its inputs. |
| F-10 | mkinitcpio neutralizers written after Packages | New `PacmanHooksAction`, registered in phase 1 between the disk actions and pacstrap. `expand_initramfs` now contributes only the `dracut` package. |
| F-11 | `sync` imported a dracut host as mkinitcpio | Generator detection by effective ownership (dasik's neutralizer marker), not package coexistence. The test that encoded the wrong assumption was replaced. |
| F-12 | `crypttab` with `size512` / a `swap` entry for an undeclared device | Preflight rejects both (`crypttab_bad_option`, `crypttab_undeclared_device`); the inherited line was removed from the local configs. |
| F-13 | Snapper configured after the transactions it should protect | `SnapperAction` runs before `PackagesAction` and installs `snapper`/`snap-pac` itself when missing. |
| F-14 | `SnapperAction.import_state()` returned `{}` | Captures every `/etc/snapper/configs` entry with its `SUBVOLUME`. |
| F-15 | `plan`/`apply`/`sync` never ran pydantic | All verbs validate the schema first; `check` additionally runs preflight. |
| F-16 | No cross-field validation | New `dasik/lib/validation/preflight.py`: groups without a provider, display-manager units without their package, two display managers, DM config files for another DM, crypttab. Errors abort before the first mutation. |
| F-18 | `bootctl`/`grub-install`/`grub-mkconfig` best-effort | `check=True`; a failed `bootctl install` aborts before `loader.conf`/`arch.conf` are written. Same fix applied to `locale-gen`, `pacman -Sy`, `ln`/`hwclock`, `mkinitcpio -P`. |
| F-19 | pacstrap exits 0 with a failed internal hook | Those lines are extracted from pacstrap's output and warned about. |
| F-20 | `/var/tmp` created 0755 | Mountpoints carry their required mode; `/tmp` and `/var/tmp` are created `1777` (and fixed if they already exist wrong). |
| F-26 | `su failed (exit 1): — file dialogs …` | `Command.execute(label=…)` names the logical command; the message carries an excerpt of the error lines in output order plus the path to the full log. AUR helper/clone/makepkg runs are labelled. |
| F-29 | The install log was not gitignored | `log-llm.log` / `*-install.log` added, with a note that RunLogger records full argv. |
| §9.9 | `initramfs`/`bootloader` were free strings | Restricted to the implemented backends (`mkinitcpio`/`dracut`, `grub`/`sd-boot`/`systemd-boot`). |

### Config decisions applied (local, gitignored configs)

| Finding | Decision | Change |
| --- | --- | --- |
| F-05 | Podman, no Docker Engine | `docker` dropped from `andres`'s groups. |
| F-07 | Plasma Login Manager | `sddm.service` → `plasmalogin.service`; the three `sddm.conf.d` files migrated to `/etc/plasmalogin.conf.d/10-dasik.conf` (autologin, halt/reboot, uid range). `[Theme]` dropped (plasmalogin is fixed to Breeze) and `DisplayServer=x11-user` deliberately not carried over (it defaults to Wayland). |
| F-02, F-03 | Peripheral, external | `sunshine`, `epson-inkjet-printer-escpr`, `epsonscan2` and the three names that exist nowhere marked `optional: true`. |
| — | `sshd.service` was enabled with no `openssh` declared | `openssh` added. |
| F-12 | ZRAM is the swap | The `cryptswap` crypttab line removed. |
| — | Snapper packages/timers/subvolume with no `snapper` section | Section added (`root` → `/`). |

The tracked sample `config/install-megamix.json` gained `docker` (it uses the
group), and the three `disk-*.json` samples were rewrapped to the current
`DisksConfiguration` shape — they no longer validated at all.

## Not changed, and why

| ID | Finding | Reason |
| --- | --- | --- |
| F-01 | A partial apply leaves a formatted disk with no generation recorded | Mitigated (an optional failure no longer aborts; the manifest never claims what is not installed; the next `plan` rediscovers reality from pacman), but per-action checkpointing is a reconciler design change that deserves its own branch and its own review. |
| F-02, F-03 | Sunshine's `pkg_resources` transition, Epson's HTTP 403 | Not dasik's to fix. Do not work around a vendor CDN or install pip globally; the packages are optional now, so they no longer block a convergence. |
| F-21, F-22, F-28 | `lib32-gstreamer` test failures, `btdu`'s killed `gdb-add-index`, pacman provider defaults | Environmental/AUR-side; no dasik defect was demonstrated. |
| F-23, F-30 | `claude-cowork-service` obsolete, JDownloader needs manual onboarding | Config/product decisions for you: "package present" ≠ "application configured". |
| F-24 | The AUR build tree is always deleted | Deliberate: a resumable cache needs ownership, a PKGBUILD fingerprint and an invalidation policy before it is safe. |
| F-27 | Credentials in `config/test-config.json` | Yours to rotate; the file stays gitignored and no value was read into any output here. |
| F-31 | No `hostname`/`network` block | Intentional-looking, but the installed system will keep the ISO's defaults — decide before the next install. |

## Verification still owed (needs a disposable guest)

Everything above is verified with mocks, the CLI smokes (`--help`, `check` on
every tracked sample, `generations`) and the gates. **Not** verified: a real
install, a real boot, LUKS unlock, and a second no-op cycle. The boot-chain fixes
(F-09/F-10/F-11) in particular deserve a QEMU run before the next real install.

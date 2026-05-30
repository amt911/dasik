# Superpowers — Follow-ups Backlog

Post-implementation follow-ups discovered while executing the declarative-convergence
plans (`docs/superpowers/plans/`). Each links a GitHub issue for tracking. Pick these
up in future iterations (slice 2+).

## From Plan 5 — sync + generations + rollback (final review, PR #62)

- **Live-host warning (spec §5)** — [#63](https://github.com/amt911/dasik/issues/63).
  `--target /` + destructive changes should print a prominent "you are mutating the
  running host" warning. Not implemented for `apply` (Plan 4) or `rollback` (Plan 5).
  The destructive confirmation gate (`y/N` unless `--yes`) **is** present, so the safety
  floor holds — this is the extra heads-up. More relevant now that `rollback` defaults
  to `--target /`.
- **`setup_actions()` double-register** — [#64](https://github.com/amt911/dasik/issues/64).
  Appends to a process-global registry without clearing; a second call in one process
  would double-register every action. Not triggered today (one verb/process; tests patch
  it out), but now 5 call sites. Clear the registry at the top of `setup_actions()` or
  make `register_action` idempotent.
- **Coverage gate** — [#65](https://github.com/amt911/dasik/issues/65) — **resolved**.
  `pytest-cov` is declared in dev deps; measured via
  `PYTHONPATH=. pytest --cov=dasik`. Baseline was 41%; added `is_needed`/`verify`/helper
  tests across the v2 service/config/boot actions (`trim`, `bluetooth`, `systemd`, `cups`,
  `kvm`, `hw_accel`, `wireguard`, `firewall`, `ms_fonts`, `pacman`, `timezone`, `users`,
  `drop_files`, `mkinitcpio`, `kernel_cmdline`) plus `ActionExecutor`, bringing it to
  ~80%. `fail_under = 80` is now enforced in `[tool.coverage.report]`. Justified omits in
  `[tool.coverage.run]`: the legacy `actions_handler.py`, the destructive
  `disk_partition_action.py` (per CLAUDE.md §Tests), and the abstract-incomplete legacy
  actions below.

## Surfaced while raising coverage (#65) — latent, untracked

- **Abstract-incomplete legacy actions.** `LocaleAction`, `NetworkAction`,
  `BaseInstallAction` never implement `AbstractAction`'s abstract `name`/`is_needed`/
  `execute`, so they raise `TypeError` on instantiation — they are dead in the v2 path
  (registered in `setup_actions()` but `ActionExecutor` would crash if it reached them).
  Port them to the `is_needed`/`execute` contract (or v3) before relying on `locales`/
  `network`/base-install in the executor. Omitted from coverage until then.
- **`__root__` config-key validation quirk.** `ActionRegistry.validate_config` checks
  `config_key not in config` literally, so the `'__root__'` sentinel always reports the
  section as missing — optional `__root__` actions are always skipped and required ones
  always "fail" validation, before `_execute_action`'s `'__root__'` special-case runs.
  `DropFilesAction`/`MkinitcpioAction`/`BaseInstallAction` are affected. Make
  `validate_config` skip the existence check (and required-field check against root) when
  `config_key == '__root__'`.

## Deferred by design (spec §7 "Out") — not bugs, future slices

- Bootloader generation entries (select a generation at GRUB / systemd-boot).
- Disk convergence (repartition to match config; stays `format`-gated, never converged on drift).
- Version pinning / lockfile; pure content-addressed store.
- Migrate systemd / files / users / sysctl to the v3 contract — `packages` is the only
  v3 domain so far, so `sync`/`plan`/`apply` only round-trip that domain.
- Multi-domain actions (`Reconciler._domain_for` still raises on >1 domain per action).

## Known limitations (documented, accepted for slice 1)

- `sync` rewriting JSON loses comments / logical grouping (JSON has no comments). A
  `<config>.bak` backup is written before overwrite.
- `sync` captures drift as plain (un-prefixed) names — `pacman -Qqe` can't distinguish
  AUR packages from official ones. Declared `aur-` entries that survive keep their prefix;
  only newly-captured drift loses it.

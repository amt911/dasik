# Superpowers — Follow-ups Backlog

Post-implementation follow-ups discovered while executing the declarative-convergence
plans (`docs/superpowers/plans/`). Each links a GitHub issue for tracking. Pick these
up in future iterations (slice 2+).

## From Plan 5 — sync + generations + rollback (final review, PR #62)

- **Live-host warning (spec §5)** — [#63](https://github.com/amt911/dasik/issues/63) —
  **resolved**. `Reconciler.apply` now prints a prominent stderr warning when
  `target.root == "/"` and the plan has destructive changes, before the confirmation
  prompt. Single choke point covers both `apply` and `rollback`. Shown even under `--yes`
  (rollback defaults to `--target /`). The `y/N` gate is unchanged.
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
  `[tool.coverage.run]`: the legacy `actions_handler.py` and the destructive
  `disk_partition_action.py` (per CLAUDE.md §Tests). Coverage is now ~82%.

## Surfaced while raising coverage (#65) — latent, untracked

- **Abstract-incomplete legacy actions** — [#66](https://github.com/amt911/dasik/issues/66) —
  **resolved**. `LocaleAction`, `NetworkAction`, `BaseInstallAction` were ported to the
  `(config, context)` + `name`/`is_needed`/`execute`/`verify` contract. Destructive
  `execute()` bodies are marked `# pragma: no cover`; the decision logic is tested
  (100%/90%/96%). They are no longer omitted from coverage. `NetworkAction` was
  re-registered with `config_key='__root__'` since it needs the root-level `hostname`
  as well as the `network` section.
- **`__root__` config-key validation quirk** — [#67](https://github.com/amt911/dasik/issues/67).
  `ActionRegistry.validate_config` checks
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

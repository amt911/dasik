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
- **Coverage gate unverifiable** — [#65](https://github.com/amt911/dasik/issues/65).
  `pytest-cov`/`coverage` not installed in the dev env, so the CLAUDE.md 80% gate could
  not be measured for Plan 5 (145 tests pass, gate unverified). Add `pytest-cov` to dev
  deps and verify with `PYTHONPATH=. pytest --cov=dasik --cov-report=term-missing`.

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

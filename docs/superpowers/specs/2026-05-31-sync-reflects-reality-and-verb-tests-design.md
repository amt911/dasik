# Design: `sync` reflects reality (packages/systemd/users) + per-verb CLI tests

Date: 2026-05-31
Status: approved (design), pending implementation plan

## Context

Two related problems surfaced while running the CLI:

1. **`sync` drops owned-but-undeclared items.** For the set domains (`packages`, `systemd`,
   `users`) `import_state` returns `declared-survivors + drift(A \ D \ M)`. An item that is
   **present (A) and owned (M) but not declared (D)** falls into neither bucket, so it
   silently disappears from the synced config. After a prior `apply`/`sync` records ownership
   (M), a later `sync` no longer reflects those packages/units/users → "shows less than
   before". `sync` should reflect **reality**.

2. **No per-verb integration tests.** The existing `tests/test_cli_*.py` mock the registry
   out, so they only check argument routing — they never exercise the real actions through a
   verb. Nothing caught the sync-abort or the owned-undeclared drop. We want deterministic,
   always-identical tests that run each verb end-to-end.

## Decisions (from brainstorming)

- **Sync semantics fix scope:** `packages`, `systemd`, `users` (the set domains affected).
  `files` already captures only declared paths (no glob); `timezone`/`locale`/`initramfs`
  already capture reality directly; `kernel_cmdline.import_state` stays explicit-only (UUID
  portability) — unchanged.
- **Sync becomes a reality snapshot:** `import_state` captures **A (present) + declared
  intent, independent of M**. M is still recorded into the manifest by `Reconciler.sync`
  (`new_managed = sorted(actual())`) — only the *captured config fragment* changes.
- **Test strategy:** in-process CLI tests against a **fake root** (`tmp_path`) with
  `Command.execute`/`subprocess.run` mocked. Deterministic, no Docker, CLAUDE.md-compliant
  (never real pacman/disk). Docker E2E is out of scope.

## 1. Sync semantics fix

The unifying rule for the three set domains: **output = all declared (intent, preserved
verbatim incl. prefixes) ∪ everything present that is not declared**. Drop the `managed`
(M) parameter from the capture logic.

- **`PackagesAction.import_state`:**
  ```python
  original = list(self.config) if isinstance(self.config, list) else []
  declared_stripped = {_strip(t) for t in original}
  extra = sorted(self.actual() - declared_stripped)   # present, not declared
  return {self._PACMAN_DOMAIN: original + extra}
  ```
  (Keeps all declared with their `aur-` prefix; appends present-undeclared as plain names.
  No M.)
- **`SystemdAction.import_state`:** keep `self.units` / `self.sockets` verbatim; `drift =
  sorted(actual - set(d_on) - d_off)` (no M); route drift by suffix; `disable_units`
  preserved.
- **`UsersAction.import_state`:** keep all declared users (refresh attrs for present ones,
  keep absent ones as intent); `drift = sorted(actual - declared_names)` (no M); capture
  present drift users whose hash is readable (skip otherwise, per the #77 fix).

Idempotency: after a `sync`, the config declares A, so the next `sync` produces the same set
→ no churn.

## 2. Per-verb integration tests

New file `tests/cli/test_verbs_integration.py` (no Docker). A small harness:

- **Fake root:** `tmp_path` as `--target`. Populate `tmp_path/etc/...` /
  `tmp_path/var/lib/dasik/...` as needed so file-reading actions and the StateStore/
  GenerationStore work against a writable, isolated root.
- **Mocked system commands:** patch `dasik.lib.command_worker.command_worker.Command.execute`
  (and `subprocess.run` where legacy actions use it) with a controllable fake that returns
  canned stdout per `(cmd, args)` — so `pacman -Qqe`, `systemctl …`, `useradd`, etc. are
  deterministic and never touch the host.
- **`setup_actions()` real** (registry not mocked — unlike the existing `test_cli_*`), so the
  real actions run through each verb.
- Invoke via `dasik.__main__.main(["<verb>", str(cfg), "--target", str(tmp_path), …])` and
  assert exit code, captured stdout, and resulting files (config rewrite, `state.json`,
  generation dir).

Verb coverage:
- **plan:** renders the diff; exit 0; writes nothing.
- **apply:** with `--yes`, runs actions (Command called), writes `state.json` + a generation;
  re-apply is a no-op (idempotent).
- **sync:** rewrites the config to reflect the mocked reality (incl. previously-owned items —
  the regression test for problem 1); writes a `.bak`.
- **generations:** lists recorded generations after an apply.
- **rollback:** restores a prior generation's config and re-applies.

These lock in each verb's behaviour and would have caught both the sync-abort and the
owned-undeclared drop.

## 3. Testing (TDD, 80% gate)

- **Unit (RED first):** `import_state` for packages/systemd/users captures an owned+present
  but undeclared item (the bug); declared intent preserved; idempotent second pass.
- **Integration:** the per-verb harness tests above.

## Out of scope

- Docker / real-system E2E.
- Changing `kernel_cmdline`/`files`/scalar import_state.
- `pacman`/`network` composite migration (next slice).

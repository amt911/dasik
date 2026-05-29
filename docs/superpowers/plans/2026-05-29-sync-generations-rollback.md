# `sync` + `generations` + `rollback` verbs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish slice 1 of the declarative-convergence design. Add the system→config write-back verb `dasik sync`, plus the generation-management verbs `dasik generations` (list) and `dasik rollback [N]` (restore a generation's config and re-apply it). After this plan, every verb in the spec (§4) exists and the only spec component still missing — `ConfigWriter` — is built here.

**Architecture:** A new pure `ConfigWriter` splices per-domain fragments into the config dict (passing `metadata`/unknown keys through) and serializes JSON. `PackagesAction.import_state(managed)` becomes the per-domain reconciliation: starting from the declared tokens (order- and `aur-`-prefix-preserving), it drops owned-but-vanished entries (`M \ A`) and appends captured drift (`A \ D \ M`). `Reconciler.sync()` walks the v3 actions, builds the merged config via `ConfigWriter.merge`, records `M ← A` into a new manifest (no new generation, no system mutation), and persists it via the injected `StateStore`. The CLI grows three verbs that reuse the existing `StateStore` / `GenerationStore` plumbing; `rollback` restores a generation's config then drives the existing `Reconciler.apply()` path.

**Tech Stack:** Python ≥3.10 stdlib (`argparse`, `pathlib`, `json`, `hashlib`, `datetime`), pytest + `unittest.mock`. No new runtime deps.

**Spec:** [`docs/superpowers/specs/2026-05-27-declarative-convergence-and-sync-design.md`](../specs/2026-05-27-declarative-convergence-and-sync-design.md) — §2 (sync set-math), §3.2 (StateStore), §3.3 (GenerationStore), §3.5 (`import_state`), §3.6 (`Reconciler.sync`), §3.7 (ConfigWriter), §3.8 + §4 (CLI verbs `sync`/`generations`/`rollback`), §5 (safety), §6 (storage layout).

**Base branch:** `main` (Plan 4 merged via PR #61 → commit `dfd4253`).

**Suggested working branch:** `plan-5-sync-generations-rollback`.

**Out of scope (future TODO, explicit per spec §7 "Out"):**
- Bootloader generation entries (selecting a generation at GRUB/sd-boot).
- Disk convergence; version pinning / lockfile; pure content-addressed store.
- Migrating systemd / files / users / sysctl to v3 — **packages stays the only v3 domain**, so `sync` only round-trips the `packages` domain for now. The walk is generic, so new v3 domains join automatically once migrated.
- Multi-domain actions (the `Reconciler._domain_for` guard still raises on >1 domain).

**Known limitations (documented, acceptable for slice 1 per spec §3.7):**
- JSON has no comments → `sync` rewriting the config loses any hand-written comments / logical grouping. A `<config>.bak` backup is written before overwriting.
- `pacman -Qqe` cannot tell AUR packages from official ones, so **drift captured by `sync` is always written as a plain (un-prefixed) name.** Declared `aur-` entries that survive keep their prefix (we preserve the original token); only newly-captured drift loses it.

---

## Default `--target` rationale

`plan` / `apply` default to `--target /mnt` (their primary use is install-time, from the live ISO). The three verbs in this plan are inherently **day-2** operations on an already-installed, running system (you sync *from* a running machine; generations only exist *after* applies), so they default to `--target /`. This is consistent with the spec §4 examples (`dasik sync <config> [--target /]`). All three still accept `--target /mnt`.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `dasik/lib/state/config_writer.py` (new) | `ConfigWriter.merge(existing, fragments) -> dict` (pure splice, passthrough) + `ConfigWriter.write(config, path)` (JSON serialize). |
| `dasik/lib/actions/abstract_action.py` (modify) | `import_state` default signature gains optional `managed` arg: `import_state(self, managed=None)`. |
| `dasik/lib/actions/packages_action.py` (modify) | `import_state(managed=None)` becomes the order-/prefix-preserving sync reconciliation (∪ drift, \ vanished-owned). |
| `dasik/lib/reconciler/reconciler.py` (modify) | Add `Reconciler.sync() -> (new_config, Optional[Manifest])`: walk v3 actions, merge fragments, record `M ← A`, persist manifest. |
| `dasik/__main__.py` (modify) | Add `sync` / `generations` / `rollback` subparsers + `_cmd_sync` / `_cmd_generations` / `_cmd_rollback` + routing; import `ConfigWriter`. |
| `tests/lib/state/test_config_writer.py` (new) | `merge` override/add/passthrough/no-mutation; `write` round-trip. |
| `tests/lib/actions/test_packages_action_v3.py` (modify) | Add `import_state(managed=...)` tests: drift capture, vanished-owned drop, `aur-` preserved + dropped, declared-intent kept. (Existing zero-arg test stays green.) |
| `tests/lib/reconciler/test_reconciler_sync.py` (new) | `sync` orchestration: no-v3 no-op, fragment merge, `M ← A`, manifest persistence, bootstrap (absent config), no generation bump. |
| `tests/test_cli_sync.py` (new) | CLI `sync`: writes new config + `.bak`, "already matches" path, default/explicit target, missing-config error. |
| `tests/test_cli_generations.py` (new) | CLI `generations`: lists with current marker, empty message, target passthrough. |
| `tests/test_cli_rollback.py` (new) | CLI `rollback`: restore+apply, default-N (previous), missing generation error, user-abort exit code, target passthrough. |

---

## Task 1: `ConfigWriter` (pure merge + write)

**Files:**
- Create: `dasik/lib/state/config_writer.py`
- Create: `tests/lib/state/test_config_writer.py`

`ConfigWriter` is the spec §3.7 component. Keep it dumb: the set-math (∪ drift, \ vanished) lives in the actions' `import_state` (Task 2) and the walk lives in `Reconciler.sync` (Task 3). `ConfigWriter` only (a) splices already-computed per-domain values into a copy of the config dict — preserving key order, passing `metadata`/unknown keys through untouched, never mutating the input — and (b) serializes to JSON.

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/state/test_config_writer.py`:

```python
import json

from dasik.lib.state.config_writer import ConfigWriter


def test_merge_overrides_existing_domain():
    existing = {"packages": ["git"], "metadata": {"name": "demo"}}
    result = ConfigWriter.merge(existing, {"packages": ["git", "htop"]})
    assert result["packages"] == ["git", "htop"]


def test_merge_adds_new_domain_for_bootstrap():
    existing = {"metadata": {"name": "fresh"}}
    result = ConfigWriter.merge(existing, {"packages": ["git", "htop"]})
    assert result["packages"] == ["git", "htop"]


def test_merge_passes_through_unknown_keys_and_metadata():
    existing = {"packages": ["git"], "metadata": {"k": "v"}, "kvm": {"enabled": True}}
    result = ConfigWriter.merge(existing, {"packages": ["git", "vlc"]})
    assert result["metadata"] == {"k": "v"}
    assert result["kvm"] == {"enabled": True}


def test_merge_does_not_mutate_inputs():
    existing = {"packages": ["git"]}
    fragments = {"packages": ["git", "htop"]}
    ConfigWriter.merge(existing, fragments)
    assert existing == {"packages": ["git"]}  # untouched
    assert fragments == {"packages": ["git", "htop"]}


def test_merge_preserves_existing_key_order():
    existing = {"metadata": {}, "packages": ["git"], "kvm": {}}
    result = ConfigWriter.merge(existing, {"packages": ["git", "x"]})
    assert list(result.keys()) == ["metadata", "packages", "kvm"]


def test_merge_empty_fragments_returns_equal_copy():
    existing = {"packages": ["git"], "metadata": {}}
    result = ConfigWriter.merge(existing, {})
    assert result == existing
    assert result is not existing


def test_write_round_trips_through_json(tmp_path):
    path = tmp_path / "config.json"
    config = {"packages": ["git", "htop"], "metadata": {"name": "demo"}}
    ConfigWriter.write(config, path)
    assert json.loads(path.read_text()) == config


def test_write_accepts_str_path(tmp_path):
    path = tmp_path / "config.json"
    ConfigWriter.write({"packages": []}, str(path))
    assert json.loads(path.read_text()) == {"packages": []}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/lib/state/test_config_writer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dasik.lib.state.config_writer'`.

- [ ] **Step 3: Implement `ConfigWriter`**

Create `dasik/lib/state/config_writer.py`:

```python
"""ConfigWriter — splice reconciliation fragments back into the config (spec §3.7).

Pure dict manipulation + JSON serialization. The set-math (which packages to
add as drift, which to drop) is computed upstream (in each action's
``import_state`` and in ``Reconciler.sync``); ConfigWriter only writes the
already-computed per-domain values into a copy of the config, leaving
``metadata`` and any unknown keys untouched.

Limitation: JSON has no comments, so any hand-written comments / logical
grouping in the source config are lost on rewrite (acceptable for slice 1).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ConfigWriter:
    @staticmethod
    def merge(existing: dict[str, Any], fragments: dict[str, Any]) -> dict[str, Any]:
        """Return a new config dict with ``fragments`` spliced over ``existing``.

        - Existing keys keep their position; ``fragments`` overrides their value.
        - New keys (e.g. bootstrapping ``packages`` into a config that lacked it)
          are appended.
        - ``metadata`` and any unknown keys not in ``fragments`` pass through
          untouched.
        - Inputs are never mutated.
        """
        merged = dict(existing)
        for key, value in fragments.items():
            merged[key] = value
        return merged

    @staticmethod
    def write(config: dict[str, Any], path: "str | Path") -> None:
        """Serialize ``config`` to ``path`` as indented JSON (trailing newline)."""
        Path(path).write_text(json.dumps(config, indent=2) + "\n")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/lib/state/test_config_writer.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/state/config_writer.py tests/lib/state/test_config_writer.py
git commit -m "feat(state): add ConfigWriter (merge fragments + write JSON)

ConfigWriter.merge() splices already-computed per-domain values into a
copy of the config, preserving key order and passing metadata / unknown
keys through untouched; it never mutates its inputs. write() serializes
to indented JSON. The reconciliation set-math lives upstream (actions +
Reconciler.sync); ConfigWriter stays a dumb, pure splice (spec §3.7).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `import_state(managed)` — per-domain sync reconciliation

**Files:**
- Modify: `dasik/lib/actions/abstract_action.py`
- Modify: `dasik/lib/actions/packages_action.py`
- Modify: `tests/lib/actions/test_packages_action_v3.py`

Today `PackagesAction.import_state()` returns the full actual set (`{"packages": sorted(actual())}`), which is correct only for the bootstrap case (`M = ∅`). `sync` needs the full spec §2 semantics: capture drift (`A \ D \ M`), drop owned-but-vanished (`M \ A`), keep mere intent (`D \ A` not owned). It must also (a) preserve the original token order and (b) preserve the `aur-` prefix on surviving declared entries.

The method gains an **optional** `managed` argument (`import_state(self, managed=None)`) so the existing zero-arg call sites keep working — `managed=None` means `M = ∅`, which reproduces today's bootstrap behavior exactly.

**Why the prefix matters:** the declared config token is `aur-yay`, but `actual()` (`pacman -Qqe`) reports `yay`. So `managed` (`M`) and `actual` (`A`) are in *stripped* space, while the original config list is in *prefixed* space. We strip only for set membership, but keep the original token when emitting the kept list — so `aur-yay` stays `aur-yay`, and a vanished `yay` correctly drops the `aur-yay` token.

- [ ] **Step 1: Update the abstract default signature**

In `dasik/lib/actions/abstract_action.py`, change the `import_state` default (around line 149) from:

```python
    def import_state(self) -> Dict[str, Any]:
        """Return the config fragment that mirrors A (for ``sync``).

        v3 actions override this to capture drift back into the config
        (e.g. ``{"packages": [...explicitly installed packages...]}``).
        The default returns an empty dict.
        """
        return {}
```

to:

```python
    def import_state(self, managed: Any = None) -> Dict[str, Any]:
        """Return the config fragment that reconciles A back into the config
        (for ``sync``, spec §2).

        v3 actions override this to capture drift (``A \\ D \\ M``) and drop
        owned-but-vanished entries (``M \\ A``); ``managed`` is the per-domain
        managed set (``M``) from the manifest, or ``None`` (≡ ``M = ∅``) for
        bootstrap / legacy zero-arg call sites. The default returns an empty dict.
        """
        return {}
```

- [ ] **Step 2: Update the v3 test stub's signature**

In `tests/lib/actions/test_abstract_action.py`, the `_V3Action` stub overrides `import_state`. Update it (around line 43) so it matches the new optional-arg contract (keeps the existing zero-arg assertion green):

```python
    def import_state(self, managed=None):
        return {"packages": ["git"]}
```

- [ ] **Step 3: Add the failing `import_state(managed=...)` tests**

In `tests/lib/actions/test_packages_action_v3.py`, append at the end of the file:

```python
# ---------------------------------------------------------------------- #
#  Plan 5: import_state(managed) — sync reconciliation                   #
# ---------------------------------------------------------------------- #


def test_import_state_captures_drift_with_managed():
    """A \\ D \\ M is appended; declared+owned survive."""
    fake = _fake_command_run(stdout=b"git\nhtop\n")  # A = {git, htop}
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=["git"], context=_ctx("/"))
        frag = a.import_state(managed=["git"])  # M = {git}; htop is drift
    assert frag == {"packages": ["git", "htop"]}


def test_import_state_drops_owned_but_vanished():
    """M \\ A (owned, removed by hand) is dropped from the config."""
    fake = _fake_command_run(stdout=b"git\n")  # A = {git}; vim gone
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=["git", "vim"], context=_ctx("/"))
        frag = a.import_state(managed=["git", "vim"])
    assert frag == {"packages": ["git"]}


def test_import_state_preserves_aur_prefix_on_survivors_and_appends_drift():
    fake = _fake_command_run(stdout=b"git\nyay\nhtop\n")  # A = {git, yay, htop}
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=["git", "aur-yay"], context=_ctx("/"))
        frag = a.import_state(managed=["git", "yay"])  # htop is drift
    assert frag == {"packages": ["git", "aur-yay", "htop"]}


def test_import_state_drops_aur_entry_when_underlying_pkg_vanished():
    fake = _fake_command_run(stdout=b"git\n")  # A = {git}; yay gone
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=["git", "aur-yay"], context=_ctx("/"))
        frag = a.import_state(managed=["git", "yay"])
    assert frag == {"packages": ["git"]}


def test_import_state_keeps_declared_intent_not_owned_not_present():
    """D \\ A that is NOT owned (mere intent) is kept; sync never drops intent."""
    fake = _fake_command_run(stdout=b"git\n")  # A = {git}; 'future' not installed
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=["git", "future"], context=_ctx("/"))
        frag = a.import_state(managed=[])  # M = {} → nothing vanished
    assert frag == {"packages": ["git", "future"]}


def test_import_state_zero_arg_still_bootstraps_full_actual():
    """Back-compat: no managed arg ≡ M = {} → capture all of A (bootstrap)."""
    fake = _fake_command_run(stdout=b"git\nhtop\n")
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=[], context=_ctx("/"))
        assert a.import_state() == {"packages": ["git", "htop"]}
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/lib/actions/test_packages_action_v3.py -k import_state -v`
Expected: FAIL — the new `managed=...` tests fail because the current `import_state()` ignores `managed` and returns the full actual set (e.g. `test_import_state_drops_owned_but_vanished` gets `["git"]`… actually returns `["git"]` only because vim isn't in A — but `test_import_state_preserves_aur_prefix...` returns `["git", "htop", "yay"]` sorted, not the prefixed/ordered list). Some also raise `TypeError` (current signature takes no `managed`).

- [ ] **Step 5: Implement the reconciliation in `PackagesAction.import_state`**

In `dasik/lib/actions/packages_action.py`, replace the current `import_state` (around lines 242-244):

```python
    def import_state(self) -> dict:
        """Config fragment derived from system reality (for sync, Plan 4)."""
        return {self._PACMAN_DOMAIN: sorted(self.actual())}
```

with:

```python
    def import_state(self, managed=None) -> dict:
        """Reconcile system reality back into the config fragment (sync, spec §2).

        Set semantics, order- and ``aur-``-prefix-preserving:
            keep    declared tokens whose stripped name did NOT vanish
            drop    owned-but-vanished  (M \\ A)  → declared token removed
            append  captured drift      (A \\ D \\ M) as plain (un-prefixed) names

        ``managed`` (M) is the per-domain managed set from the manifest, or
        ``None`` (≡ M = ∅) for bootstrap. Declared-but-absent entries that are
        NOT owned are mere intent and are kept (sync never drops intent).

        Note: ``pacman -Qqe`` cannot distinguish AUR packages, so drift is
        always captured as a plain name (the ``aur-`` prefix is only preserved
        on entries that were already declared with it).
        """
        managed_set = set(managed or [])
        actual = self.actual()
        original = list(self.config) if isinstance(self.config, list) else []

        def _strip(token: str) -> str:
            return token[len(AUR_PREFIX):] if token.startswith(AUR_PREFIX) else token

        declared_stripped = {_strip(t) for t in original}
        vanished = managed_set - actual                       # M \ A
        kept = [t for t in original if _strip(t) not in vanished]
        drift = sorted(actual - declared_stripped - managed_set)  # A \ D \ M
        return {self._PACMAN_DOMAIN: kept + drift}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/lib/actions/test_packages_action_v3.py tests/lib/actions/test_abstract_action.py -v`
Expected: PASS — new `import_state` tests green, existing zero-arg test green, abstract-action tests green.

- [ ] **Step 7: Run the full suite**

Run: `PYTHONPATH=. pytest -q`
Expected: PASS — everything still green.

- [ ] **Step 8: Commit**

```bash
git add dasik/lib/actions/abstract_action.py dasik/lib/actions/packages_action.py \
        tests/lib/actions/test_packages_action_v3.py tests/lib/actions/test_abstract_action.py
git commit -m "feat(actions): import_state(managed) reconciles reality for sync

import_state now takes an optional managed set (M) and applies the spec
§2 sync set-math: keep declared survivors, drop owned-but-vanished
(M \\ A), append captured drift (A \\ D \\ M). Order- and aur--prefix-
preserving. Zero-arg call (M = {}) still bootstraps the full actual set,
so existing call sites keep working.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: `Reconciler.sync()`

**Files:**
- Modify: `dasik/lib/reconciler/reconciler.py`
- Create: `tests/lib/reconciler/test_reconciler_sync.py`

`sync()` walks the v3 actions (same registry the `build_plan` walk uses), but — unlike `build_plan` — it does **not** skip actions whose config slice is absent: bootstrap is the whole point, so an undeclared domain still gets its reality captured (config slice defaults to empty). For each v3 action it collects `import_state(managed)` into a fragment dict, records `managed ← actual()` for the new manifest, then:

- merges the fragments into a new config via `ConfigWriter.merge`,
- builds a new `Manifest` with `managed = M←A`, a fresh `applied_at`, and `config_hash` of the **new** config, keeping the **same generation** (sync does NOT create a generation — only `apply` does, spec §1),
- persists the manifest via the injected `StateStore` (if present),
- returns `(new_config, new_manifest)`. No system mutation; the config-file write is the caller's job (`ConfigWriter.write`).

Returns `(self._config, None)` when there are no v3 actions (nothing to sync).

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/reconciler/test_reconciler_sync.py`:

```python
from unittest.mock import MagicMock

from dasik.lib.actions.abstract_action import AbstractAction
from dasik.lib.reconciler.reconciler import Reconciler
from dasik.lib.target.target import Target


class _SyncStub(AbstractAction):
    """v3 stub with configurable actual()/import_state()/managed_keys()."""

    _actual: set = set()
    _fragment: dict = {}
    _domain: str = "packages"

    @property
    def name(self) -> str: return "sync-stub"
    def is_needed(self) -> bool: return False
    def execute(self) -> None: pass
    def plan(self, managed): return []          # marks the class as v3

    def actual(self):
        return set(type(self)._actual)

    def import_state(self, managed=None):
        return dict(type(self)._fragment)

    def managed_keys(self):
        return {type(self)._domain: []}


def _meta(cls, config_key="packages"):
    return {
        "class": cls,
        "config_key": config_key,
        "is_optional": True,
        "required_fields": [],
        "depends_on": [],
    }


class _LegacyStub(AbstractAction):
    @property
    def name(self) -> str: return "legacy"
    def is_needed(self) -> bool: return False
    def execute(self) -> None: pass


def _make(*, config=None, manifest=None, metas=None, store=None):
    return Reconciler(
        config=config if config is not None else {"packages": ["git"]},
        target=Target(root="/"),
        manifest=manifest,
        action_metas=metas if metas is not None else [],
        state_store=store,
    )


def test_sync_no_v3_actions_returns_config_and_none():
    store = MagicMock()
    r = _make(metas=[_meta(_LegacyStub)], store=store)
    new_config, manifest = r.sync()
    assert new_config == {"packages": ["git"]}
    assert manifest is None
    store.save.assert_not_called()


def test_sync_merges_fragment_into_config():
    _SyncStub._actual = {"git", "htop"}
    _SyncStub._fragment = {"packages": ["git", "htop"]}
    r = _make(config={"packages": ["git"]}, metas=[_meta(_SyncStub)])
    new_config, _ = r.sync()
    assert new_config["packages"] == ["git", "htop"]


def test_sync_records_managed_as_actual():
    _SyncStub._actual = {"git", "htop", "vlc"}
    _SyncStub._fragment = {"packages": ["git", "htop", "vlc"]}
    r = _make(config={"packages": ["git"]}, metas=[_meta(_SyncStub)])
    _, manifest = r.sync()
    assert manifest.managed == {"packages": ["git", "htop", "vlc"]}  # sorted A


def test_sync_persists_manifest_via_state_store():
    _SyncStub._actual = {"git"}
    _SyncStub._fragment = {"packages": ["git"]}
    store = MagicMock()
    r = _make(config={"packages": ["git"]}, metas=[_meta(_SyncStub)], store=store)
    _, manifest = r.sync()
    store.save.assert_called_once_with(manifest)


def test_sync_does_not_bump_generation():
    _SyncStub._actual = {"git"}
    _SyncStub._fragment = {"packages": ["git"]}
    r = _make(
        config={"packages": ["git"]},
        manifest={"managed": {"packages": ["git"]}, "generation": 4},
        metas=[_meta(_SyncStub)],
    )
    _, manifest = r.sync()
    assert manifest.generation == 4  # unchanged — sync records no generation


def test_sync_bootstrap_captures_actual_when_config_section_absent():
    """Config has no 'packages' key → sync still captures reality into it."""
    _SyncStub._actual = {"git", "htop"}
    _SyncStub._fragment = {"packages": ["git", "htop"]}
    r = _make(config={"metadata": {"name": "fresh"}}, metas=[_meta(_SyncStub)])
    new_config, manifest = r.sync()
    assert new_config["packages"] == ["git", "htop"]
    assert new_config["metadata"] == {"name": "fresh"}  # passthrough
    assert manifest.managed == {"packages": ["git", "htop"]}


def test_sync_sets_config_hash_of_new_config():
    import hashlib, json
    _SyncStub._actual = {"git", "htop"}
    _SyncStub._fragment = {"packages": ["git", "htop"]}
    r = _make(config={"packages": ["git"]}, metas=[_meta(_SyncStub)])
    new_config, manifest = r.sync()
    expected = hashlib.sha256(
        json.dumps(new_config, sort_keys=True).encode("utf-8")
    ).hexdigest()
    assert manifest.config_hash == expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/lib/reconciler/test_reconciler_sync.py -v`
Expected: FAIL — `Reconciler` has no `sync` method (`AttributeError`).

- [ ] **Step 3: Implement `Reconciler.sync`**

In `dasik/lib/reconciler/reconciler.py`, append at the end of the `Reconciler` class (after `_build_new_manifest`):

```python
    def sync(self) -> "tuple[dict[str, Any], Optional[Manifest]]":
        """Capture system reality back into the config (spec §2 / §4 sync flow).

        Walks the v3 actions and, for each, asks ``import_state(managed)`` for
        the reconciled config fragment (∪ drift, \\ vanished-owned) and records
        ``managed ← actual()`` for the new manifest. Unlike ``build_plan``, an
        absent config slice is NOT skipped — bootstrap captures undeclared
        reality. Merges fragments into a new config via ``ConfigWriter.merge``
        and persists the new manifest via the injected ``StateStore``.

        sync records NO generation (only ``apply`` does) and performs NO system
        mutation — the config-file write is the caller's job.

        Returns ``(new_config, new_manifest)``; ``new_manifest`` is ``None``
        only when there are no v3 actions to sync.
        """
        from ..state.config_writer import ConfigWriter

        managed_all = (self._manifest or {}).get("managed", {})
        ctx = ActionContext(target=self._target, manifest=self._manifest)

        fragments: dict[str, Any] = {}
        new_managed: dict[str, Any] = {}
        saw_v3 = False

        for meta in self._metas:
            cls = meta["class"]
            if not cls.is_v3():
                continue
            saw_v3 = True

            config_key = meta["config_key"]
            if config_key == "__root__":
                action_config = self._config
            else:
                action_config = self._config.get(config_key)
            if action_config is None:
                # Bootstrap: capture reality even for an undeclared domain.
                action_config = self._empty_config_for(cls)

            action = cls(action_config, ctx)
            managed_for_action = self._managed_for(action, managed_all)

            fragment = action.import_state(managed_for_action)
            if isinstance(fragment, dict):
                fragments.update(fragment)

            domain = self._domain_for(action)
            if domain is not None:
                new_managed[domain] = sorted(action.actual())

        if not saw_v3:
            return self._config, None

        new_config = ConfigWriter.merge(self._config, fragments)

        prev_generation = 0
        if isinstance(self._manifest, dict):
            prev_generation = int(self._manifest.get("generation", 0))

        config_hash = hashlib.sha256(
            json.dumps(new_config, sort_keys=True).encode("utf-8")
        ).hexdigest()

        new_manifest = Manifest(
            generation=prev_generation,   # sync does NOT record a generation
            applied_at=datetime.now(timezone.utc).isoformat(),
            config_hash=config_hash,
            managed=new_managed,
        )

        if self._state_store is not None:
            self._state_store.save(new_manifest)

        return new_config, new_manifest
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/lib/reconciler/ -v`
Expected: PASS — Plan 3 + Plan 4 + new sync tests all green.

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=. pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dasik/lib/reconciler/reconciler.py tests/lib/reconciler/test_reconciler_sync.py
git commit -m "feat(reconciler): add sync() (reality -> config write-back)

sync() walks the v3 actions, asks each import_state(managed) for the
reconciled fragment, merges them into a new config via ConfigWriter, and
records managed <- actual into a new manifest persisted through the
injected StateStore. It records no generation and mutates no system
state; the config-file write is the caller's job. Absent config slices
are captured (bootstrap), not skipped.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: CLI `sync` verb

**Files:**
- Modify: `dasik/__main__.py`
- Create: `tests/test_cli_sync.py`

`sync <config> [--target /]`:

- Reuses the config-load + `setup_actions()` + `Reconciler(...)` plumbing (StateStore injected; no GenerationStore — sync records no generation).
- Loads the active manifest and passes it through (so `M \ A` / drift are computed against what dasik owns).
- Calls `reconciler.sync()` → `(new_config, manifest)`.
- If `manifest is None` (no v3 actions) → prints a notice, exits 0, writes nothing.
- If `new_config == config` (already reconciled) → prints "already matches", exits 0, writes nothing.
- Otherwise: writes a `<config>.bak` backup of the original, then `ConfigWriter.write(new_config, config_path)`; prints a summary; exits 0.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_sync.py`:

```python
import json
from unittest.mock import patch, MagicMock

from dasik import __main__ as cli


def _write_config(tmp_path, payload):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(payload))
    return p


def _patches():
    return (
        patch("dasik.__main__.Reconciler"),
        patch("dasik.__main__.setup_actions", lambda: None),
        patch("dasik.__main__.get_default_registry"),
        patch("dasik.__main__.StateStore"),
    )


def test_sync_writes_new_config_and_backup(tmp_path, capsys):
    cfg = _write_config(tmp_path, {"packages": ["git"]})
    p_recon, _, p_reg, p_store = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store:
        Reg.return_value.get_all_actions.return_value = []
        recon = Recon.return_value
        recon.sync.return_value = ({"packages": ["git", "htop"]}, MagicMock())
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}

        rc = cli.main(["sync", str(cfg)])

    assert rc == 0
    assert json.loads(cfg.read_text()) == {"packages": ["git", "htop"]}
    bak = tmp_path / "config.json.bak"
    assert bak.exists()
    assert json.loads(bak.read_text()) == {"packages": ["git"]}
    assert "Synced" in capsys.readouterr().out


def test_sync_no_change_does_not_write_or_backup(tmp_path, capsys):
    cfg = _write_config(tmp_path, {"packages": ["git"]})
    p_recon, _, p_reg, p_store = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store:
        Reg.return_value.get_all_actions.return_value = []
        recon = Recon.return_value
        recon.sync.return_value = ({"packages": ["git"]}, MagicMock())
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}

        rc = cli.main(["sync", str(cfg)])

    assert rc == 0
    assert not (tmp_path / "config.json.bak").exists()
    assert "already matches" in capsys.readouterr().out.lower()


def test_sync_no_v3_actions_writes_nothing(tmp_path, capsys):
    cfg = _write_config(tmp_path, {"packages": ["git"]})
    p_recon, _, p_reg, p_store = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store:
        Reg.return_value.get_all_actions.return_value = []
        recon = Recon.return_value
        recon.sync.return_value = ({"packages": ["git"]}, None)
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}

        rc = cli.main(["sync", str(cfg)])

    assert rc == 0
    assert not (tmp_path / "config.json.bak").exists()
    assert json.loads(cfg.read_text()) == {"packages": ["git"]}  # untouched


def test_sync_default_target_is_root(tmp_path):
    cfg = _write_config(tmp_path, {"packages": []})
    p_recon, _, p_reg, p_store = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store:
        Reg.return_value.get_all_actions.return_value = []
        Recon.return_value.sync.return_value = ({"packages": []}, None)
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}
        cli.main(["sync", str(cfg)])

    assert Recon.call_args.kwargs["target"].root == "/"


def test_sync_explicit_target_mnt(tmp_path):
    cfg = _write_config(tmp_path, {"packages": []})
    p_recon, _, p_reg, p_store = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store:
        Reg.return_value.get_all_actions.return_value = []
        Recon.return_value.sync.return_value = ({"packages": []}, None)
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}
        cli.main(["sync", str(cfg), "--target", "/mnt"])

    assert Recon.call_args.kwargs["target"].root == "/mnt"
    assert Store.call_args.args[0].root == "/mnt"


def test_sync_missing_config_exits_nonzero(tmp_path, capsys):
    missing = tmp_path / "nope.json"
    rc = cli.main(["sync", str(missing)])
    assert rc != 0
    assert "does not exist" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_cli_sync.py -v`
Expected: FAIL — no `sync` verb; argparse errors / `SystemExit`, and `ConfigWriter` not imported in `__main__`.

- [ ] **Step 3: Wire the `sync` verb**

In `dasik/__main__.py`:

(a) Add the `ConfigWriter` import to the import block (after the `StateStore` import):

```python
from dasik.lib.state.config_writer import ConfigWriter
from dasik.lib.state.generation_store import GenerationStore
from dasik.lib.state.state_store import StateStore
```

(b) Update `_KNOWN_VERBS`:

```python
_KNOWN_VERBS = {"plan", "apply", "sync", "generations", "rollback"}
```

(c) In `_build_parser`, before `return parser`, add the `sync` subparser:

```python
    sync_p = sub.add_parser(
        "sync",
        help="Capture system reality back into the config file (non-destructive)",
    )
    sync_p.add_argument("config", help="Path to the JSON configuration file")
    sync_p.add_argument(
        "--target",
        default="/",
        help="Root to read reality from (/ for the live host, /mnt for an "
             "install target). Default: /.",
    )
```

(d) Add `_cmd_sync` (next to `_cmd_apply`):

```python
def _cmd_sync(config_path: Path, target_root: str) -> int:
    """Capture system reality back into the config file (spec §4 sync flow)."""
    try:
        config = json.loads(config_path.read_text())
    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        return 1

    setup_actions()
    registry = get_default_registry()
    target = Target(root=target_root)
    state_store = StateStore(target)
    manifest_dict = state_store.load().to_dict()

    reconciler = Reconciler(
        config=config,
        target=target,
        manifest=manifest_dict,
        action_metas=registry.get_all_actions(),
        state_store=state_store,
    )
    new_config, new_manifest = reconciler.sync()

    if new_manifest is None:
        print("Nothing to sync (no convergence-aware actions registered).")
        return 0
    if new_config == config:
        print("Config already matches system reality - nothing to sync.")
        return 0

    backup = config_path.with_suffix(config_path.suffix + ".bak")
    backup.write_text(config_path.read_text())
    ConfigWriter.write(new_config, config_path)
    print(f"Synced system reality into {config_path} (backup: {backup}).")
    return 0
```

(e) In `main`, route the `sync` verb (after the `apply` block, before `parser.print_help`):

```python
        if args.verb == "sync":
            path = _validate_config_file(args.config)
            if path is None:
                return 1
            return _cmd_sync(path, args.target)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_cli_sync.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=. pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dasik/__main__.py tests/test_cli_sync.py
git commit -m "feat(cli): add 'sync' verb (reality -> config write-back)

sync loads the config + active manifest, calls Reconciler.sync(), and —
when the reconciled config differs — backs up the original to
<config>.bak and rewrites it via ConfigWriter. Non-destructive to the
system; defaults to --target / (day-2). No-op paths (no v3 actions /
already reconciled) write nothing.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: CLI `generations` verb

**Files:**
- Modify: `dasik/__main__.py`
- Create: `tests/test_cli_generations.py`

`generations [--target /]` lists recorded generations via `GenerationStore(target).list()`, marking the current one. No config argument.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_generations.py`:

```python
from unittest.mock import patch, MagicMock

from dasik import __main__ as cli
from dasik.lib.state.generation_store import GenInfo


def test_generations_lists_with_current_marker(capsys):
    with patch("dasik.__main__.GenerationStore") as Gen:
        Gen.return_value.list.return_value = [
            GenInfo(number=1, is_current=False),
            GenInfo(number=2, is_current=True),
        ]
        rc = cli.main(["generations"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "1" in out
    assert "2" in out
    assert "current" in out.lower()
    # The current marker is on generation 2, not 1.
    line2 = [ln for ln in out.splitlines() if "2" in ln][0]
    assert "current" in line2.lower()


def test_generations_empty_prints_message(capsys):
    with patch("dasik.__main__.GenerationStore") as Gen:
        Gen.return_value.list.return_value = []
        rc = cli.main(["generations"])

    assert rc == 0
    assert "no generations" in capsys.readouterr().out.lower()


def test_generations_default_target_is_root():
    with patch("dasik.__main__.GenerationStore") as Gen:
        Gen.return_value.list.return_value = []
        cli.main(["generations"])
    assert Gen.call_args.args[0].root == "/"


def test_generations_explicit_target():
    with patch("dasik.__main__.GenerationStore") as Gen:
        Gen.return_value.list.return_value = []
        cli.main(["generations", "--target", "/mnt"])
    assert Gen.call_args.args[0].root == "/mnt"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_cli_generations.py -v`
Expected: FAIL — no `generations` verb (argparse `SystemExit` / help printed, `rc == 2`).

- [ ] **Step 3: Wire the `generations` verb**

In `dasik/__main__.py`:

(a) In `_build_parser`, before `return parser`, add:

```python
    gens_p = sub.add_parser(
        "generations",
        help="List recorded generations",
    )
    gens_p.add_argument(
        "--target",
        default="/",
        help="Root whose generations to list. Default: /.",
    )
```

(b) Add `_cmd_generations`:

```python
def _cmd_generations(target_root: str) -> int:
    """List recorded generations, marking the current one."""
    gens = GenerationStore(Target(root=target_root)).list()
    if not gens:
        print("No generations recorded.")
        return 0
    for g in gens:
        marker = " (current)" if g.is_current else ""
        print(f"Generation {g.number}{marker}")
    return 0
```

(c) In `main`, route it (after the `sync` block):

```python
        if args.verb == "generations":
            return _cmd_generations(args.target)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_cli_generations.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=. pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dasik/__main__.py tests/test_cli_generations.py
git commit -m "feat(cli): add 'generations' verb (list recorded generations)

generations prints each recorded generation under <target>/var/lib/dasik,
marking the active one. Defaults to --target / (day-2).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: CLI `rollback` verb

**Files:**
- Modify: `dasik/__main__.py`
- Create: `tests/test_cli_rollback.py`

`rollback [N] [--target /] [--yes]`:

- Resolves `N`: if given, use it; if omitted, the generation immediately before the current one (error if none).
- `GenerationStore(target).restore(N)` → `(restored_config, _restored_manifest)` (also re-points the `current` symlink). `FileNotFoundError` → stderr + exit 1.
- Builds a `Reconciler` with the **restored config** as the desired state (D) and the **current** manifest (`StateStore.load()`) as owned (M) — so `apply` converges *from now* *to* the restored config. Injects both stores (so the rolled-back state is recorded as a new generation, NixOS-style).
- `build_plan()` → render → if empty, report and exit 0; else `reconciler.apply(plan, results, assume_yes=args.yes)`. On user-abort (`apply` returns `None`) → stderr "Aborted" + exit 1.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_rollback.py`:

```python
from unittest.mock import patch, MagicMock

import pytest

from dasik import __main__ as cli
from dasik.lib.state.change import Plan, Change, Op
from dasik.lib.state.generation_store import GenInfo


def _nonempty_plan_pair():
    p = Plan()
    p.add(Change("packages", Op.INSTALL, "git"))
    return p, []


def _empty_plan_pair():
    return Plan(), []


def _patches():
    return (
        patch("dasik.__main__.Reconciler"),
        patch("dasik.__main__.setup_actions", lambda: None),
        patch("dasik.__main__.get_default_registry"),
        patch("dasik.__main__.StateStore"),
        patch("dasik.__main__.GenerationStore"),
    )


def test_rollback_restores_given_generation_and_applies(capsys):
    p_recon, _, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store, p_gen as Gen:
        Reg.return_value.get_all_actions.return_value = []
        Gen.return_value.restore.return_value = ({"packages": ["git"]}, {"managed": {}})
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {"packages": []}}
        recon = Recon.return_value
        recon.build_plan.return_value = _nonempty_plan_pair()
        recon.apply.return_value = MagicMock(generation=5)

        rc = cli.main(["rollback", "2", "--yes"])

    assert rc == 0
    Gen.return_value.restore.assert_called_once_with(2)
    # Desired state for apply is the restored config.
    assert Recon.call_args.kwargs["config"] == {"packages": ["git"]}
    recon.apply.assert_called_once()
    assert recon.apply.call_args.kwargs.get("assume_yes") is True
    assert "generation 5" in capsys.readouterr().out


def test_rollback_default_n_uses_previous_generation(capsys):
    p_recon, _, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store, p_gen as Gen:
        Reg.return_value.get_all_actions.return_value = []
        Gen.return_value.list.return_value = [
            GenInfo(number=1, is_current=False),
            GenInfo(number=2, is_current=False),
            GenInfo(number=3, is_current=True),
        ]
        Gen.return_value.restore.return_value = ({"packages": []}, {"managed": {}})
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}
        recon = Recon.return_value
        recon.build_plan.return_value = _empty_plan_pair()

        rc = cli.main(["rollback", "--yes"])

    assert rc == 0
    Gen.return_value.restore.assert_called_once_with(2)  # current(3) - 1


def test_rollback_no_previous_generation_errors(capsys):
    p_recon, _, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store, p_gen as Gen:
        Reg.return_value.get_all_actions.return_value = []
        Gen.return_value.list.return_value = [GenInfo(number=1, is_current=True)]

        rc = cli.main(["rollback"])

    assert rc != 0
    assert "roll back" in capsys.readouterr().err.lower()


def test_rollback_missing_generation_errors(capsys):
    p_recon, _, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store, p_gen as Gen:
        Reg.return_value.get_all_actions.return_value = []
        Gen.return_value.restore.side_effect = FileNotFoundError("Generation 9 not found")

        rc = cli.main(["rollback", "9"])

    assert rc != 0
    assert "not found" in capsys.readouterr().err.lower()


def test_rollback_empty_plan_reports_and_exits_zero(capsys):
    p_recon, _, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store, p_gen as Gen:
        Reg.return_value.get_all_actions.return_value = []
        Gen.return_value.restore.return_value = ({"packages": []}, {"managed": {}})
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}
        recon = Recon.return_value
        recon.build_plan.return_value = _empty_plan_pair()

        rc = cli.main(["rollback", "1", "--yes"])

    assert rc == 0
    recon.apply.assert_not_called()


def test_rollback_user_abort_returns_nonzero(capsys):
    p_recon, _, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store, p_gen as Gen:
        Reg.return_value.get_all_actions.return_value = []
        Gen.return_value.restore.return_value = ({"packages": ["git"]}, {"managed": {}})
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}
        recon = Recon.return_value
        recon.build_plan.return_value = _nonempty_plan_pair()
        recon.apply.return_value = None  # user said no

        rc = cli.main(["rollback", "2"])

    assert rc != 0
    assert "aborted" in capsys.readouterr().err.lower()


def test_rollback_default_target_is_root():
    p_recon, _, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store, p_gen as Gen:
        Reg.return_value.get_all_actions.return_value = []
        Gen.return_value.restore.return_value = ({"packages": []}, {"managed": {}})
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}
        Recon.return_value.build_plan.return_value = _empty_plan_pair()
        cli.main(["rollback", "1"])

    assert Gen.call_args.args[0].root == "/"
    assert Recon.call_args.kwargs["target"].root == "/"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_cli_rollback.py -v`
Expected: FAIL — no `rollback` verb.

- [ ] **Step 3: Wire the `rollback` verb**

In `dasik/__main__.py`:

(a) In `_build_parser`, before `return parser`, add:

```python
    rollback_p = sub.add_parser(
        "rollback",
        help="Restore a generation's config and re-apply it (DESTRUCTIVE)",
    )
    rollback_p.add_argument(
        "generation",
        nargs="?",
        type=int,
        default=None,
        help="Generation number to roll back to. Default: the generation "
             "before the current one.",
    )
    rollback_p.add_argument(
        "--target",
        default="/",
        help="Root to converge. Default: /.",
    )
    rollback_p.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip the destructive-change confirmation prompt.",
    )
```

(b) Add `_previous_generation` + `_cmd_rollback`:

```python
def _previous_generation(gen_store: GenerationStore) -> Optional[int]:
    """The generation immediately before the current one, or None."""
    gens = gen_store.list()
    if not gens:
        return None
    current = next((g.number for g in gens if g.is_current), None)
    if current is None:
        return None
    earlier = [g.number for g in gens if g.number < current]
    return max(earlier) if earlier else None


def _cmd_rollback(target_root: str, number: Optional[int], assume_yes: bool) -> int:
    """Restore a generation's config and re-apply it (spec §4 rollback)."""
    target = Target(root=target_root)
    state_store = StateStore(target)
    gen_store = GenerationStore(target)

    if number is None:
        number = _previous_generation(gen_store)
        if number is None:
            print("Error: no earlier generation to roll back to.", file=sys.stderr)
            return 1

    try:
        restored_config, _restored_manifest = gen_store.restore(number)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    setup_actions()
    registry = get_default_registry()
    manifest_dict = state_store.load().to_dict()

    reconciler = Reconciler(
        config=restored_config,
        target=target,
        manifest=manifest_dict,
        action_metas=registry.get_all_actions(),
        state_store=state_store,
        generation_store=gen_store,
    )
    plan, results = reconciler.build_plan()
    print(plan.render())

    if plan.is_empty():
        print(f"System already matches generation {number}.")
        return 0

    new_manifest = reconciler.apply(plan, results, assume_yes=assume_yes)
    if new_manifest is None:
        print("Aborted: no changes applied.", file=sys.stderr)
        return 1

    print(
        f"Rolled back to generation {number} "
        f"(recorded as generation {new_manifest.generation})."
    )
    return 0
```

(c) In `main`, route it (after the `generations` block):

```python
        if args.verb == "rollback":
            return _cmd_rollback(args.target, args.generation, args.yes)
```

(d) Update the module docstring at the top of `dasik/__main__.py` — replace the line `` ``sync`` / ``generations`` / ``rollback`` land in a future plan. `` with a short description of the three new verbs:

```python
  * ``sync <config> [--target /]`` — capture system reality back into the
    config file (non-destructive to the system; rewrites the config).
  * ``generations [--target /]`` — list recorded generations.
  * ``rollback [N] [--target /] [--yes]`` — restore generation N's config and
    re-apply it (DESTRUCTIVE; defaults N to the generation before current).
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_cli_rollback.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Run the full suite + coverage gate**

Run: `PYTHONPATH=. pytest -q --cov=dasik --cov-report=term-missing`
Expected: PASS — Plan 1–5 all green. Coverage ≥ 80% (CLAUDE.md gate). If `__main__` shell-out / legacy paths drag coverage below 80%, add a written `omit` justification in `pyproject.toml` rather than lowering the gate (per CLAUDE.md "Tests and quality").

- [ ] **Step 6: Smoke (read-only — `generations` against the live host)**

`rollback` / `sync` mutate state, so the manual smoke uses the read-only `generations` verb (safe on any host — lists or prints "No generations recorded."):

```bash
PYTHONPATH=. python -m dasik generations --target /
```

Expected: prints "No generations recorded." (on a host dasik never applied to) or a generation list. Exit 0, no system change.

- [ ] **Step 7: Commit**

```bash
git add dasik/__main__.py tests/test_cli_rollback.py
git commit -m "feat(cli): add 'rollback' verb (restore generation + re-apply)

rollback restores generation N's config (default: the generation before
current) via GenerationStore, then drives the existing Reconciler.apply()
path with the current manifest as the owned set — converging the live
system to the restored config and recording it as a new generation.
Destructive changes still gate behind --yes / confirmation.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review (completed during planning)

**1. Spec coverage:**
- §2 sync set-math (∪ DRIFT, \\ vanished-owned, keep intent, M ← A, bootstrap): Task 2 (`import_state`) + Task 3 (`sync` walk + manifest). ✅
- §3.2 StateStore (load/save manifest): reused by Tasks 3/4/6 (no change needed). ✅
- §3.3 GenerationStore (list/restore): reused by Tasks 5/6 (no change needed — already built in Plan 1). ✅
- §3.5 `import_state` action contract: Task 2. ✅
- §3.6 `Reconciler.sync`: Task 3. ✅
- §3.7 ConfigWriter: Task 1. ✅
- §3.8 + §4 CLI verbs `sync` / `generations` / `rollback`: Tasks 4 / 5 / 6. ✅
- §4 sync flow (parse → load manifest → import_state per action → merge → write file → M ← A, no system mutation): Tasks 3 + 4. ✅
- §4 rollback flow (restore(N) → run apply against that config): Task 6. ✅
- §5 safety (rollback re-apply gates destructive changes behind --yes / confirmation; sync is non-destructive to the system + writes a `.bak`): Tasks 6 + 4. ✅
- §6 storage layout: unchanged — sync writes `state.json` only (no new generation dir); rollback reuses GenerationStore's existing layout. ✅
- **Deferred (spec §7 "Out"):** bootloader generation entries, disk convergence, pinning/lockfile, pure store, migrating other domains to v3, multi-domain actions — all declared out of scope in the plan header. The only spec component this plan does NOT exercise for non-package domains is the v3 migration of systemd/files/users, which §7 explicitly defers.

**2. Placeholder scan:** none. Every code/test step contains complete source. The "Known limitations" (JSON comment loss, AUR-prefix loss on captured drift) are documented spec-acknowledged constraints, not gaps.

**3. Type consistency:**
- `ConfigWriter.merge(existing: dict, fragments: dict) -> dict` and `ConfigWriter.write(config: dict, path: str | Path) -> None` — defined Task 1; called in Task 3 (`merge`) and Task 4 (`write`). ✅
- `AbstractAction.import_state(self, managed: Any = None) -> Dict[str, Any]` — Task 2 default; `PackagesAction.import_state(self, managed=None) -> dict` overrides it; `Reconciler.sync` calls `action.import_state(managed_for_action)` (Task 3). The `_V3Action` stub (test) and `_SyncStub` (test) both use `import_state(self, managed=None)`. ✅
- `Reconciler.sync(self) -> tuple[dict, Optional[Manifest]]` — Task 3; CLI `_cmd_sync` unpacks `(new_config, new_manifest)` (Task 4). ✅
- `Reconciler(config, target, manifest, action_metas, state_store=None, generation_store=None)` — unchanged from Plan 4; Task 4 passes `state_store` only (no gen store), Task 6 passes both. ✅
- `Reconciler.build_plan() -> (Plan, list[ActionPlanResult])` and `Reconciler.apply(plan, results, *, assume_yes=False, input_fn=input) -> Optional[Manifest]` — unchanged from Plan 4; reused by Task 6. ✅
- `GenerationStore(target).list() -> list[GenInfo]` (`GenInfo.number`, `GenInfo.is_current`) and `GenerationStore(target).restore(n) -> (config_dict, manifest_dict)` — unchanged from Plan 1; used in Tasks 5/6. ✅
- `StateStore(target).load() -> Manifest`, `Manifest.to_dict()` — unchanged; used in Tasks 4/6. ✅
- `Manifest(generation, applied_at, config_hash, managed)` (version defaults to `STATE_VERSION`) — Task 3 constructs it. ✅
- `Target(root).root` / `Target(root).is_chroot` — unchanged from Plan 1. ✅
- CLI `main(argv=None) -> int` — unchanged; new verbs route through the existing dispatch; `_KNOWN_VERBS` extended so the legacy no-verb detector still distinguishes a verb from a config path. ✅

**Decision notes:**
- **Set-math lives in `import_state`, not `ConfigWriter`.** Keeping `ConfigWriter` a dumb pure splice makes it trivially testable and keeps the `aur-` prefix / ordering knowledge inside the action that owns the domain. The Reconciler stays the orchestrator; the action owns domain quirks.
- **`import_state` gains an optional `managed` arg rather than a new method.** The spec names `import_state` as *the* sync method (§3.5). An optional arg (`managed=None`) keeps the existing zero-arg call sites (and the bootstrap semantics) working with no churn, and `None ≡ M=∅` is exactly the bootstrap case.
- **`sync` records no generation.** Spec §1/§4: only `apply` records a generation. `sync` only updates `state.json` (`M ← A`) and the config file. Generation number is therefore preserved; `config_hash` reflects the *new* config so the manifest stays self-consistent.
- **`sync` does NOT skip absent config slices** (unlike `build_plan`). Bootstrap (spec §2) is the headline use case: an empty/`packages`-less config on a populated system must capture reality, so the walk always runs each v3 action with an empty config default.
- **`sync` writes a `<config>.bak` and does not prompt.** It is non-destructive to the *system*; the only risk is rewriting the user's file (and losing comments). A backup is the proportionate safety measure; an interactive prompt would be friction on a safe operation.
- **`sync` / `generations` / `rollback` default to `--target /`.** They are day-2 operations on a running, already-installed host (generations only exist after applies; you sync *from* a live machine), unlike install-time `plan` / `apply` which default to `/mnt`. All accept `--target /mnt` explicitly.
- **`rollback` uses the *current* manifest as M, the *restored* config as D.** Convergence runs *from now* *to* the target generation, so removals (`M \ D`) are computed against what dasik currently owns — not against the restored generation's own manifest. The restored manifest is intentionally discarded (`_restored_manifest`).
- **`rollback` re-applies through the existing `apply` path** (records a new generation, gates destructive changes). This is the NixOS model: rolling back produces a new generation whose config equals the old one. The double current-symlink move (restore points at N, then apply's `new()` points at N+1) is benign for slice 1.
- **`rollback [N]` default = previous generation.** With no argument, roll back one step (the highest generation below current). Matches the common "undo the last apply" intent; explicit N covers arbitrary jumps.

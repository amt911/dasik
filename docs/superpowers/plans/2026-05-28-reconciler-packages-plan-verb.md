# Reconciler + Packages v3 + CLI `plan` verb — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the first end-to-end read-only convergence demo: `dasik plan <config> [--target / | /mnt]` prints the package install/remove/drift diff for the `packages` domain. Everything is safe (no system mutation); the destructive `apply` path lands in Plan 4.

**Architecture:** A new `Reconciler` orchestrates Plan 2's v3 action contract — it walks the registry, asks each `is_v3()` action for its `plan(managed)`, and aggregates the per-action results into a single renderable `Plan`. `PackagesAction` becomes the first v3 implementation (additive — its legacy `is_needed`/`execute` stay untouched so the existing executor path keeps working). The CLI grows a `plan` subcommand wired to the Reconciler; the no-verb form (`dasik <config>`) remains the legacy install entry point with a deprecation notice. Nothing destructive is reachable from `dasik plan`.

**Tech Stack:** Python ≥3.10 stdlib (`dataclasses`, `argparse`, `pathlib`, `subprocess`), pytest + `unittest.mock`. No new runtime deps.

**Spec:** [`docs/superpowers/specs/2026-05-27-declarative-convergence-and-sync-design.md`](../specs/2026-05-27-declarative-convergence-and-sync-design.md) — §3.6 Reconciler, §4 `plan` flow, §2 reconciliation model.

**Base branch:** `plan-2-action-contract-v3` (Plan 2 PR is open; Plan 3 chains on top). When Plan 2 merges, rebase onto `main`.

**Out of scope (Plan 4):**
- `Reconciler.apply()` and the `apply` CLI verb (destructive — needs safety gating + generation recording).
- `PackagesAction.apply()` (pacman -S / -Rns) and AUR write-path support.
- Migrating systemd / files / users domains.
- `sync` / `rollback` / `generations` verbs, `ConfigWriter`, protected sets.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `dasik/lib/reconciler/__init__.py` | package marker |
| `dasik/lib/reconciler/reconciler.py` | `ActionPlanResult` + `Reconciler.build_plan()` — pure orchestration over a registry of v3 actions |
| `dasik/lib/actions/packages_action.py` (modify) | add v3 methods `actual` / `plan` / `managed_keys` / `import_state`; leave legacy `is_needed`/`execute` unchanged |
| `dasik/__main__.py` (modify) | argparse subcommands: `plan` verb wired to Reconciler; no-verb form preserved with deprecation notice |
| `tests/lib/reconciler/test_reconciler.py` | Reconciler unit tests with fake v3 actions (no real Command/Target) |
| `tests/lib/actions/test_packages_action_v3.py` | v3 method tests with mocked `Command.execute` |
| `tests/test_cli_plan.py` | CLI smoke: invoke `main(["plan", ...])` with a mocked Reconciler, assert stdout + exit code |

---

## Task 1: `Reconciler` engine

**Files:**
- Create: `dasik/lib/reconciler/__init__.py`
- Create: `dasik/lib/reconciler/reconciler.py`
- Test: `tests/lib/reconciler/test_reconciler.py`

The Reconciler is pure orchestration: no Command, no subprocess, no Target lookups of its own. It receives a config dict, a target, a manifest dict, and an iterable of action metadata (the existing `ActionRegistry.get_all_actions()` shape: `{'class', 'config_key', 'is_optional', ...}`). For each v3 action it (a) instantiates with the right config slice + an `ActionContext(target, manifest)`, (b) calls `plan(managed)` where `managed` is extracted from `manifest['managed']` keyed by the action's first managed-keys domain, (c) collects `ActionPlanResult`. Legacy actions (`is_v3() is False`) are skipped with no error.

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/reconciler/test_reconciler.py`:

```python
from dataclasses import dataclass

from dasik.lib.actions.abstract_action import AbstractAction
from dasik.lib.reconciler.reconciler import ActionPlanResult, Reconciler
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target


class _LegacyOnly(AbstractAction):
    @property
    def name(self) -> str: return "legacy"
    def is_needed(self) -> bool: return True
    def execute(self) -> None: pass


class _PkgsV3(AbstractAction):
    """A minimal v3 action: declares packages; reports actual via class attr."""

    actual_set: set[str] = set()

    @property
    def name(self) -> str: return "pkgs"

    def is_needed(self) -> bool: return False

    def execute(self) -> None: pass

    def actual(self):
        return type(self).actual_set

    def plan(self, managed):
        from dasik.lib.state.set_math import compute_changes
        desired = self.config if isinstance(self.config, list) else []
        changes, drift = compute_changes(
            "packages", desired=desired, managed=managed, actual=self.actual()
        )
        type(self).last_drift = drift
        return changes

    def managed_keys(self):
        return {"packages": list(self.config) if isinstance(self.config, list) else []}


def _registry_entry(cls, config_key, is_optional=True):
    return {
        "class": cls,
        "config_key": config_key,
        "is_optional": is_optional,
        "required_fields": [],
        "depends_on": [],
    }


def test_build_plan_returns_empty_when_no_actions():
    r = Reconciler(
        config={},
        target=Target(root="/"),
        manifest={"managed": {}},
        action_metas=[],
    )
    plan, results = r.build_plan()
    assert plan.is_empty()
    assert results == []


def test_build_plan_skips_legacy_actions_silently():
    r = Reconciler(
        config={},
        target=Target(root="/"),
        manifest={"managed": {}},
        action_metas=[_registry_entry(_LegacyOnly, "anything")],
    )
    plan, results = r.build_plan()
    assert plan.is_empty()
    assert results == []


def test_build_plan_calls_v3_action_with_config_slice_and_managed():
    _PkgsV3.actual_set = {"git"}
    r = Reconciler(
        config={"packages": ["git", "htop"]},
        target=Target(root="/"),
        manifest={"managed": {"packages": ["git"]}},
        action_metas=[_registry_entry(_PkgsV3, "packages")],
    )
    plan, results = r.build_plan()
    items = [(c.op, c.item) for c in plan.changes]
    assert items == [(Op.INSTALL, "htop")]
    assert len(results) == 1
    res = results[0]
    assert isinstance(res, ActionPlanResult)
    assert res.changes == [Change("packages", Op.INSTALL, "htop")]
    assert res.action.config == ["git", "htop"]


def test_build_plan_uses_empty_managed_when_manifest_missing_domain():
    """First-apply: manifest has no entry for this domain → managed=[]."""
    _PkgsV3.actual_set = set()
    r = Reconciler(
        config={"packages": ["git"]},
        target=Target(root="/"),
        manifest={"managed": {}},   # no "packages" key
        action_metas=[_registry_entry(_PkgsV3, "packages")],
    )
    plan, _ = r.build_plan()
    assert [(c.op, c.item) for c in plan.changes] == [(Op.INSTALL, "git")]


def test_build_plan_uses_empty_managed_when_manifest_is_none():
    """No manifest at all (e.g., bootstrap before any apply) → managed=[]."""
    _PkgsV3.actual_set = set()
    r = Reconciler(
        config={"packages": ["git"]},
        target=Target(root="/"),
        manifest=None,
        action_metas=[_registry_entry(_PkgsV3, "packages")],
    )
    plan, _ = r.build_plan()
    assert [(c.op, c.item) for c in plan.changes] == [(Op.INSTALL, "git")]


def test_build_plan_skips_action_when_optional_section_missing():
    """Optional v3 action with no config slice and no managed entries → skip."""
    r = Reconciler(
        config={},  # no "packages"
        target=Target(root="/"),
        manifest={"managed": {}},
        action_metas=[_registry_entry(_PkgsV3, "packages", is_optional=True)],
    )
    plan, results = r.build_plan()
    assert plan.is_empty()
    assert results == []


def test_build_plan_runs_action_when_optional_section_missing_but_managed_has_entries():
    """Pure REMOVE case: config dropped 'packages' but manifest still owns some."""
    _PkgsV3.actual_set = {"vim"}
    r = Reconciler(
        config={},  # no "packages"
        target=Target(root="/"),
        manifest={"managed": {"packages": ["vim"]}},
        action_metas=[_registry_entry(_PkgsV3, "packages", is_optional=True)],
    )
    plan, results = r.build_plan()
    items = [(c.op, c.item) for c in plan.changes]
    assert items == [(Op.REMOVE, "vim")]
    assert results[0].action.config == []   # empty desired set


def test_action_context_passed_to_v3_action_carries_target_and_manifest():
    """Verify the ActionContext seen by the v3 action has target + manifest."""
    captured = {}

    class _Capture(_PkgsV3):
        def plan(self, managed):
            captured["target"] = self.context.target
            captured["manifest"] = self.context.manifest
            return []

    t = Target(root="/")
    manifest = {"managed": {"packages": []}}
    r = Reconciler(
        config={"packages": []},
        target=t,
        manifest=manifest,
        action_metas=[_registry_entry(_Capture, "packages")],
    )
    r.build_plan()
    assert captured["target"] is t
    assert captured["manifest"] is manifest
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/lib/reconciler/test_reconciler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dasik.lib.reconciler'`

- [ ] **Step 3: Create the package marker**

Create `dasik/lib/reconciler/__init__.py` (empty file).

- [ ] **Step 4: Implement `Reconciler`**

Create `dasik/lib/reconciler/reconciler.py`:

```python
"""Reconciler — orchestrates v3 actions to produce an aggregate Plan.

This is the pure orchestration layer (spec §3.6). It:
  * walks an action registry,
  * skips actions that are not yet v3 (``cls.is_v3() is False``),
  * extracts each action's config slice and its per-domain managed list
    from the manifest,
  * calls ``action.plan(managed)`` and collects the Changes,
  * returns an aggregate ``Plan`` (for rendering / destructive checks) plus
    a list of ``ActionPlanResult`` (per-action breakdown, needed by the
    apply path in Plan 4).

No I/O. No Command. The caller (CLI) is responsible for loading config,
resolving Target, and loading the manifest dict from StateStore.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from ..actions.abstract_action import AbstractAction
from ..actions.action_context import ActionContext
from ..state.change import Change, Plan
from ..target.target import Target


@dataclass
class ActionPlanResult:
    """Per-action planning result. Used by Plan 4's apply path."""

    action: AbstractAction
    changes: list[Change] = field(default_factory=list)


class Reconciler:
    """Builds an aggregate Plan by driving v3 actions over a registry.

    Args:
        config: the parsed config dict (root level).
        target: the Target commands will run against.
        manifest: the active manifest dict (``StateStore.load().to_dict()``)
            or ``None`` for first-apply / bootstrap.
        action_metas: iterable of registry entries — each a dict with keys
            ``class``, ``config_key``, ``is_optional``, ``required_fields``,
            ``depends_on``. Matches ``ActionRegistry.get_all_actions()``.
    """

    def __init__(
        self,
        config: dict[str, Any],
        target: Target,
        manifest: Optional[dict[str, Any]],
        action_metas: Iterable[dict[str, Any]],
    ):
        self._config = config
        self._target = target
        self._manifest = manifest
        self._metas = list(action_metas)

    def build_plan(self) -> tuple[Plan, list[ActionPlanResult]]:
        managed_all = (self._manifest or {}).get("managed", {})
        ctx = ActionContext(target=self._target, manifest=self._manifest)

        plan = Plan()
        results: list[ActionPlanResult] = []

        for meta in self._metas:
            cls = meta["class"]
            if not cls.is_v3():
                continue

            config_key = meta["config_key"]
            if config_key == "__root__":
                action_config = self._config
            else:
                action_config = self._config.get(config_key)

            # Optional action whose section is absent AND has no managed
            # entries to clean up → skip; nothing for it to plan.
            if action_config is None:
                domain_managed_any = self._any_managed_for(cls, managed_all)
                if not domain_managed_any:
                    continue
                # If there are owned items but no config slice, default to
                # an empty config so REMOVE = M\D fires.
                action_config = self._empty_config_for(cls)

            action = cls(action_config, ctx)
            managed_for_action = self._managed_for(action, managed_all)
            changes = list(action.plan(managed=managed_for_action))

            plan.extend(changes)
            results.append(ActionPlanResult(action=action, changes=changes))

        return plan, results

    # -- helpers -------------------------------------------------------

    @staticmethod
    def _domain_for(action: AbstractAction) -> Optional[str]:
        """Pick the first domain key from ``managed_keys()``; ``None`` if empty."""
        try:
            keys = action.managed_keys()
        except Exception:
            return None
        if not isinstance(keys, dict) or not keys:
            return None
        return next(iter(keys))

    @classmethod
    def _managed_for(
        cls, action: AbstractAction, managed_all: dict[str, Any]
    ) -> list[Any]:
        domain = cls._domain_for(action)
        if domain is None:
            return []
        return list(managed_all.get(domain, []))

    @classmethod
    def _any_managed_for(cls, action_cls, managed_all: dict[str, Any]) -> bool:
        """Probe (via a no-config instance) whether the class owns any manifest keys."""
        try:
            probe = action_cls.__new__(action_cls)
            probe.config = None
            probe.context = None
            keys = probe.managed_keys()
        except Exception:
            return False
        if not isinstance(keys, dict):
            return False
        return any(managed_all.get(k) for k in keys)

    @classmethod
    def _empty_config_for(cls, action_cls) -> Any:
        """When config slice is missing but managed has entries, hand the
        action an empty config of the right shape (list/dict) so its plan()
        can run. Defaults to ``[]`` — packages/users/systemd all accept a list.
        """
        return []
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/lib/reconciler/test_reconciler.py -v`
Expected: PASS (8 passed)

- [ ] **Step 6: Commit**

```bash
git add dasik/lib/reconciler/ tests/lib/reconciler/
git commit -m "feat: add Reconciler.build_plan() for v3 actions (spec §3.6)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: `PackagesAction` v3 (read-only methods)

**Files:**
- Modify: `dasik/lib/actions/packages_action.py`
- Test: `tests/lib/actions/test_packages_action_v3.py`

Add the read-only v3 methods (`actual`, `plan`, `managed_keys`, `import_state`). Leave `is_needed` / `execute` and every legacy AUR helper unchanged — the existing install path keeps working untouched. Do **not** implement `apply()` (defer to Plan 4 with AUR handling) — the inherited `AbstractAction.apply()` no-op is correct for now, and the read-only `plan` CLI verb never calls it.

Notes:
- `actual()` runs `pacman -Qqe` against `self.context.target` (the install/host root). The output is a newline-separated list of explicitly-installed package names.
- `plan()` ignores `aur-` prefixed entries in the config for now (the v3 set-math compares names that `pacman -Qqe` would emit; AUR install/uninstall lands in Plan 4 along with the apply path).
- The `context` arg may be `None` in legacy call-sites. Guard for that in `actual()` so the legacy `execute()` path is unaffected.

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/actions/test_packages_action_v3.py`:

```python
from unittest.mock import MagicMock, patch

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.packages_action import PackagesAction
from dasik.lib.state.change import Op
from dasik.lib.target.target import Target


def _ctx(root: str = "/") -> ActionContext:
    return ActionContext(target=Target(root=root))


def _fake_command_run(stdout: bytes = b"", returncode: int = 0):
    mock = MagicMock()
    mock.return_value = MagicMock(stdout=stdout, stderr=b"", returncode=returncode)
    return mock


def test_packages_action_is_v3_after_migration():
    assert PackagesAction.is_v3() is True


def test_actual_runs_pacman_Qqe_against_target_and_returns_set():
    fake = _fake_command_run(stdout=b"git\nhtop\nvim\n")
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=[], context=_ctx("/"))
        result = a.actual()
    assert result == {"git", "htop", "vim"}
    assert fake.called
    call_args = fake.call_args
    # Command.execute("pacman", ["-Qqe"], target=Target(root="/"))
    assert call_args.args[0] == "pacman"
    assert call_args.args[1] == ["-Qqe"]
    assert call_args.kwargs.get("target") is not None
    assert call_args.kwargs["target"].root == "/"


def test_actual_handles_empty_pacman_output():
    fake = _fake_command_run(stdout=b"")
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=[], context=_ctx("/"))
        assert a.actual() == set()


def test_actual_strips_blank_lines():
    fake = _fake_command_run(stdout=b"git\n\nhtop\n")
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=[], context=_ctx("/"))
        assert a.actual() == {"git", "htop"}


def test_actual_returns_empty_when_context_is_none():
    """Legacy call-sites instantiate without context — actual must not crash."""
    a = PackagesAction(config=[], context=None)
    assert a.actual() == set()


def test_plan_emits_install_for_missing_pacman_pkgs():
    fake = _fake_command_run(stdout=b"git\n")  # only git installed
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=["git", "htop"], context=_ctx("/"))
        changes = a.plan(managed=[])
    items = [(c.op, c.item) for c in changes]
    assert items == [(Op.INSTALL, "htop")]


def test_plan_emits_remove_for_managed_no_longer_declared():
    fake = _fake_command_run(stdout=b"vim\n")
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=[], context=_ctx("/"))
        changes = a.plan(managed=["vim"])
    assert len(changes) == 1
    assert changes[0].op == Op.REMOVE
    assert changes[0].item == "vim"
    assert changes[0].destructive is True


def test_plan_ignores_aur_prefixed_entries_in_config():
    """Plan-3 scope: AUR install/remove lands in Plan 4. aur- entries are skipped."""
    fake = _fake_command_run(stdout=b"")
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=["git", "aur-yay"], context=_ctx("/"))
        changes = a.plan(managed=[])
    items = [(c.op, c.item) for c in changes]
    assert items == [(Op.INSTALL, "git")]


def test_plan_empty_when_converged():
    fake = _fake_command_run(stdout=b"git\nhtop\n")
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=["git", "htop"], context=_ctx("/"))
        assert a.plan(managed=["git", "htop"]) == []


def test_managed_keys_returns_desired_pacman_set():
    a = PackagesAction(config=["git", "htop"], context=_ctx("/"))
    assert a.managed_keys() == {"packages": ["git", "htop"]}


def test_managed_keys_ignores_aur_prefix_entries():
    a = PackagesAction(config=["git", "aur-yay"], context=_ctx("/"))
    assert a.managed_keys() == {"packages": ["git"]}


def test_import_state_returns_actual_as_config_fragment():
    fake = _fake_command_run(stdout=b"git\nhtop\n")
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=[], context=_ctx("/"))
        frag = a.import_state()
    assert frag == {"packages": ["git", "htop"]}


def test_legacy_is_needed_still_works_without_context():
    """Legacy entry point: ActionExecutor passes context=ActionContext()
    with target=None. is_needed/execute must keep working (hardcoded /mnt).
    """
    a = PackagesAction(config=["git"], context=ActionContext())
    # The legacy is_needed calls _missing → _is_installed, which uses
    # arch-chroot /mnt directly. We just confirm calling it does not raise.
    with patch("dasik.lib.actions.packages_action.subprocess.run") as run:
        run.return_value = MagicMock(returncode=1)  # not installed
        assert a.is_needed() is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/lib/actions/test_packages_action_v3.py -v`
Expected: FAIL — `is_v3()` returns False (no `plan` override yet); `actual`/`plan`/`managed_keys`/`import_state` exist as no-op defaults from `AbstractAction` and will not match the test assertions.

- [ ] **Step 3: Add v3 methods to `PackagesAction`**

In `dasik/lib/actions/packages_action.py`, append the following block at the end of the class body (after `verify`, around line 193). Do not modify any existing method.

```python

    # ------------------------------------------------------------------ #
    #  v3 interface (read-only; apply() lands in Plan 4 with AUR support) #
    # ------------------------------------------------------------------ #

    _PACMAN_DOMAIN = "packages"

    def actual(self) -> set[str]:
        """Set of explicitly-installed packages on the target.

        Runs ``pacman -Qqe`` via ``Command.execute`` against
        ``self.context.target``. Returns an empty set if the context or
        target is missing (legacy call-sites).
        """
        target = getattr(self.context, "target", None) if self.context else None
        if target is None:
            return set()
        result = Command.execute("pacman", ["-Qqe"], target=target)
        stdout = getattr(result, "stdout", b"") or b""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        return {line.strip() for line in stdout.splitlines() if line.strip()}

    def plan(self, managed):
        """Compute INSTALL/REMOVE for pacman packages (AUR deferred to Plan 4)."""
        from ..state.set_math import compute_changes
        desired = list(self.pacman_pkgs)
        changes, _drift = compute_changes(
            self._PACMAN_DOMAIN,
            desired=desired,
            managed=managed,
            actual=self.actual(),
        )
        return changes

    def managed_keys(self) -> dict:
        """The pacman set this action would own after apply (AUR excluded)."""
        return {self._PACMAN_DOMAIN: list(self.pacman_pkgs)}

    def import_state(self) -> dict:
        """Config fragment derived from system reality (for sync, Plan 4)."""
        return {self._PACMAN_DOMAIN: sorted(self.actual())}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/lib/actions/test_packages_action_v3.py -v`
Expected: PASS (12 passed)

- [ ] **Step 5: Confirm nothing else broke**

Run: `PYTHONPATH=. pytest -v`
Expected: PASS — Plan 1 + Plan 2 + Reconciler + the new packages-v3 tests all green; the existing legacy `is_needed`/`execute` path is unchanged so any prior test of those methods (none exist today, but the full suite must stay green) keeps passing.

- [ ] **Step 6: Commit**

```bash
git add dasik/lib/actions/packages_action.py tests/lib/actions/test_packages_action_v3.py
git commit -m "feat(packages): add v3 read-only methods (actual/plan/managed_keys/import_state)

apply() deferred to Plan 4 (needs AUR write-path + safety gating).
Legacy is_needed/execute kept intact so the existing installer flow
keeps working unchanged.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: CLI `plan` verb

**Files:**
- Modify: `dasik/__main__.py`
- Test: `tests/test_cli_plan.py`

Replace the current single-positional-arg parser with argparse subcommands. Add `plan <config> [--target / | /mnt]`. Keep the no-verb form (`dasik <config>`) working as a deprecated alias for the legacy install path (`ActionsHandler(...)`) so today's behavior is preserved with a stderr warning. `plan` loads the config, builds a `Reconciler` against `setup_actions()`'s registry, calls `build_plan()`, prints the rendered diff, and exits 0.

Notes:
- The `--target` default is `/mnt` to match install-time semantics.
- `state.json` may not exist on first run — that's fine: `Reconciler` accepts `manifest=None`.
- Errors loading config / running plan should print to stderr and exit non-zero, not raise unhandled tracebacks.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_plan.py`:

```python
import json
import sys
from unittest.mock import patch, MagicMock

import pytest

from dasik import __main__ as cli


def _write_config(tmp_path, payload):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(payload))
    return p


def _empty_plan():
    from dasik.lib.state.change import Plan
    return Plan(), []


def test_plan_verb_invokes_reconciler_and_prints_no_changes(tmp_path, capsys):
    cfg = _write_config(tmp_path, {"packages": []})
    fake_reconciler = MagicMock()
    fake_reconciler.return_value.build_plan.return_value = _empty_plan()

    with patch("dasik.__main__.Reconciler", fake_reconciler), \
         patch("dasik.__main__.setup_actions", lambda: None), \
         patch("dasik.__main__.get_default_registry") as reg:
        reg.return_value.get_all_actions.return_value = []
        rc = cli.main(["plan", str(cfg)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "No changes" in out


def test_plan_verb_renders_changes(tmp_path, capsys):
    from dasik.lib.state.change import Plan, Change, Op
    p = Plan()
    p.add(Change("packages", Op.INSTALL, "git"))

    cfg = _write_config(tmp_path, {"packages": ["git"]})
    fake_reconciler = MagicMock()
    fake_reconciler.return_value.build_plan.return_value = (p, [])

    with patch("dasik.__main__.Reconciler", fake_reconciler), \
         patch("dasik.__main__.setup_actions", lambda: None), \
         patch("dasik.__main__.get_default_registry") as reg:
        reg.return_value.get_all_actions.return_value = []
        rc = cli.main(["plan", str(cfg)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "git" in out
    assert "+" in out  # INSTALL renders with "+"


def test_plan_verb_passes_target_flag_through(tmp_path):
    cfg = _write_config(tmp_path, {"packages": []})
    fake_reconciler = MagicMock()
    fake_reconciler.return_value.build_plan.return_value = _empty_plan()

    with patch("dasik.__main__.Reconciler", fake_reconciler), \
         patch("dasik.__main__.setup_actions", lambda: None), \
         patch("dasik.__main__.get_default_registry") as reg:
        reg.return_value.get_all_actions.return_value = []
        rc = cli.main(["plan", str(cfg), "--target", "/"])

    assert rc == 0
    target_passed = fake_reconciler.call_args.kwargs["target"]
    assert target_passed.root == "/"


def test_plan_verb_default_target_is_mnt(tmp_path):
    cfg = _write_config(tmp_path, {"packages": []})
    fake_reconciler = MagicMock()
    fake_reconciler.return_value.build_plan.return_value = _empty_plan()

    with patch("dasik.__main__.Reconciler", fake_reconciler), \
         patch("dasik.__main__.setup_actions", lambda: None), \
         patch("dasik.__main__.get_default_registry") as reg:
        reg.return_value.get_all_actions.return_value = []
        cli.main(["plan", str(cfg)])

    assert fake_reconciler.call_args.kwargs["target"].root == "/mnt"


def test_plan_verb_missing_config_exits_nonzero(tmp_path, capsys):
    missing = tmp_path / "nope.json"
    rc = cli.main(["plan", str(missing)])
    assert rc != 0
    assert "does not exist" in capsys.readouterr().err


def test_no_verb_form_still_works_with_deprecation_warning(tmp_path, capsys):
    cfg = _write_config(tmp_path, {"packages": []})
    with patch("dasik.__main__.ActionsHandler") as handler:
        rc = cli.main([str(cfg)])
    assert rc == 0
    err = capsys.readouterr().err
    assert "deprecated" in err.lower()
    handler.assert_called_once_with(str(cfg))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_cli_plan.py -v`
Expected: FAIL — `cli.main` does not accept an argv list, has no `plan` verb, and does not import `Reconciler`/`setup_actions`/`get_default_registry`.

- [ ] **Step 3: Rewrite `dasik/__main__.py`**

Replace the entire contents of `dasik/__main__.py` with:

```python
"""dasik CLI entry point.

Verbs (slice 1 of declarative-convergence):
  * ``plan <config> [--target / | /mnt]`` — show the diff between config and
    system reality. **Read-only; safe to run on any host.**
  * (no verb) ``dasik <config>`` — DEPRECATED. Falls back to the legacy
    install path (``ActionsHandler``). Will be removed once ``apply`` lands.

``apply`` / ``sync`` / ``generations`` / ``rollback`` land in Plan 4.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from dasik.lib.actions.actions_handler import ActionsHandler
from dasik.lib.actions.actions_handler_v2 import setup_actions
from dasik.lib.actions.action_registry import get_default_registry
from dasik.lib.reconciler.reconciler import Reconciler
from dasik.lib.target.target import Target


def _validate_config_file(config_path: str) -> Optional[Path]:
    """Return the Path if valid, else print error to stderr and return None."""
    path = Path(config_path)
    if not path.exists():
        print(f"Error: Configuration file '{config_path}' does not exist.",
              file=sys.stderr)
        return None
    if not path.is_file():
        print(f"Error: '{config_path}' is not a file.", file=sys.stderr)
        return None
    if path.suffix != ".json":
        print(f"Warning: '{config_path}' does not have .json extension.",
              file=sys.stderr)
    return path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dasik",
        description="Declarative Arch Linux installer / configuration manager",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable verbose output")

    # Subparsers; ``required=False`` so the legacy positional form
    # ``dasik <config>`` keeps working with a deprecation warning.
    sub = parser.add_subparsers(dest="verb")

    plan_p = sub.add_parser(
        "plan",
        help="Show what would change to converge the system to the config",
    )
    plan_p.add_argument("config", help="Path to the JSON configuration file")
    plan_p.add_argument(
        "--target",
        default="/mnt",
        help="Root commands run against (/ for the live host, /mnt for an "
             "install target). Default: /mnt.",
    )

    # Legacy positional fallback (no verb).
    parser.add_argument(
        "legacy_config",
        nargs="?",
        help=argparse.SUPPRESS,  # not advertised; kept for back-compat
    )
    return parser


def _cmd_plan(config_path: Path, target_root: str) -> int:
    """Run the read-only plan flow."""
    try:
        config = json.loads(config_path.read_text())
    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        return 1

    setup_actions()
    registry = get_default_registry()

    reconciler = Reconciler(
        config=config,
        target=Target(root=target_root),
        manifest=None,  # Plan 4 wires StateStore here.
        action_metas=registry.get_all_actions(),
    )
    plan, _results = reconciler.build_plan()
    print(plan.render())
    return 0


def _cmd_legacy(config_path_str: str) -> int:
    """Deprecated no-verb form. Delegates to the legacy install handler."""
    print(
        "Warning: invoking `dasik <config>` without a verb is deprecated. "
        "Use `dasik plan <config>` or (Plan 4) `dasik apply <config>`.",
        file=sys.stderr,
    )
    ActionsHandler(config_path_str)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.verb == "plan":
            path = _validate_config_file(args.config)
            if path is None:
                return 1
            return _cmd_plan(path, args.target)

        if args.legacy_config is not None:
            path = _validate_config_file(args.legacy_config)
            if path is None:
                return 1
            return _cmd_legacy(str(path))

        parser.print_help(file=sys.stderr)
        return 2

    except KeyboardInterrupt:
        print("\nOperation cancelled by user.", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_cli_plan.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=. pytest -v`
Expected: PASS — Plan 1 + Plan 2 + Reconciler + packages-v3 + CLI tests all green.

- [ ] **Step 6: Manual smoke (no system mutation)**

Run a real `plan` invocation against the live host with a trivial config:

```bash
cat > /tmp/dasik-smoke.json <<'EOF'
{"packages": ["bash"]}
EOF
PYTHONPATH=. python -m dasik plan /tmp/dasik-smoke.json --target /
```

Expected: prints either `No changes - system matches config.` (if `bash` is installed and not owned by dasik — actually it will be DRIFT since `managed=[]` and `bash` is in `pacman -Qqe`) **or** a `+ [packages] install bash` line (if `bash` isn't an explicit install on your host). Either way: exit 0, no errors, **no system change**.

If pacman / arch-chroot is unavailable in the runner, this manual smoke is informational only — the unit tests are authoritative.

- [ ] **Step 7: Commit**

```bash
git add dasik/__main__.py tests/test_cli_plan.py
git commit -m "feat(cli): add 'plan' verb (read-only convergence diff)

Subcommand parser introduced; legacy 'dasik <config>' form preserved
with a stderr deprecation notice. 'plan' wires JsonParser →
setup_actions() → Reconciler → Plan.render(). apply/sync/generations/
rollback land in Plan 4.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Self-Review (completed during planning)

**1. Spec coverage (Plan 3 portion):**
- §3.6 Reconciler `build_plan()` → Task 1. ✅ (`apply()` deferred to Plan 4 as plan header notes.)
- §3.5 v3 action contract on a concrete domain → Task 2 (packages, read-only). ✅
- §4 `plan` CLI verb → Task 3. ✅
- §2 set-math used end-to-end → Task 2's `plan()` calls `compute_changes`. ✅
- §3.7 ConfigWriter, §3.8 full CLI surface (apply/sync/generations/rollback), §5 safety, §6 storage wiring — **deferred to Plan 4** (declared in plan header).

**2. Placeholder scan:** none. Every code/test step contains full source. No "implement later" except for the Plan-4-deferred items, which are explicit scope statements, not gaps.

**3. Type consistency:**
- `Reconciler(config, target, manifest, action_metas)` signature matches all Task 1 tests and the Task 3 CLI call-site (`reconciler.call_args.kwargs["target"]` etc.).
- `Reconciler.build_plan() -> tuple[Plan, list[ActionPlanResult]]` matches Task 3's `_empty_plan()` helper that returns `(Plan(), [])`.
- `ActionPlanResult(action, changes)` dataclass matches Task 1's `res.action.config == ["git", "htop"]` and `res.changes == [...]` assertions.
- `PackagesAction.actual() -> set[str]`, `plan(managed) -> list[Change]`, `managed_keys() -> dict`, `import_state() -> dict` consistent across Task 2 tests and the Reconciler's call pattern from Task 1.
- `Command.execute(cmd, args, *, target=...)` matches Plan 1's signature; Task 2 tests assert `call_args.kwargs["target"].root`.
- `Target(root="/")` / `Target(root="/mnt")` used identically in all tasks.
- Action registry shape (`{"class", "config_key", "is_optional", "required_fields", "depends_on"}`) matches `dasik/lib/actions/action_registry.py:32-39`.
- `cli.main(argv=None)` returning `int` matches Task 3 tests calling `cli.main(["plan", str(cfg)])`.
- `Plan.add(Change)` / `Plan.extend(list[Change])` / `Plan.is_empty()` / `Plan.render()` consistent with Plan 1's `dasik/lib/state/change.py`.

**Decision notes:**
- **Why no `apply()` in Plan 3:** the destructive path needs (a) AUR write-path, (b) safety gating + confirmation, (c) generation recording. Stuffing all of that into Plan 3 makes the plan too big and conflates "show diff" (low risk, demoable today) with "mutate system" (high risk, demands more guard rails). Splitting buys a clean ship point and keeps Plan 4 focused on the destructive surface.
- **Why `Reconciler._domain_for(action)` picks the first key from `managed_keys()`:** in slice 1 each action owns exactly one domain. When systemd + files + users land in Plan 4, multi-domain actions either return their primary domain first or override the helper. Documented in `Reconciler._domain_for` doc.
- **Why `aur-` entries are dropped in `plan()`:** AUR install/uninstall requires the makepkg + temp-user dance that the legacy `execute()` already implements. Mirroring that in `apply()` belongs with the rest of the destructive work in Plan 4; pretending v3 already handles AUR would emit INSTALL changes that `apply()` couldn't honor. Test `test_plan_ignores_aur_prefixed_entries_in_config` locks this in.
- **Why the no-verb form stays:** preserves backward compatibility for any user running `dasik config.json` today and keeps the legacy install path reachable until Plan 4's `apply` replaces it. Stderr deprecation tells the user where to migrate.

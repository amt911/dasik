# Action Contract v3 + Set-Math Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the engine layer that domain actions will plug into — the v3 `AbstractAction` interface (`actual`/`plan`/`apply`/`import_state`/`managed_keys`), a `Target`/`Manifest`-aware `ActionContext`, and the pure D/M/A → `Change` set-math at the heart of `apply` and `sync`.

**Architecture:** Additive, non-breaking extensions on top of Plan 1's primitives. The v3 methods land as concrete defaults on `AbstractAction` so the ~20 existing legacy actions keep working untouched; an `is_v3()` class-method discriminator lets the future `Reconciler` (Plan 3) pick the right code path per action. Set-math is a pure module under `dasik/lib/state/` — no I/O, no `Command`, exhaustive unit tests per spec §8 ("highest value").

**Tech Stack:** Python ≥3.10 stdlib (`dataclasses`, `typing`), pytest. No new runtime deps.

**Spec:** [`docs/superpowers/specs/2026-05-27-declarative-convergence-and-sync-design.md`](../specs/2026-05-27-declarative-convergence-and-sync-design.md) — §2 (reconciliation model), §3.5 (Action contract v3), §3 (ActionContext injection note), §8 (testing strategy).

**Out of scope (deferred):**
- `Reconciler` orchestration and the actual driving of `plan`/`apply` across actions — Plan 3.
- Migrating concrete actions (`packages_action`, `systemd_action`, files, users) to v3 — Plan 3.
- `ConfigWriter`, CLI verbs, safety gating — Plan 4.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `dasik/lib/state/set_math.py` | Pure `compute_changes(domain, desired, managed, actual) -> (changes, drift)` per spec §2 |
| `dasik/lib/actions/action_context.py` (modify) | Add `target: Target \| None` and `manifest: dict \| None` fields; keep existing API |
| `dasik/lib/actions/abstract_action.py` (modify) | Add v3 methods (`actual`/`plan`/`apply`/`import_state`/`managed_keys`) as concrete defaults; add `is_v3()` classmethod discriminator |
| `tests/lib/state/test_set_math.py` | Exhaustive set-math tests (first-apply, bootstrap, pure-install, pure-remove, drift, mixed) |
| `tests/lib/actions/test_action_context.py` | ActionContext new fields + legacy API preserved |
| `tests/lib/actions/test_abstract_action.py` | v3 defaults + `is_v3()` discrimination |

---

## Task 1: Set-math (`compute_changes`)

**Files:**
- Create: `dasik/lib/state/set_math.py`
- Test: `tests/lib/state/test_set_math.py`

This is the pure heart of the reconciliation model (spec §2). Implement it before anything else so later tasks can reference it without ambiguity.

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/state/test_set_math.py`:

```python
from dasik.lib.state.change import Op
from dasik.lib.state.set_math import compute_changes


def test_first_apply_only_installs_no_remove():
    """M=∅ ⇒ REMOVE=∅; pre-existing undeclared items are DRIFT, untouched."""
    changes, drift = compute_changes(
        "packages", desired=["git", "htop"], managed=[], actual=["vim"]
    )
    ops = [(c.op, c.item) for c in changes]
    assert ops == [(Op.INSTALL, "git"), (Op.INSTALL, "htop")]
    assert drift == ["vim"]


def test_bootstrap_everything_is_drift():
    """D=∅, M=∅: nothing to install, nothing to remove, all A → drift."""
    changes, drift = compute_changes(
        "packages", desired=[], managed=[], actual=["git", "vim"]
    )
    assert changes == []
    assert drift == ["git", "vim"]


def test_pure_install():
    changes, drift = compute_changes(
        "packages", desired=["git"], managed=[], actual=[]
    )
    assert [(c.op, c.item) for c in changes] == [(Op.INSTALL, "git")]
    assert drift == []


def test_pure_remove_owned_no_longer_declared():
    changes, drift = compute_changes(
        "packages", desired=[], managed=["vim"], actual=["vim"]
    )
    assert len(changes) == 1
    c = changes[0]
    assert c.op == Op.REMOVE
    assert c.item == "vim"
    assert c.destructive is True
    assert c.reason == "no longer declared"
    assert drift == []


def test_already_converged_no_changes_no_drift():
    changes, drift = compute_changes(
        "packages", desired=["git"], managed=["git"], actual=["git"]
    )
    assert changes == []
    assert drift == []


def test_mixed_install_and_drift():
    """Declared + missing installs; manually-installed surfaces as drift."""
    changes, drift = compute_changes(
        "packages",
        desired=["git", "htop"],
        managed=["git"],
        actual=["git", "vim"],
    )
    ops = [(c.op, c.item) for c in changes]
    assert ops == [(Op.INSTALL, "htop")]
    assert drift == ["vim"]


def test_remove_only_targets_owned_items():
    """A \\ D \\ M = drift, NOT removal — primary safety property of the model."""
    changes, drift = compute_changes(
        "packages", desired=[], managed=[], actual=["user-installed"]
    )
    assert changes == []
    assert drift == ["user-installed"]


def test_custom_ops_for_domain():
    """Domains like systemd want ENABLE/DISABLE instead of INSTALL/REMOVE."""
    changes, drift = compute_changes(
        "systemd",
        desired=["NetworkManager.service"],
        managed=["sshd.service"],
        actual=["sshd.service"],
        op_install=Op.ENABLE,
        op_remove=Op.DISABLE,
    )
    ops = [(c.op, c.item) for c in changes]
    assert ops == [
        (Op.ENABLE, "NetworkManager.service"),
        (Op.DISABLE, "sshd.service"),
    ]
    assert drift == []
    assert changes[1].destructive is True  # DISABLE is destructive


def test_output_is_deterministic_sorted():
    """Changes and drift are sorted so renders/diffs are stable across runs."""
    changes, drift = compute_changes(
        "packages",
        desired=["zsh", "git", "htop"],
        managed=[],
        actual=["nano", "vim"],
    )
    install_items = [c.item for c in changes]
    assert install_items == sorted(install_items)
    assert drift == sorted(drift)


def test_accepts_any_iterable_not_just_lists():
    """Sets, tuples, generators all work — they're hashed internally."""
    changes, drift = compute_changes(
        "packages",
        desired={"git"},
        managed=("git",),
        actual=iter(["git", "vim"]),
    )
    assert changes == []
    assert drift == ["vim"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/lib/state/test_set_math.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dasik.lib.state.set_math'`

- [ ] **Step 3: Implement `set_math.py`**

Create `dasik/lib/state/set_math.py`:

```python
"""Pure D/M/A → Change set-math (spec §2).

No I/O, no Command, no Target — this is the heart of the reconciliation model
and the highest-value unit to test exhaustively (spec §8).
"""
from typing import Iterable

from .change import Change, Op


def compute_changes(
    domain: str,
    *,
    desired: Iterable[str],
    managed: Iterable[str],
    actual: Iterable[str],
    op_install: Op = Op.INSTALL,
    op_remove: Op = Op.REMOVE,
) -> tuple[list[Change], list[str]]:
    """Compute the (changes, drift) tuple for one domain.

    Set semantics (spec §2):
        INSTALL = D \\ A          declared, absent          → add / enable / create
        REMOVE  = M \\ D          owned, no longer declared → DESTRUCTIVE
        DRIFT   = A \\ D \\ M     present, neither declared nor owned → REPORTED, UNTOUCHED

    The primary safety property of this model: removal is scoped to M (what
    dasik itself applied). Manually-installed items appear as drift and become
    candidates for `sync`, never for automatic removal.

    Args:
        domain: domain label embedded into each Change (e.g. "packages").
        desired: D — the set the config declares.
        managed: M — the set the manifest records as owned by dasik.
        actual:  A — the set actually present on the system.
        op_install: Change op for D \\ A. Defaults to INSTALL; pass ENABLE for
            systemd, CREATE for files, etc.
        op_remove: Change op for M \\ D. Defaults to REMOVE; pass DISABLE for
            systemd, DELETE for files, etc.

    Returns:
        (changes, drift) — changes sorted by op then item for deterministic
        output; drift sorted alphabetically. Changes carry ``reason="no longer
        declared"`` for removals so plan rendering explains the destructive op.
    """
    D, M, A = set(desired), set(managed), set(actual)

    changes: list[Change] = []
    for item in sorted(D - A):
        changes.append(Change(domain, op_install, item))
    for item in sorted(M - D):
        changes.append(Change(domain, op_remove, item, reason="no longer declared"))

    drift = sorted(A - D - M)
    return changes, drift
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/lib/state/test_set_math.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/state/set_math.py tests/lib/state/test_set_math.py
git commit -m "feat: add pure D/M/A set-math (spec §2)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: `ActionContext` carries `Target` and `Manifest`

**Files:**
- Modify: `dasik/lib/actions/action_context.py`
- Test: `tests/lib/actions/test_action_context.py`

Spec §3.1 notes that `Target` is "injected into `ActionContext` and read by `Command`". v3 actions need to know which root they operate against; they also need a read of the current manifest so `plan()` knows what is currently managed. Add both as optional attributes — existing legacy actions ignore them, so nothing breaks.

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/actions/test_action_context.py`:

```python
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target


def test_default_target_is_none():
    """Legacy actions construct ActionContext() with no args — must still work."""
    ctx = ActionContext()
    assert ctx.target is None
    assert ctx.manifest is None


def test_can_set_target_at_construction():
    t = Target(root="/mnt")
    ctx = ActionContext(target=t)
    assert ctx.target is t


def test_can_set_manifest_at_construction():
    m = {"generation": 1, "managed": {"packages": ["git"]}}
    ctx = ActionContext(manifest=m)
    assert ctx.manifest == {"generation": 1, "managed": {"packages": ["git"]}}


def test_legacy_partition_api_preserved():
    """Existing call-sites use partition_map / set_partition / get_partition."""
    ctx = ActionContext()
    ctx.set_partition("root", "/dev/sda2")
    assert ctx.get_partition("root") == "/dev/sda2"
    assert ctx.get_all_partitions() == {"root": "/dev/sda2"}
    assert ctx.get_partition("missing") is None


def test_legacy_get_set_has_preserved():
    ctx = ActionContext()
    assert ctx.has("k") is False
    ctx.set("k", 42)
    assert ctx.has("k") is True
    assert ctx.get("k") == 42
    assert ctx.get("absent", "default") == "default"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/lib/actions/test_action_context.py -v`
Expected: FAIL — first three tests raise `TypeError: __init__() got an unexpected keyword argument 'target'` (or `'manifest'`) or `AttributeError: 'ActionContext' object has no attribute 'target'`.

- [ ] **Step 3: Add `target` and `manifest` to `ActionContext.__init__`**

In `dasik/lib/actions/action_context.py`, replace the existing imports and `__init__` (lines 1–14) with:

```python
from typing import Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..target.target import Target


class ActionContext:
    """Shared context between actions during installation.
    
    This allows actions to share state and communicate with each other.
    For example, disk partitioning action can store partition mappings
    that will be used by the base installation action.

    v3 additions (spec §3.1, §3.5):
    - ``target``: the root commands run against (``/`` for day-2, ``/mnt`` for
      install). Read by v3 actions and forwarded to ``Command.execute(target=…)``.
    - ``manifest``: the active state manifest (the M set per domain). Read by
      v3 actions inside ``plan()`` so they can compute REMOVE = M \\ D.

    Both default to ``None`` so legacy actions and existing call-sites that do
    ``ActionContext()`` keep working unchanged.
    """

    def __init__(
        self,
        target: Optional["Target"] = None,
        manifest: Optional[Dict[str, Any]] = None,
    ):
        """Initialize empty context."""
        self._data: Dict[str, Any] = {}
        self.partition_map: Dict[str, str] = {}
        self.target = target
        self.manifest = manifest
```

Leave every method below `__init__` (lines 16–74) **unchanged**.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/lib/actions/test_action_context.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Confirm nothing else broke**

Run: `pytest -v`
Expected: PASS — all Plan 1 tests still green; no regressions in the existing suite.

- [ ] **Step 6: Commit**

```bash
git add dasik/lib/actions/action_context.py tests/lib/actions/test_action_context.py
git commit -m "feat: ActionContext carries Target and manifest for v3 actions

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: `AbstractAction` grows the v3 interface

**Files:**
- Modify: `dasik/lib/actions/abstract_action.py`
- Test: `tests/lib/actions/test_abstract_action.py`

Add the five v3 methods (`actual`, `plan`, `apply`, `import_state`, `managed_keys`) as concrete defaults so the ~20 existing legacy actions keep working unchanged. v3 actions opt in by overriding `plan` (the discriminator). An `is_v3()` classmethod tells the future `Reconciler` which path to drive.

> The legacy `is_needed()` / `execute()` stay abstract — existing actions already
> implement them and the spec's "thin shims" go in once `Reconciler` exists (Plan 3).
> Plan 2 only lays down the contract surface.

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/actions/test_abstract_action.py`:

```python
import pytest

from dasik.lib.actions.abstract_action import AbstractAction
from dasik.lib.state.change import Change, Op


class _LegacyAction(AbstractAction):
    """A pre-v3 action: only implements is_needed/execute."""

    @property
    def name(self) -> str:
        return "legacy"

    def is_needed(self) -> bool:
        return False

    def execute(self) -> None:
        pass


class _V3Action(AbstractAction):
    """A v3 action: overrides plan/apply/actual/import_state/managed_keys."""

    @property
    def name(self) -> str:
        return "v3"

    def is_needed(self) -> bool:
        return bool(self.plan(managed=set()))

    def execute(self) -> None:
        self.apply(self.plan(managed=set()))

    def actual(self):
        return {"git"}

    def plan(self, managed):
        return [Change("packages", Op.INSTALL, "git")]

    def apply(self, plan):
        self._applied = list(plan)

    def import_state(self):
        return {"packages": ["git"]}

    def managed_keys(self):
        return {"packages": ["git"]}


def test_legacy_action_default_plan_is_empty():
    a = _LegacyAction(config={})
    assert a.plan(managed=set()) == []


def test_legacy_action_default_apply_is_noop():
    a = _LegacyAction(config={})
    a.apply([])  # must not raise


def test_legacy_action_default_actual_is_empty_set():
    a = _LegacyAction(config={})
    assert a.actual() == set()


def test_legacy_action_default_import_state_is_empty_dict():
    a = _LegacyAction(config={})
    assert a.import_state() == {}


def test_legacy_action_default_managed_keys_is_empty_dict():
    a = _LegacyAction(config={})
    assert a.managed_keys() == {}


def test_legacy_action_is_v3_false():
    """Legacy actions don't override plan → is_v3 is False."""
    assert _LegacyAction.is_v3() is False


def test_v3_action_is_v3_true():
    """Overriding plan flips the discriminator."""
    assert _V3Action.is_v3() is True


def test_v3_action_plan_apply_round_trip():
    a = _V3Action(config={})
    plan = a.plan(managed=set())
    assert plan == [Change("packages", Op.INSTALL, "git")]
    a.apply(plan)
    assert a._applied == plan


def test_abstract_action_cannot_be_instantiated_directly():
    """name/is_needed/execute remain abstract — sanity check."""
    with pytest.raises(TypeError):
        AbstractAction(config={})  # type: ignore[abstract]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/lib/actions/test_abstract_action.py -v`
Expected: FAIL — `_LegacyAction` and `_V3Action` raise `AttributeError`/`NotImplementedError` because `plan`/`apply`/`actual`/`import_state`/`managed_keys`/`is_v3` don't exist on `AbstractAction` yet.

- [ ] **Step 3: Add the v3 methods + `is_v3()` to `AbstractAction`**

In `dasik/lib/actions/abstract_action.py`, update the imports (line 2) to:

```python
from typing import Any, Dict, List, TYPE_CHECKING
```

Then add an import below it (after the existing `if TYPE_CHECKING:` block, around line 5):

```python
if TYPE_CHECKING:
    from .action_context import ActionContext
    from ..state.change import Change
```

Append the following block to the end of the class body (after the `KEY_NAME` property, line 96):

```python

    # ------------------------------------------------------------------
    # v3 interface (spec §3.5) — concrete defaults so legacy actions that
    # only override is_needed/execute keep working unchanged. v3 actions
    # opt in by overriding ``plan``; ``is_v3()`` discriminates the two so
    # the future Reconciler (Plan 3) can pick the right code path.
    # ------------------------------------------------------------------

    def actual(self) -> Any:
        """Read system reality (A) for this action's domain.

        v3 actions override this to query the system (e.g. ``pacman -Qqe``)
        via ``Command.execute(target=self.context.target)``. The default
        returns an empty set so legacy actions can still be introspected
        without error.
        """
        return set()

    def plan(self, managed: Any) -> "List[Change]":
        """Compute the list of Changes needed to converge to the config.

        v3 actions override this with set-math over (D=config, M=managed,
        A=self.actual()) — typically via
        ``dasik.lib.state.set_math.compute_changes``. The default returns
        an empty list, which makes ``is_v3()`` return False.
        """
        return []

    def apply(self, plan: "List[Change]") -> None:
        """Execute the Changes produced by ``plan``.

        v3 actions override this; the default is a no-op so legacy actions
        keep using their own ``execute()`` path.
        """
        return None

    def import_state(self) -> Dict[str, Any]:
        """Return the config fragment that mirrors A (for ``sync``).

        v3 actions override this to capture drift back into the config
        (e.g. ``{"packages": [...explicitly installed packages...]}``).
        The default returns an empty dict.
        """
        return {}

    def managed_keys(self) -> Dict[str, Any]:
        """Return what this action contributes to the manifest after apply.

        v3 actions override this; the default returns an empty dict.
        """
        return {}

    @classmethod
    def is_v3(cls) -> bool:
        """True if this subclass overrides ``plan`` — i.e. uses the v3 API.

        Used by the Reconciler (Plan 3) to decide between the v3
        ``plan``/``apply`` path and the legacy ``is_needed``/``execute`` path.
        """
        return cls.plan is not AbstractAction.plan
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/lib/actions/test_abstract_action.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `pytest -v`
Expected: PASS — every Plan 1 and Plan 2 test green; no existing action breaks because the new methods are additive concrete defaults.

- [ ] **Step 6: Coverage check on the new modules**

Run: `pytest --cov=dasik.lib.state.set_math --cov=dasik.lib.actions.action_context --cov=dasik.lib.actions.abstract_action --cov-report=term-missing -v`
Expected: PASS; each new/touched module reports ≥80% line+branch coverage. `set_math` should hit 100%.

- [ ] **Step 7: Commit**

```bash
git add dasik/lib/actions/abstract_action.py tests/lib/actions/test_abstract_action.py
git commit -m "feat: AbstractAction grows v3 interface (plan/apply/actual/...)

Concrete defaults keep all ~20 legacy actions working unchanged.
is_v3() classmethod discriminates v3-migrated actions for the
future Reconciler (Plan 3).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Self-Review (completed during planning)

**1. Spec coverage (Plan 2 portion):**
- §2 reconciliation model (set-math `INSTALL = D\A`, `REMOVE = M\D`, `DRIFT = A\D\M`) → Task 1. ✅
- §3.5 Action contract v3 (`actual`/`plan`/`apply`/`import_state`/`managed_keys`) → Task 3. ✅
- §3.1 `Target` injected into `ActionContext` → Task 2. ✅
- §3 mention of `Manifest` available to actions for `plan()` → Task 2 (ActionContext.manifest). ✅
- §3.6 Reconciler orchestration → **deferred to Plan 3** (called out in plan header).
- §3.7 ConfigWriter, §3.8 CLI, §5 safety → **deferred to Plan 4**.
- §4 first-apply safety + per-domain `op_install`/`op_remove` (systemd ENABLE/DISABLE, files CREATE/DELETE) → Task 1 (parameters + dedicated tests `test_first_apply_only_installs_no_remove`, `test_custom_ops_for_domain`). ✅
- §8 TDD + 80% coverage gate on touched modules → built into each task; final coverage step in Task 3. ✅

**2. Placeholder scan:** none — every code/test step contains full source. No "implement later" or "similar to Task N" patterns.

**3. Type consistency:**
- `compute_changes(domain, *, desired, managed, actual, op_install, op_remove) -> tuple[list[Change], list[str]]` signature matches every usage in Task 1 tests.
- `Change(domain, op, item, reason="")` and `Op.INSTALL/REMOVE/ENABLE/DISABLE` match the Plan 1 implementation in `dasik/lib/state/change.py`.
- `Change.destructive` is a `@property` (Plan 1 decision note) — tests use `c.destructive is True` consistently.
- `ActionContext(target=None, manifest=None)` accepted positionally and by keyword; defaults preserve the existing `ActionContext()` call-sites.
- `AbstractAction` v3 methods: `actual() -> set`, `plan(managed) -> list[Change]`, `apply(plan) -> None`, `import_state() -> dict`, `managed_keys() -> dict`, `is_v3() -> bool` (classmethod). Same names used in tests and in the deferred-work notes for Plans 3–4.
- `from dasik.lib.state.change import Change, Op` import path matches Plan 1's actual layout (`dasik/lib/state/change.py`).
- `Target(root="/mnt")` used in Task 2 tests matches the Plan 1 `Target` dataclass signature.

**Decision note:** `is_v3()` uses identity comparison (`cls.plan is not AbstractAction.plan`) rather than a class attribute flag. Reason: zero ceremony for v3 authors — overriding `plan` is the natural opt-in — and the check is O(1). Trade-off: a subclass that wants to expose the v3 surface without changing planning would need a non-default `plan` body, which is exactly what "v3" means anyway.

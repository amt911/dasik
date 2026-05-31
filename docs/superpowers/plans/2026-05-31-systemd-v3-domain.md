# systemd v3-domain Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the `systemd` domain to the v3 `plan`/`apply`/`sync` contract (second v3 domain after `packages`) and add a `disable_units` capability that turns units off even when dasik never enabled them.

**Architecture:** Extend the pure `set_math.compute_changes` with a `forced` set (ensure-removed regardless of M). `SystemdAction` gains the v3 methods (`actual`/`plan`/`apply`/`managed_keys`/`import_state`) over a flat `systemd` domain (`D_on = enable_units + enable_sockets`, `D_off = disable_units`), keeping its legacy `is_needed`/`execute` consistent with disables.

**Tech Stack:** Python 3.10+, pydantic, pytest/pytest-cov, `systemctl` via `Command.execute`.

Spec: `docs/superpowers/specs/2026-05-31-systemd-v3-domain-design.md`.

**Test runner note:** the repo has no installed pytest in the system env. Run tests in a throwaway venv:
```bash
python -m venv /tmp/dasik-venv && /tmp/dasik-venv/bin/pip install -q pytest pytest-cov colorama pydantic
PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest ...
```

---

## Task 1: `set_math.compute_changes` — `forced` parameter

**Files:**
- Modify: `dasik/lib/state/set_math.py`
- Test: `tests/lib/state/test_set_math.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/lib/state/test_set_math.py`:

```python
from dasik.lib.state.set_math import compute_changes
from dasik.lib.state.change import Op


def test_forced_disables_non_owned_present_unit():
    # bluetooth is enabled (A) but not declared (D) and not owned (M);
    # forced makes it a DISABLE anyway.
    changes, drift = compute_changes(
        "systemd",
        desired=[], managed=[], actual=["bluetooth.service"],
        op_install=Op.ENABLE, op_remove=Op.DISABLE,
        forced=["bluetooth.service"],
    )
    assert [(c.op, c.item, c.reason) for c in changes] == [
        (Op.DISABLE, "bluetooth.service", "explicitly disabled")
    ]
    assert drift == []  # forced unit is not drift


def test_forced_absent_unit_is_noop():
    # declared-off but not currently enabled → nothing to do.
    changes, drift = compute_changes(
        "systemd",
        desired=[], managed=[], actual=[],
        op_install=Op.ENABLE, op_remove=Op.DISABLE,
        forced=["bluetooth.service"],
    )
    assert changes == []
    assert drift == []


def test_forced_dedupes_with_owned_removal():
    # unit is both owned-no-longer-declared (M\D) and forced; emit one DISABLE.
    changes, _ = compute_changes(
        "systemd",
        desired=[], managed=["x.service"], actual=["x.service"],
        op_install=Op.ENABLE, op_remove=Op.DISABLE,
        forced=["x.service"],
    )
    disables = [c for c in changes if c.op is Op.DISABLE]
    assert len(disables) == 1
    assert disables[0].item == "x.service"
    assert disables[0].reason == "no longer declared"  # owned reason wins


def test_forced_excluded_from_drift():
    changes, drift = compute_changes(
        "systemd",
        desired=[], managed=[], actual=["a.service", "b.service"],
        op_install=Op.ENABLE, op_remove=Op.DISABLE,
        forced=["a.service"],
    )
    # a → DISABLE (forced); b → drift (not forced, not owned)
    assert [c.item for c in changes] == ["a.service"]
    assert drift == ["b.service"]


def test_no_forced_is_backward_compatible():
    # default forced=() must match prior behaviour for the packages path.
    changes, drift = compute_changes(
        "packages",
        desired=["git", "htop"], managed=["vim"], actual=["vim", "extra"],
    )
    assert [(c.op, c.item) for c in changes] == [
        (Op.INSTALL, "git"), (Op.INSTALL, "htop"), (Op.REMOVE, "vim"),
    ]
    assert drift == ["extra"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/state/test_set_math.py -k forced -v`
Expected: FAIL — `compute_changes() got an unexpected keyword argument 'forced'`.

- [ ] **Step 3: Implement the `forced` parameter**

Replace the body of `compute_changes` in `dasik/lib/state/set_math.py`. New signature + logic:

```python
def compute_changes(
    domain: str,
    *,
    desired: Iterable[str],
    managed: Iterable[str],
    actual: Iterable[str],
    op_install: Op = Op.INSTALL,
    op_remove: Op = Op.REMOVE,
    forced: Iterable[str] = (),
) -> tuple[list[Change], list[str]]:
    D, M, A, F = set(desired), set(managed), set(actual), set(forced)

    changes: list[Change] = []
    for item in sorted(D - A):
        changes.append(Change(domain, op_install, item))

    owned_removals = M - D
    for item in sorted(owned_removals):
        changes.append(Change(domain, op_remove, item, reason="no longer declared"))

    # Forced removals: declared-off units that are actually present, minus the
    # ones already emitted as owned removals (dedupe).
    for item in sorted((F & A) - owned_removals):
        changes.append(Change(domain, op_remove, item, reason="explicitly disabled"))

    drift = sorted(A - D - M - F)
    return changes, drift
```

Also update the docstring `Args:` section to document `forced` (one line: "forced: F — ensure-removed regardless of M; emits op_remove for F ∩ A. Precondition: D ∩ F = ∅.").

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/state/test_set_math.py -v`
Expected: PASS (new + all existing).

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/state/set_math.py tests/lib/state/test_set_math.py
git commit -m "feat(set_math): add forced (ensure-removed) param to compute_changes"
```

---

## Task 2: `SystemdModel` — `disable_units` + overlap validator

**Files:**
- Modify: `dasik/lib/models/systemd_model.py`
- Test: `tests/lib/models/test_systemd_model.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/models/test_systemd_model.py`:

```python
import pytest

from dasik.lib.models.systemd_model import SystemdModel


def test_defaults_are_empty_lists():
    m = SystemdModel()
    assert m.enable_units == []
    assert m.enable_sockets == []
    assert m.disable_units == []


def test_accepts_disjoint_enable_and_disable():
    m = SystemdModel(
        enable_units=["sshd.service"],
        enable_sockets=["cups.socket"],
        disable_units=["bluetooth.service"],
    )
    assert m.disable_units == ["bluetooth.service"]


def test_rejects_unit_in_both_enable_and_disable():
    with pytest.raises(ValueError):
        SystemdModel(
            enable_units=["sshd.service"],
            disable_units=["sshd.service"],
        )


def test_rejects_socket_in_both_enable_and_disable():
    with pytest.raises(ValueError):
        SystemdModel(
            enable_sockets=["cups.socket"],
            disable_units=["cups.socket"],
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/models/test_systemd_model.py -v`
Expected: FAIL — `disable_units` is not a field / overlap not rejected.

- [ ] **Step 3: Implement the model change**

Replace `dasik/lib/models/systemd_model.py` with:

```python
"""Models for systemd unit enablement."""
from typing import List
from pydantic import BaseModel, Field, model_validator


class SystemdModel(BaseModel):
    """Systemd services and sockets to enable, and units to disable."""
    enable_units: List[str] = Field(default_factory=list, description="Services/timers to enable")
    enable_sockets: List[str] = Field(default_factory=list, description="Sockets to enable")
    disable_units: List[str] = Field(default_factory=list, description="Units to ensure disabled")

    @model_validator(mode="after")
    def _no_enable_disable_overlap(self) -> "SystemdModel":
        enabled = set(self.enable_units) | set(self.enable_sockets)
        overlap = enabled & set(self.disable_units)
        if overlap:
            raise ValueError(
                f"units declared both enabled and disabled: {sorted(overlap)}"
            )
        return self
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/models/test_systemd_model.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/models/systemd_model.py tests/lib/models/test_systemd_model.py
git commit -m "feat(models): add disable_units to SystemdModel with overlap validation"
```

---

## Task 3: `SystemdAction.actual()` + constructor flatten

**Files:**
- Modify: `dasik/lib/actions/systemd_action.py`
- Test: `tests/lib/actions/test_systemd_action.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/lib/actions/test_systemd_action.py`:

```python
from unittest.mock import MagicMock, patch as _patch

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target


def _ctx(root="/"):
    return ActionContext(target=Target(root=root))


def test_constructor_exposes_d_on_and_d_off():
    a = SystemdAction(
        {"enable_units": ["sshd.service"], "enable_sockets": ["cups.socket"],
         "disable_units": ["bluetooth.service"]}
    )
    assert a._d_on() == ["sshd.service", "cups.socket"]
    assert a._d_off() == ["bluetooth.service"]


def test_actual_parses_enabled_unit_files():
    out = b"sshd.service enabled\ncups.socket enabled\nfstrim.timer enabled\n"
    fake = MagicMock(return_value=MagicMock(stdout=out, returncode=0))
    with _patch("dasik.lib.actions.systemd_action.Command.execute", fake):
        a = SystemdAction({}, _ctx("/"))
        assert a.actual() == {"sshd.service", "cups.socket", "fstrim.timer"}
    call = fake.call_args
    assert call.args[0] == "systemctl"
    assert call.args[1] == ["list-unit-files", "--state=enabled", "--no-legend"]
    assert call.kwargs["target"].root == "/"


def test_actual_empty_when_no_target():
    a = SystemdAction({}, None)
    assert a.actual() == set()


def test_is_v3_true():
    assert SystemdAction.is_v3() is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_systemd_action.py -k "d_on or actual or is_v3" -v`
Expected: FAIL — `_d_on` / `actual` not defined; `is_v3()` False.

- [ ] **Step 3: Implement constructor fields, `_d_on`/`_d_off`, `actual`**

In `dasik/lib/actions/systemd_action.py`, add the import and extend `__init__`/helpers. Change the import line:

```python
from typing import Any, List
from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command
import subprocess
```

Extend `__init__` (after the existing `self.sockets` line):

```python
        self.disable_units: List[str] = cfg.get("disable_units", [])

    _SYSTEMD_DOMAIN = "systemd"

    def _d_on(self) -> List[str]:
        return self.units + self.sockets

    def _d_off(self) -> List[str]:
        return self.disable_units

    def actual(self) -> set:
        """Set of all enabled unit files on the target (spec: A = all enabled)."""
        target = getattr(self.context, "target", None) if self.context else None
        if target is None:
            return set()
        result = Command.execute(
            "systemctl", ["list-unit-files", "--state=enabled", "--no-legend"],
            target=target,
        )
        stdout = getattr(result, "stdout", b"") or b""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        return {
            line.split()[0] for line in stdout.splitlines() if line.split()
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_systemd_action.py -v`
Expected: PASS (new + existing legacy tests).

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/systemd_action.py tests/lib/actions/test_systemd_action.py
git commit -m "feat(systemd): add actual() and D_on/D_off helpers (v3 groundwork)"
```

---

## Task 4: `SystemdAction.plan()` + `managed_keys()`

**Files:**
- Modify: `dasik/lib/actions/systemd_action.py`
- Test: `tests/lib/actions/test_systemd_action.py` (append)

- [ ] **Step 1: Write the failing tests**

Append:

```python
from dasik.lib.state.change import Op


def _action(cfg, actual):
    a = SystemdAction(cfg, _ctx("/"))
    a.actual = lambda: set(actual)   # stub system reality
    return a


def test_plan_enables_missing_declared_units():
    a = _action({"enable_units": ["sshd.service"]}, actual=[])
    changes = a.plan(managed=[])
    assert [(c.op, c.item) for c in changes] == [(Op.ENABLE, "sshd.service")]


def test_plan_disables_owned_no_longer_declared():
    a = _action({"enable_units": []}, actual=["old.service"])
    changes = a.plan(managed=["old.service"])
    assert [(c.op, c.item) for c in changes] == [(Op.DISABLE, "old.service")]


def test_plan_disables_forced_non_owned():
    a = _action({"disable_units": ["bluetooth.service"]}, actual=["bluetooth.service"])
    changes = a.plan(managed=[])
    assert [(c.op, c.item, c.reason) for c in changes] == [
        (Op.DISABLE, "bluetooth.service", "explicitly disabled")
    ]


def test_plan_empty_when_converged():
    a = _action({"enable_units": ["sshd.service"]}, actual=["sshd.service"])
    assert a.plan(managed=["sshd.service"]) == []


def test_managed_keys_is_d_on():
    a = SystemdAction(
        {"enable_units": ["sshd.service"], "enable_sockets": ["cups.socket"]}
    )
    assert a.managed_keys() == {"systemd": ["sshd.service", "cups.socket"]}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_systemd_action.py -k "plan or managed_keys" -v`
Expected: FAIL — `plan` returns `[]` (base class), `managed_keys` returns `{}`.

- [ ] **Step 3: Implement `plan` and `managed_keys`**

Add to `SystemdAction`:

```python
    def plan(self, managed):
        from ..state.set_math import compute_changes
        changes, _drift = compute_changes(
            self._SYSTEMD_DOMAIN,
            desired=self._d_on(),
            managed=managed,
            actual=self.actual(),
            op_install=Op.ENABLE,
            op_remove=Op.DISABLE,
            forced=self._d_off(),
        )
        return changes

    def managed_keys(self) -> dict:
        return {self._SYSTEMD_DOMAIN: self._d_on()}
```

Add the import at the top of the file (next to the others):

```python
from ..state.change import Op
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_systemd_action.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/systemd_action.py tests/lib/actions/test_systemd_action.py
git commit -m "feat(systemd): v3 plan() + managed_keys() (enable/disable/forced)"
```

---

## Task 5: `SystemdAction.apply()`

**Files:**
- Modify: `dasik/lib/actions/systemd_action.py`
- Test: `tests/lib/actions/test_systemd_action.py` (append)

- [ ] **Step 1: Write the failing tests**

Append:

```python
from dasik.lib.state.change import Change


def test_apply_enables_and_disables_routed():
    a = SystemdAction({}, _ctx("/"))
    changes = [
        Change("systemd", Op.ENABLE, "sshd.service"),
        Change("systemd", Op.DISABLE, "bluetooth.service"),
    ]
    with _patch("dasik.lib.actions.systemd_action.Command.execute") as run:
        a.apply(changes)
    calls = [(c.args[0], c.args[1]) for c in run.call_args_list]
    # enable runs before disable
    assert calls[0] == ("systemctl", ["enable", "sshd.service"])
    assert calls[1] == ("systemctl", ["disable", "bluetooth.service"])
    assert run.call_args_list[0].kwargs["target"].root == "/"


def test_apply_noop_on_empty():
    a = SystemdAction({}, _ctx("/"))
    with _patch("dasik.lib.actions.systemd_action.Command.execute") as run:
        a.apply([])
    run.assert_not_called()


def test_apply_noop_without_target():
    a = SystemdAction({}, None)
    with _patch("dasik.lib.actions.systemd_action.Command.execute") as run:
        a.apply([Change("systemd", Op.ENABLE, "sshd.service")])
    run.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_systemd_action.py -k apply -v`
Expected: FAIL — base `apply` is a no-op, calls never happen / ordering wrong.

- [ ] **Step 3: Implement `apply`**

Add to `SystemdAction`:

```python
    def apply(self, changes) -> None:
        target = getattr(self.context, "target", None) if self.context else None
        if target is None:
            return
        enables = [c.item for c in changes if c.op is Op.ENABLE]
        disables = [c.item for c in changes if c.op is Op.DISABLE]
        for unit in enables:                       # additive first
            Command.execute("systemctl", ["enable", unit], target=target)
        for unit in disables:
            Command.execute("systemctl", ["disable", unit], target=target)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_systemd_action.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/systemd_action.py tests/lib/actions/test_systemd_action.py
git commit -m "feat(systemd): v3 apply() routes ENABLE/DISABLE via systemctl"
```

---

## Task 6: `SystemdAction.import_state()` (sync)

**Files:**
- Modify: `dasik/lib/actions/systemd_action.py`
- Test: `tests/lib/actions/test_systemd_action.py` (append)

- [ ] **Step 1: Write the failing tests**

Append:

```python
def test_import_state_captures_drift_routed_by_suffix():
    a = _action(
        {"enable_units": ["sshd.service"], "enable_sockets": []},
        actual=["sshd.service", "docker.service", "cups.socket"],
    )
    frag = a.import_state(managed=[])
    sd = frag["systemd"]
    assert sd["enable_units"] == ["sshd.service", "docker.service"]
    assert sd["enable_sockets"] == ["cups.socket"]
    assert sd["disable_units"] == []


def test_import_state_drops_owned_but_vanished():
    a = _action({"enable_units": ["sshd.service", "old.service"]},
                actual=["sshd.service"])
    frag = a.import_state(managed=["sshd.service", "old.service"])
    assert frag["systemd"]["enable_units"] == ["sshd.service"]


def test_import_state_keeps_declared_intent_not_present():
    a = _action({"enable_units": ["sshd.service", "future.service"]},
                actual=["sshd.service"])
    frag = a.import_state(managed=[])  # future not owned → intent kept
    assert frag["systemd"]["enable_units"] == ["sshd.service", "future.service"]


def test_import_state_preserves_disable_units_and_excludes_them_from_drift():
    a = _action({"disable_units": ["bluetooth.service"]},
                actual=["bluetooth.service", "docker.service"])
    frag = a.import_state(managed=[])
    sd = frag["systemd"]
    assert sd["disable_units"] == ["bluetooth.service"]
    # bluetooth is forced-off → not captured as drift; docker is drift
    assert sd["enable_units"] == ["docker.service"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_systemd_action.py -k import_state -v`
Expected: FAIL — base `import_state` returns `{}`.

- [ ] **Step 3: Implement `import_state`**

Add to `SystemdAction`:

```python
    def import_state(self, managed=None) -> dict:
        managed_set = set(managed or [])
        actual = self.actual()
        d_off = set(self._d_off())

        vanished = managed_set - actual                       # M \ A
        kept_units = [u for u in self.units if u not in vanished]
        kept_sockets = [s for s in self.sockets if s not in vanished]

        drift = sorted(actual - set(self._d_on()) - managed_set - d_off)
        socket_drift = [d for d in drift if d.endswith(".socket")]
        unit_drift = [d for d in drift if not d.endswith(".socket")]

        return {self._SYSTEMD_DOMAIN: {
            "enable_units": kept_units + unit_drift,
            "enable_sockets": kept_sockets + socket_drift,
            "disable_units": list(self.disable_units),
        }}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_systemd_action.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/systemd_action.py tests/lib/actions/test_systemd_action.py
git commit -m "feat(systemd): v3 import_state() for sync (drift routed by suffix)"
```

---

## Task 7: Legacy `is_needed`/`execute`/`verify` honor disables

**Files:**
- Modify: `dasik/lib/actions/systemd_action.py`
- Test: `tests/lib/actions/test_systemd_action.py` (append)

Keeps the old `ActionExecutor` path consistent with `disable_units`.

- [ ] **Step 1: Write the failing tests**

Append (reuse the `_enabled_map` helper already at the top of the file):

```python
def test_legacy_is_needed_true_when_unit_to_disable_is_enabled():
    a = SystemdAction({"disable_units": ["bluetooth.service"]})
    with _patch("dasik.lib.actions.systemd_action.subprocess.run",
                _enabled_map({"bluetooth.service"})):
        assert a.is_needed() is True


def test_legacy_not_needed_when_disable_target_already_off():
    a = SystemdAction({"enable_units": ["sshd.service"],
                       "disable_units": ["bluetooth.service"]})
    with _patch("dasik.lib.actions.systemd_action.subprocess.run",
                _enabled_map({"sshd.service"})):  # bluetooth not enabled
        assert a.is_needed() is False
        assert a.verify() is True


def test_legacy_to_disable_lists_only_enabled_targets():
    a = SystemdAction({"disable_units": ["a.service", "b.service"]})
    with _patch("dasik.lib.actions.systemd_action.subprocess.run",
                _enabled_map({"a.service"})):
        assert a._to_disable() == ["a.service"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_systemd_action.py -k "legacy or to_disable" -v`
Expected: FAIL — `_to_disable` not defined; `is_needed` ignores disables.

- [ ] **Step 3: Implement `_to_disable` and extend `is_needed`/`execute`/`verify`**

In `SystemdAction`, replace the idempotency block (`is_needed`/`execute`/`verify`) with:

```python
    def _to_disable(self) -> List[str]:
        return [u for u in self.disable_units if self._is_enabled(u)]

    def is_needed(self) -> bool:
        return bool(self._pending()) or bool(self._to_disable())

    def execute(self) -> None:
        for unit in self._pending():
            print(f"  Enabling {unit} ...")
            subprocess.run(
                ["arch-chroot", "/mnt", "systemctl", "enable", unit],
                check=True,
            )
        for unit in self._to_disable():
            print(f"  Disabling {unit} ...")
            subprocess.run(
                ["arch-chroot", "/mnt", "systemctl", "disable", unit],
                check=True,
            )

    def verify(self) -> bool:
        return not self._pending() and not self._to_disable()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_systemd_action.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/systemd_action.py tests/lib/actions/test_systemd_action.py
git commit -m "feat(systemd): legacy is_needed/execute honor disable_units"
```

---

## Task 8: Config sample + full suite + coverage gate

**Files:**
- Modify: `config/install-megamix.json`
- Test: full suite

- [ ] **Step 1: Add a `disable_units` entry to the sample**

In `config/install-megamix.json`, inside the existing `"systemd"` object, add a
`disable_units` array after `enable_sockets` (pick a plausible unit, e.g.):

```jsonc
    "disable_units": [
      "systemd-networkd.service"
    ]
```

Ensure the JSON stays valid (comma after the previous array).

- [ ] **Step 2: Validate the sample parses**

Run:
```bash
PYTHONPATH=. /tmp/dasik-venv/bin/python -c "from dasik.lib.json_parser.json_parser import JsonParser; JsonParser('config/install-megamix.json').debug(); print('OK')"
```
Expected: `OK` (no validation error — the chosen disable unit must not also be in enable_units/enable_sockets).

- [ ] **Step 3: Run the full suite with coverage**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest --cov=dasik -q`
Expected: all pass; `Required test coverage of 80.0% reached`.

- [ ] **Step 4: Commit**

```bash
git add config/install-megamix.json
git commit -m "docs(config): exercise systemd disable_units in megamix sample"
```

---

## Self-Review notes

- **Spec coverage:** Task 1 = `forced` set-math; Task 2 = model + validator; Tasks 3-6 = `actual`/`plan`/`managed_keys`/`apply`/`import_state`; Task 7 = legacy consistency; Task 8 = sample + gate. All spec sections covered.
- **Type consistency:** domain string `"systemd"` (`_SYSTEMD_DOMAIN`), ops `Op.ENABLE`/`Op.DISABLE`, helper names `_d_on`/`_d_off`/`_to_disable`, `forced` kwarg — used identically across tasks.
- **Reconciler integration:** no Reconciler change needed — it already walks every `is_v3()` action; `SystemdAction` becomes v3 the moment `plan` is overridden (Task 4). `_domain_for` sees one key (`systemd`) → no multi-domain trigger.

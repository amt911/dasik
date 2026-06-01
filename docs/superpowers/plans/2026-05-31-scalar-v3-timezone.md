# Scalar v3 base + timezone Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a reusable `ScalarV3Action` base for single-value v3 domains and migrate `timezone` onto it, so `dasik plan/apply/sync` finally reconciles the timezone.

**Architecture:** `ScalarV3Action(AbstractAction)` implements the generic v3 contract (`actual`/`plan`/`apply`/`managed_keys`/`import_state`) over four subclass hooks (`_desired_value`/`_actual_value`/`_set_value`/`_import_fragment`). A scalar domain emits at most one `Op.MODIFY`; no CREATE/DELETE. `TimezoneAction` subclasses it and stays target-aware (falls back to `/mnt` for legacy call-sites).

**Tech Stack:** Python 3.10+, pytest/pytest-cov.

Spec: `docs/superpowers/specs/2026-05-31-scalar-v3-timezone-design.md`.

**Test runner:**
```bash
python -m venv /tmp/dasik-venv && /tmp/dasik-venv/bin/pip install -q pytest pytest-cov colorama pydantic
PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest ...
```

---

## Task 1: `ScalarV3Action` base

**Files:**
- Create: `dasik/lib/actions/scalar_action.py`
- Test: `tests/lib/actions/test_scalar_action.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/actions/test_scalar_action.py`:

```python
from dasik.lib.actions.scalar_action import ScalarV3Action
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Op


class _FakeScalar(ScalarV3Action):
    _DOMAIN = "thing"

    def __init__(self, desired, actual, context=None):
        super().__init__({}, context)
        self._d = desired
        self._a = actual
        self.set_calls = 0

    def _desired_value(self):
        return self._d

    def _actual_value(self):
        return self._a

    def _set_value(self):
        self.set_calls += 1

    def _import_fragment(self, value):
        return {"thing": value}

    @property
    def name(self):
        return "Fake Scalar"


def _ctx(root="/"):
    return ActionContext(target=Target(root=root))


def test_actual_wraps_value_in_set():
    assert _FakeScalar("x", "x").actual() == {"x"}


def test_actual_empty_when_no_value():
    assert _FakeScalar("x", None).actual() == set()


def test_plan_modify_when_desired_differs():
    changes = _FakeScalar("new", "old").plan(managed=[])
    assert [(c.op, c.item) for c in changes] == [(Op.MODIFY, "new")]


def test_plan_empty_when_equal():
    assert _FakeScalar("same", "same").plan(managed=[]) == []


def test_plan_empty_when_no_desired():
    assert _FakeScalar(None, "old").plan(managed=[]) == []


def test_apply_sets_value_when_changes_and_target():
    a = _FakeScalar("new", "old", _ctx("/"))
    a.apply([object()])
    assert a.set_calls == 1


def test_apply_noop_without_changes():
    a = _FakeScalar("new", "old", _ctx("/"))
    a.apply([])
    assert a.set_calls == 0


def test_apply_noop_without_target():
    a = _FakeScalar("new", "old", None)
    a.apply([object()])
    assert a.set_calls == 0


def test_managed_keys_lists_desired():
    assert _FakeScalar("x", None).managed_keys() == {"thing": ["x"]}
    assert _FakeScalar(None, None).managed_keys() == {"thing": []}


def test_import_state_uses_actual_then_desired():
    assert _FakeScalar("d", "a").import_state() == {"thing": "a"}
    assert _FakeScalar("d", None).import_state() == {"thing": "d"}
    assert _FakeScalar(None, None).import_state() == {}


def test_is_v3_true():
    assert _FakeScalar("x", "x").is_v3() is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_scalar_action.py -v`
Expected: FAIL — `scalar_action` module missing.

- [ ] **Step 3: Implement the base**

Create `dasik/lib/actions/scalar_action.py`:

```python
"""Base action for v3 domains whose state is a single value (not a set).

Set-math models a value change as INSTALL(new)+REMOVE(old); a scalar domain
wants one MODIFY instead. ScalarV3Action implements the v3 contract generically
over four subclass hooks. No CREATE/DELETE — a scalar is set or replaced, never
removed.
"""
from __future__ import annotations
from typing import Any, Optional
from .abstract_action import AbstractAction
from ..state.change import Change, Op


class ScalarV3Action(AbstractAction):
    """v3 contract for single-value domains."""

    _DOMAIN: str = ""

    # --- subclass hooks ------------------------------------------------ #

    def _desired_value(self) -> Optional[str]:
        raise NotImplementedError

    def _actual_value(self) -> Optional[str]:
        raise NotImplementedError

    def _set_value(self) -> None:
        raise NotImplementedError

    def _import_fragment(self, value: str) -> dict:
        raise NotImplementedError

    # --- generic v3 contract ------------------------------------------ #

    def actual(self) -> set:
        v = self._actual_value()
        return {v} if v else set()

    def plan(self, managed: Any):
        desired = self._desired_value()
        if desired and desired != self._actual_value():
            return [Change(self._DOMAIN, Op.MODIFY, desired, reason="set")]
        return []

    def apply(self, changes) -> None:
        target = getattr(self.context, "target", None) if self.context else None
        if changes and target is not None:
            self._set_value()

    def managed_keys(self) -> dict:
        desired = self._desired_value()
        return {self._DOMAIN: [desired] if desired else []}

    def import_state(self, managed=None) -> dict:
        value = self._actual_value() or self._desired_value()
        return self._import_fragment(value) if value else {}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_scalar_action.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/scalar_action.py tests/lib/actions/test_scalar_action.py
git commit -m "feat(actions): ScalarV3Action base for single-value v3 domains"
```

---

## Task 2: `TimezoneAction` → `ScalarV3Action`

**Files:**
- Modify: `dasik/lib/actions/timezone_action.py`
- Test: `tests/lib/actions/test_timezone_action.py` (append v3 tests; existing legacy tests stay)

The existing `tests/lib/actions/test_timezone_action.py` patches
`dasik.lib.actions.timezone_action.Path` and asserts `is_needed`/`verify`. Those must keep
passing — the refactor routes them through the new `_actual_value()` helper.

- [ ] **Step 1: Write the failing v3 tests**

Append to `tests/lib/actions/test_timezone_action.py`:

```python
from unittest.mock import MagicMock as _MM
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target


def _ctx(root="/"):
    return ActionContext(target=Target(root=root))


def test_is_v3_true():
    assert TimezoneAction(_cfg()).is_v3() is True


def test_desired_value_joins_region_city():
    assert TimezoneAction(_cfg())._desired_value() == "Europe/Madrid"


def test_actual_value_parses_symlink():
    a = TimezoneAction(_cfg(), _ctx("/"))
    with patch("dasik.lib.actions.timezone_action.Path",
               return_value=_link(target="/usr/share/zoneinfo/Asia/Tokyo")):
        assert a._actual_value() == "Asia/Tokyo"


def test_actual_value_none_when_not_symlink():
    a = TimezoneAction(_cfg(), _ctx("/"))
    with patch("dasik.lib.actions.timezone_action.Path",
               return_value=_link(is_symlink=False)):
        assert a._actual_value() is None


def test_plan_modify_when_zone_differs():
    a = TimezoneAction(_cfg(), _ctx("/"))
    with patch("dasik.lib.actions.timezone_action.Path",
               return_value=_link(target="/usr/share/zoneinfo/America/New_York")):
        changes = a.plan(managed=[])
    assert [(c.op, c.item) for c in changes] == [(Op.MODIFY, "Europe/Madrid")]


def test_plan_empty_when_zone_matches():
    a = TimezoneAction(_cfg(), _ctx("/"))
    with patch("dasik.lib.actions.timezone_action.Path", return_value=_link()):
        assert a.plan(managed=[]) == []


def test_set_value_issues_ln_and_hwclock_with_target():
    a = TimezoneAction(_cfg(), _ctx("/"))
    with patch("dasik.lib.actions.timezone_action.Command.execute") as run:
        a._set_value()
    cmds = [(c.args[0], c.args[1]) for c in run.call_args_list]
    assert ("ln", ["-sf", "/usr/share/zoneinfo/Europe/Madrid", "/etc/localtime"]) in cmds
    assert ("hwclock", ["--systohc"]) in cmds
    assert run.call_args_list[0].kwargs["target"].root == "/"


def test_import_fragment_splits_region_city():
    a = TimezoneAction(_cfg())
    assert a._import_fragment("Asia/Tokyo") == {
        "timezone": {"region": "Asia", "city": "Tokyo"}}
```

You will also need `Op` imported at the top of the test file:
```python
from dasik.lib.state.change import Op
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_timezone_action.py -k "v3 or desired_value or actual_value or plan or set_value or import_fragment" -v`
Expected: FAIL — `_desired_value`/`_actual_value`/`_set_value`/`_import_fragment` missing; `is_v3()` False.

- [ ] **Step 3: Rewrite `TimezoneAction`**

Replace `dasik/lib/actions/timezone_action.py` with:

```python
from typing import Any, Dict, Optional
from pathlib import Path
from .scalar_action import ScalarV3Action
from ..command_worker.command_worker import Command

_LOCALTIME = "/etc/localtime"
_ZONEINFO_MARKER = "/zoneinfo/"


class TimezoneAction(ScalarV3Action):
    """Configure system timezone (scalar v3 domain)."""

    _DOMAIN = "timezone"

    def __init__(self, config: Dict[str, Any], context=None):
        super().__init__(config, context)
        self.region: str = config["region"]
        self.city: str = config["city"]

    @property
    def name(self) -> str:
        return "Timezone Configuration"

    @property
    def is_optional(self) -> bool:
        return True

    # --- target helpers ----------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _localtime_path(self) -> str:
        t = self._target()
        return t.path(_LOCALTIME) if t is not None else "/mnt" + _LOCALTIME

    # --- scalar hooks ------------------------------------------------- #

    def _desired_value(self) -> Optional[str]:
        return f"{self.region}/{self.city}"

    def _actual_value(self) -> Optional[str]:
        link = Path(self._localtime_path())
        if not link.exists() or not link.is_symlink():
            return None
        try:
            target = link.readlink().as_posix()
        except Exception:
            return None
        idx = target.find(_ZONEINFO_MARKER)
        if idx == -1:
            return None
        return target[idx + len(_ZONEINFO_MARKER):] or None

    def _set_value(self) -> None:
        value = self._desired_value()
        link = f"/usr/share/zoneinfo/{value}"
        t = self._target()
        if t is not None:
            Command.execute("ln", ["-sf", link, _LOCALTIME], target=t)
            Command.execute("hwclock", ["--systohc"], target=t)
        else:
            Command.execute("ln", ["-sf", link, _LOCALTIME], True)
            Command.execute("hwclock", ["--systohc"], True)

    def _import_fragment(self, value: str) -> dict:
        region, _, city = value.partition("/")
        return {"timezone": {"region": region, "city": city}}

    # --- legacy executor path (is_needed/execute/verify) -------------- #

    def is_needed(self) -> bool:
        return self._desired_value() != self._actual_value()

    def execute(self) -> None:
        print(f"Setting timezone to {self._desired_value()} ...")
        self._set_value()

    def verify(self) -> bool:
        return self._desired_value() == self._actual_value()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_timezone_action.py -v`
Expected: PASS (new v3 tests + the existing legacy `is_needed`/`verify` tests).

> If a legacy test fails because it relied on the old hardcoded `/mnt` `Path("...")` with no
> context: the existing tests construct `TimezoneAction(_cfg())` (no context) → `_target()`
> is None → `_localtime_path()` returns `/mnt/etc/localtime`, and they patch
> `timezone_action.Path` anyway, so the path string is irrelevant. They should pass as-is.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/timezone_action.py tests/lib/actions/test_timezone_action.py
git commit -m "feat(timezone): migrate to ScalarV3Action (plan/apply/sync coverage)"
```

---

## Task 3: Integration — reconciler picks timezone + full suite

**Files:**
- Test: full suite (no product change expected)

- [ ] **Step 1: Confirm timezone is now a v3 domain end-to-end**

Run:
```bash
PYTHONPATH=. /tmp/dasik-venv/bin/python -c "
from dasik.lib.actions.timezone_action import TimezoneAction
print('is_v3:', TimezoneAction.is_v3())
print('domain:', TimezoneAction({'region':'Europe','city':'Madrid'}).managed_keys())
"
```
Expected: `is_v3: True` and `domain: {'timezone': ['Europe/Madrid']}`.

- [ ] **Step 2: Full suite + coverage**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest --cov=dasik -q`
Expected: all pass; `Required test coverage of 80.0% reached`.

- [ ] **Step 3: Sanity — sample still parses**

Run:
```bash
PYTHONPATH=. /tmp/dasik-venv/bin/python -c "from dasik.lib.json_parser.json_parser import JsonParser; JsonParser('config/install-megamix.json').debug(); print('OK')"
```
Expected: `OK` (timezone config shape unchanged).

- [ ] **Step 4: Commit (if anything changed; otherwise skip)**

No product change is expected in this task. If you adjusted tests, commit them:
```bash
git add -A
git commit -m "test(timezone): integration check for v3 reconciler coverage"
```

---

## Self-Review notes

- **Spec coverage:** Task 1 = `ScalarV3Action` base (all generic methods); Task 2 = `TimezoneAction` subclass (4 hooks + legacy refactor + target-awareness); Task 3 = integration + gate. All spec sections covered.
- **Type consistency:** `_DOMAIN` class attr, hooks `_desired_value`/`_actual_value`/`_set_value`/`_import_fragment`, `Op.MODIFY`, `_target`/`_localtime_path` helpers — consistent across tasks.
- **Reconciler integration:** `TimezoneAction` is registered `config_key="timezone"`; once it subclasses `ScalarV3Action`, `is_v3()` is True, so `build_plan`/`sync` include it. `_domain_for` sees the single `timezone` key. No reconciler change needed.
- **Legacy/target edge:** the old executor builds the action with `ActionContext()` (target None) → helpers fall back to `/mnt` (`_localtime_path`, `_set_value`), preserving install-time behaviour. v3 reconciler always passes a real target.
- **`_cfg`/`_link` helpers:** Task 2's appended tests reuse the `_cfg` and `_link` helpers already defined at the top of `tests/lib/actions/test_timezone_action.py`.

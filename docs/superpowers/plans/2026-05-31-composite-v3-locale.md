# composite v3 base + locale Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `CompositeV3Action` base (dict-shaped state over `ScalarV3Action`) and migrate `locale` onto it, so `dasik plan/apply/sync` reconciles the locale composite.

**Architecture:** A composite domain is a single canonically-serialized state value, so `CompositeV3Action` reuses `ScalarV3Action`'s value machinery and overrides `plan()` to emit one `MODIFY` listing the changed fields. `LocaleAction` provides the dict hooks (`_desired_state`/`_actual_state`) over `locale.gen`/`locale.conf`/`vconsole.conf` (target-aware) plus `_set_value`/`_import_fragment`.

**Tech Stack:** Python 3.10+, pytest/pytest-cov.

Spec: `docs/superpowers/specs/2026-05-31-composite-v3-locale-design.md`.

**Test runner:**
```bash
python -m venv /tmp/dasik-venv && /tmp/dasik-venv/bin/pip install -q pytest pytest-cov colorama pydantic
PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest ...
```

---

## Task 1: `CompositeV3Action` base

**Files:**
- Create: `dasik/lib/actions/composite_action.py`
- Test: `tests/lib/actions/test_composite_action.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/actions/test_composite_action.py`:

```python
from dasik.lib.actions.composite_action import CompositeV3Action
from dasik.lib.state.change import Op


class _FakeComposite(CompositeV3Action):
    _DOMAIN = "thing"

    def __init__(self, desired, actual):
        super().__init__({}, None)
        self._d = desired
        self._a = actual
        self.set_calls = 0

    def _desired_state(self):
        return self._d

    def _actual_state(self):
        return self._a

    def _set_value(self):
        self.set_calls += 1

    def _import_fragment(self, value):
        return {"thing": self._actual_state()}

    @property
    def name(self):
        return "Fake Composite"


def test_plan_empty_when_states_equal():
    a = _FakeComposite({"x": 1, "y": 2}, {"x": 1, "y": 2})
    assert a.plan(managed=[]) == []


def test_plan_modify_lists_changed_keys():
    a = _FakeComposite({"x": 1, "y": 2}, {"x": 1, "y": 9})
    changes = a.plan(managed=[])
    assert [(c.op, c.item) for c in changes] == [(Op.MODIFY, "y")]


def test_plan_all_keys_when_actual_none():
    a = _FakeComposite({"x": 1, "y": 2}, None)
    changes = a.plan(managed=[])
    assert changes[0].op is Op.MODIFY
    assert changes[0].item == "x,y"


def test_actual_wraps_serialized_value():
    a = _FakeComposite({"x": 1}, {"x": 1})
    assert a.actual() == {'{"x": 1}'}


def test_actual_empty_when_state_none():
    a = _FakeComposite({"x": 1}, None)
    assert a.actual() == set()


def test_managed_keys_carries_serialized_desired():
    a = _FakeComposite({"b": 2, "a": 1}, None)
    assert a.managed_keys() == {"thing": ['{"a": 1, "b": 2}']}  # sort_keys canonical


def test_is_v3_true():
    assert _FakeComposite({"x": 1}, {"x": 1}).is_v3() is True


def test_empty_config_is_empty_dict():
    assert _FakeComposite.empty_config() == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_composite_action.py -v`
Expected: FAIL — `composite_action` module missing.

- [ ] **Step 3: Implement the base**

Create `dasik/lib/actions/composite_action.py`:

```python
"""Base action for v3 domains whose state is a composite record (a dict).

A composite is compared via a canonical JSON serialization, so a converged
record yields no change (idempotent). It reuses ScalarV3Action's value-based
machinery (actual/managed_keys/import_state/is_needed/execute/verify) and emits
a single MODIFY listing the changed fields.
"""
from __future__ import annotations
import json
from typing import Optional
from .scalar_action import ScalarV3Action
from ..state.change import Change, Op


class CompositeV3Action(ScalarV3Action):
    """v3 contract for multi-field (composite) domains."""

    # --- subclass hooks ------------------------------------------------ #

    def _desired_state(self) -> dict:
        raise NotImplementedError

    def _actual_state(self) -> Optional[dict]:
        raise NotImplementedError

    # --- bridge to ScalarV3Action's value machinery ------------------- #

    @staticmethod
    def _serialize(state: dict) -> str:
        return json.dumps(state, sort_keys=True)

    def _desired_value(self) -> Optional[str]:
        return self._serialize(self._desired_state())

    def _actual_value(self) -> Optional[str]:
        state = self._actual_state()
        return self._serialize(state) if state is not None else None

    # --- field-aware plan (clean render) ------------------------------ #

    def plan(self, managed):
        desired = self._desired_state()
        actual = self._actual_state()
        if actual == desired:
            return []
        if actual is None:
            changed = sorted(desired)
        else:
            changed = sorted(k for k in desired if desired.get(k) != actual.get(k))
        item = ",".join(changed) or self._DOMAIN
        return [Change(self._DOMAIN, Op.MODIFY, item, reason="config")]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_composite_action.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/composite_action.py tests/lib/actions/test_composite_action.py
git commit -m "feat(actions): CompositeV3Action base for multi-field v3 domains"
```

---

## Task 2: `LocaleAction` → `CompositeV3Action`

**Files:**
- Modify: `dasik/lib/actions/locale_action.py`
- Test: `tests/lib/actions/test_locale_action.py` (rewrite for the v3 contract)

- [ ] **Step 1: Rewrite the test file**

Replace `tests/lib/actions/test_locale_action.py` entirely:

```python
from unittest.mock import mock_open, patch

from dasik.lib.actions.locale_action import LocaleAction
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Op


def _ctx(root="/"):
    return ActionContext(target=Target(root=root))


def _cfg(selected=None, locale="en_US.UTF-8", layout="us"):
    return {
        "selected_locales": selected if selected is not None else ["en_US.UTF-8 UTF-8"],
        "desired_locale": locale,
        "desired_tty_layout": layout,
    }


_GEN = "#es_ES.UTF-8 UTF-8\nen_US.UTF-8 UTF-8\n"


def _open_tree(gen=_GEN, conf="LANG=en_US.UTF-8", vconsole="KEYMAP=us", missing=()):
    def opener(path, *a, **k):
        p = str(path)
        if "locale.gen" in p:
            data = gen
        elif "locale.conf" in p:
            if "locale.conf" in missing:
                raise FileNotFoundError(p)
            data = conf
        else:
            if "vconsole" in missing:
                raise FileNotFoundError(p)
            data = vconsole
        return mock_open(read_data=data)()
    return patch("builtins.open", side_effect=opener)


def test_is_v3_true():
    assert LocaleAction.is_v3() is True


def test_desired_state_sorts_selected():
    a = LocaleAction(_cfg(selected=["en_US.UTF-8 UTF-8", "es_ES.UTF-8 UTF-8"]), _ctx("/"))
    st = a._desired_state()
    assert st == {
        "selected_locales": ["en_US.UTF-8 UTF-8", "es_ES.UTF-8 UTF-8"],
        "desired_locale": "en_US.UTF-8",
        "desired_tty_layout": "us",
    }


def test_actual_state_parses_three_files():
    a = LocaleAction(_cfg(), _ctx("/"))
    with _open_tree():
        st = a._actual_state()
    assert st == {
        "selected_locales": ["en_US.UTF-8 UTF-8"],
        "desired_locale": "en_US.UTF-8",
        "desired_tty_layout": "us",
    }


def test_actual_state_none_when_locale_conf_missing():
    a = LocaleAction(_cfg(), _ctx("/"))
    with _open_tree(missing=("locale.conf",)):
        assert a._actual_state() is None


def test_actual_state_none_when_vconsole_missing():
    a = LocaleAction(_cfg(), _ctx("/"))
    with _open_tree(missing=("vconsole",)):
        assert a._actual_state() is None


def test_plan_empty_when_converged():
    a = LocaleAction(_cfg(), _ctx("/"))
    with _open_tree():
        assert a.plan(managed=[]) == []


def test_plan_modify_when_lang_differs():
    a = LocaleAction(_cfg(locale="de_DE.UTF-8"), _ctx("/"))
    with _open_tree():
        changes = a.plan(managed=[])
    assert changes[0].op is Op.MODIFY and "desired_locale" in changes[0].item


def test_plan_modify_when_keymap_differs():
    a = LocaleAction(_cfg(layout="es"), _ctx("/"))
    with _open_tree():
        changes = a.plan(managed=[])
    assert changes[0].op is Op.MODIFY and "desired_tty_layout" in changes[0].item


def test_import_fragment_returns_live_state():
    a = LocaleAction(_cfg(), _ctx("/"))
    with _open_tree():
        frag = a.import_state(managed=[])
    assert frag == {"locales": {
        "selected_locales": ["en_US.UTF-8 UTF-8"],
        "desired_locale": "en_US.UTF-8",
        "desired_tty_layout": "us",
    }}


def test_name_and_optional():
    a = LocaleAction(_cfg())
    assert a.name == "Locale Configuration"
    assert a.is_optional is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_locale_action.py -v`
Expected: FAIL — `_desired_state`/`_actual_state` missing; `is_v3()` False (still legacy).

- [ ] **Step 3: Rewrite `LocaleAction`**

Replace `dasik/lib/actions/locale_action.py` with:

```python
"""Action: configure system locales (locale.gen, locale.conf, vconsole.conf).

Composite v3 domain "locales": the desired state is the (selected_locales,
LANG, KEYMAP) record; one MODIFY when any field drifts. Target-aware.
"""
from __future__ import annotations
import re
from typing import Any, Dict, List, Optional
from .composite_action import CompositeV3Action
from ..command_worker.command_worker import Command

_LOCALE_GEN = "/etc/locale.gen"
_LOCALE_CONF = "/etc/locale.conf"
_VCONSOLE_CONF = "/etc/vconsole.conf"


class LocaleAction(CompositeV3Action):
    """Configure locales declaratively (composite v3 domain)."""

    _DOMAIN = "locales"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self._selected_locales: List[str] = cfg.get("selected_locales", [])
        self._desired_locale: str = cfg.get("desired_locale", "")
        self._desired_tty_layout: str = cfg.get("desired_tty_layout", "")

    @property
    def name(self) -> str:
        return "Locale Configuration"

    @property
    def is_optional(self) -> bool:
        return True

    # --- target-aware paths ------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _p(self, canonical: str) -> str:
        t = self._target()
        return t.path(canonical) if t is not None else "/mnt" + canonical

    # --- composite state ---------------------------------------------- #

    def _desired_state(self) -> dict:
        return {
            "selected_locales": sorted(self._selected_locales),
            "desired_locale": self._desired_locale,
            "desired_tty_layout": self._desired_tty_layout,
        }

    def _read(self, canonical: str) -> Optional[str]:
        try:
            with open(self._p(canonical), "r") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def _actual_state(self) -> Optional[dict]:
        gen = self._read(_LOCALE_GEN)
        conf = self._read(_LOCALE_CONF)
        vconsole = self._read(_VCONSOLE_CONF)
        if gen is None or conf is None or vconsole is None:
            return None
        uncommented = re.findall(r"^[a-z]+_\S+ \S+", gen, re.MULTILINE)
        lang = ""
        for line in conf.splitlines():
            if line.startswith("LANG="):
                lang = line.split("=", 1)[1].strip()
        keymap = ""
        for line in vconsole.splitlines():
            if line.startswith("KEYMAP="):
                keymap = line.split("=", 1)[1].strip()
        return {
            "selected_locales": sorted(uncommented),
            "desired_locale": lang,
            "desired_tty_layout": keymap,
        }

    def _import_fragment(self, value) -> dict:
        return {self._DOMAIN: self._actual_state() or self._desired_state()}

    def _set_value(self) -> None:  # pragma: no cover - writes /etc + runs locale-gen
        gen_path = self._p(_LOCALE_GEN)
        with open(gen_path, "r") as f:
            text = f.read()
        text = re.sub(r"(^[a-z]+)", r"#\1", text, 0, re.MULTILINE)  # comment all
        for loc in self._selected_locales:
            text = text.replace(f"#{loc}", f"{loc}")
        with open(gen_path, "w") as f:
            f.write(text)
        with open(self._p(_LOCALE_CONF), "w") as f:
            f.write(f"LANG={self._desired_locale}")
        with open(self._p(_VCONSOLE_CONF), "w") as f:
            f.write(f"KEYMAP={self._desired_tty_layout}")
        t = self._target()
        if t is not None:
            Command.execute("locale-gen", [], target=t)
        else:
            Command.execute("locale-gen", [], True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_locale_action.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/locale_action.py tests/lib/actions/test_locale_action.py
git commit -m "feat(locale): migrate to CompositeV3Action (plan/apply/sync coverage)"
```

---

## Task 3: integration + full suite + gate

**Files:**
- Test: full suite (no product change expected)

- [ ] **Step 1: Confirm locale is now a v3 domain**

Run:
```bash
PYTHONPATH=. /tmp/dasik-venv/bin/python -c "
from dasik.lib.actions.locale_action import LocaleAction
print('is_v3:', LocaleAction.is_v3())
print('keys:', LocaleAction({'selected_locales':['en_US.UTF-8 UTF-8'],'desired_locale':'en_US.UTF-8','desired_tty_layout':'us'}).managed_keys())
"
```
Expected: `is_v3: True` and a `{'locales': ['{...}']}` serialized managed value.

- [ ] **Step 2: Sample still parses (locales shape unchanged)**

Run:
```bash
PYTHONPATH=. /tmp/dasik-venv/bin/python -c "from dasik.lib.json_parser.json_parser import JsonParser; JsonParser('config/install-megamix.json').debug(); print('OK')"
```
Expected: `OK`.

- [ ] **Step 3: Full suite + coverage**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest --cov=dasik -q`
Expected: all pass; `Required test coverage of 80.0% reached`.

- [ ] **Step 4: Commit (only if tests were adjusted; otherwise skip)**

No product change expected here. If anything was tweaked:
```bash
git add -A
git commit -m "test(locale): integration check for v3 reconciler coverage"
```

---

## Self-Review notes

- **Spec coverage:** Task 1 = `CompositeV3Action` (serialized state + field-aware plan); Task 2 = `LocaleAction` dict hooks + target-aware IO + `_set_value`/`_import_fragment`; Task 3 = integration + gate. All spec sections covered.
- **Type consistency:** `CompositeV3Action._desired_state/_actual_state/_serialize`, `LocaleAction._DOMAIN="locales"`, helpers `_p`/`_read`, `Op.MODIFY` — consistent across tasks.
- **Reconciler integration:** `LocaleAction` registered `config_key="locales"`; subclassing `CompositeV3Action` (which overrides `plan`) makes `is_v3()` True so `build_plan`/`sync` include it; `_domain_for` sees the single `locales` key; `empty_config()` is `{}` (inherited) so a missing slice with owned entries bootstraps correctly.
- **Idempotency:** `_desired_state` and `_actual_state` both sort `selected_locales`, so a converged locale serializes identically → `plan()` empty (`test_plan_empty_when_converged`).
- **Legacy path:** `is_needed`/`execute`/`verify` are now inherited from `ScalarV3Action` (via `CompositeV3Action`) — `is_needed = bool(plan)`, `execute = _set_value`, `verify = not plan` — so the old executor path still works; the action's own legacy methods are removed.
- **`_set_value` coverage:** marked `# pragma: no cover` (writes /etc + runs locale-gen), consistent with other destructive `execute`/apply bodies.

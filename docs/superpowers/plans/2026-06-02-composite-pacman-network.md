# Composite migration: pacman + network Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate `PacmanAction` and `NetworkAction` onto the `CompositeV3Action` contract so both participate in `plan`/`apply`/`sync`, are target-aware, and enforce desired state bidirectionally.

**Architecture:** Each action subclasses `CompositeV3Action` (like `LocaleAction`): implement `_desired_state`/`_actual_state`/`_set_value`/`_import_fragment`, plus target-aware `_p()`. pacman enforces the four known flags both directions; network keeps `type` validation but excludes `type` from the comparison record and short-circuits when nothing is declared.

**Tech Stack:** Python 3.10+, pydantic (no model changes here), pytest, `unittest.mock`. Tests drive real temp files via `Target(root=tmp_path)`.

**Spec:** `docs/superpowers/specs/2026-06-02-composite-pacman-network-design.md`

**Reference implementation:** `dasik/lib/actions/locale_action.py` + `tests/lib/actions/test_locale_action.py`.

**Pre-flight (read before starting):**
- `dasik/lib/actions/composite_action.py` — base: `_desired_state`/`_actual_state` hooks, field-aware `plan()` emitting one `MODIFY`.
- `dasik/lib/actions/scalar_action.py` — `actual()`/`is_needed()`/`execute()`/`apply()`/`managed_keys()`/`import_state()` defaults. Note `apply()` only calls `_set_value()` when `changes and target is not None`; `execute()` calls `_set_value()` unconditionally (legacy `do_action()` path).
- `dasik/lib/target/target.py` — `Target.path("/etc/x")` → `"/mnt/etc/x"` (or unchanged for root `/`).

**Branch:** `feat-composite-pacman-network` (already created; spec already committed).

---

## Task 1: PacmanAction → CompositeV3Action

**Files:**
- Modify (full rewrite): `dasik/lib/actions/pacman_action.py`
- Modify (full rewrite): `tests/lib/actions/test_pacman_action.py`

- [ ] **Step 1: Replace the test file with the v3 contract tests (failing)**

Overwrite `tests/lib/actions/test_pacman_action.py` with:

```python
from dasik.lib.actions.pacman_action import PacmanAction
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Op

_COMMENTED = """\
#ParallelDownloads = 5
#Color
#VerbosePkgLists
#[multilib]
#Include = /etc/pacman.d/mirrorlist
"""

_ACTIVE = """\
ParallelDownloads = 5
Color
VerbosePkgLists
[multilib]
Include = /etc/pacman.d/mirrorlist
"""


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


def _write_conf(tmp_path, text):
    etc = tmp_path / "etc"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "pacman.conf").write_text(text)


def _cfg(parallel=True, color=True, verbose=False, multilib=False):
    return {
        "options": {"Parallel": parallel, "Color": color, "VerbosePkgLists": verbose},
        "multilib": multilib,
    }


def test_is_v3_true():
    assert PacmanAction.is_v3() is True


def test_desired_state():
    a = PacmanAction(_cfg(parallel=True, color=False, verbose=True, multilib=True))
    assert a._desired_state() == {
        "Parallel": True, "Color": False, "VerbosePkgLists": True, "multilib": True,
    }


def test_actual_state_active(tmp_path):
    _write_conf(tmp_path, _ACTIVE)
    a = PacmanAction(_cfg(), _ctx(tmp_path))
    assert a._actual_state() == {
        "Parallel": True, "Color": True, "VerbosePkgLists": True, "multilib": True,
    }


def test_actual_state_commented(tmp_path):
    _write_conf(tmp_path, _COMMENTED)
    a = PacmanAction(_cfg(), _ctx(tmp_path))
    assert a._actual_state() == {
        "Parallel": False, "Color": False, "VerbosePkgLists": False, "multilib": False,
    }


def test_actual_state_none_when_missing(tmp_path):
    a = PacmanAction(_cfg(), _ctx(tmp_path))  # no pacman.conf written
    assert a._actual_state() is None


def test_plan_empty_when_converged(tmp_path):
    _write_conf(tmp_path, _ACTIVE)
    a = PacmanAction(_cfg(parallel=True, color=True, verbose=True, multilib=True), _ctx(tmp_path))
    assert a.plan(managed=[]) == []


def test_plan_modify_when_flag_on_but_commented(tmp_path):
    _write_conf(tmp_path, _COMMENTED)
    a = PacmanAction(_cfg(color=True), _ctx(tmp_path))
    changes = a.plan(managed=[])
    assert changes and changes[0].op is Op.MODIFY and "Color" in changes[0].item


def test_plan_modify_when_flag_off_but_active(tmp_path):
    # bidirectional: Color declared False but active in conf -> MODIFY
    _write_conf(tmp_path, _ACTIVE)
    a = PacmanAction(_cfg(parallel=True, color=False, verbose=True, multilib=True), _ctx(tmp_path))
    changes = a.plan(managed=[])
    assert changes and changes[0].op is Op.MODIFY and "Color" in changes[0].item


def test_set_value_enables_all(tmp_path):
    _write_conf(tmp_path, _COMMENTED)
    a = PacmanAction(_cfg(parallel=True, color=True, verbose=True, multilib=True), _ctx(tmp_path))
    a._set_value()
    assert a._actual_state() == {
        "Parallel": True, "Color": True, "VerbosePkgLists": True, "multilib": True,
    }


def test_set_value_disables_all(tmp_path):
    # bidirectional down: active conf, all flags False -> commented back out
    _write_conf(tmp_path, _ACTIVE)
    a = PacmanAction(_cfg(parallel=False, color=False, verbose=False, multilib=False), _ctx(tmp_path))
    a._set_value()
    assert a._actual_state() == {
        "Parallel": False, "Color": False, "VerbosePkgLists": False, "multilib": False,
    }


def test_set_value_idempotent(tmp_path):
    _write_conf(tmp_path, _COMMENTED)
    cfg = _cfg(parallel=True, color=True, verbose=True, multilib=True)
    a = PacmanAction(cfg, _ctx(tmp_path))
    a._set_value()
    a._set_value()  # second run is a no-op
    assert a.plan(managed=[]) == []


def test_import_fragment_shape(tmp_path):
    _write_conf(tmp_path, _ACTIVE)
    a = PacmanAction(_cfg(), _ctx(tmp_path))
    frag = a.import_state(managed=[])
    assert frag == {"pacman": {
        "options": {"Parallel": True, "Color": True, "VerbosePkgLists": True},
        "multilib": True,
    }}


def test_name_and_optional():
    a = PacmanAction(_cfg())
    assert a.name == "Pacman Configuration"
    assert a.is_optional is True
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `pytest tests/lib/actions/test_pacman_action.py -q`
Expected: failures/errors (old `PacmanAction` has no `_desired_state`/`_actual_state`/`is_v3`-as-True; `_actual_state` etc. missing).

- [ ] **Step 3: Rewrite the action**

Overwrite `dasik/lib/actions/pacman_action.py` with:

```python
"""Action: configure pacman.conf (parallel, color, verbose, multilib).

Composite v3 domain "pacman": the desired state is the four flags dasik knows
(Parallel, Color, VerbosePkgLists, multilib). Bidirectional — a flag set False
is commented back out and the [multilib] block re-commented. Target-aware. One
MODIFY when any flag drifts.
"""
from __future__ import annotations
import re
from typing import Any, Dict, Optional
from .composite_action import CompositeV3Action

_PACMAN_CONF = "/etc/pacman.conf"

# config-facing flag -> pacman.conf token
_OPTION_TOKENS = {
    "Parallel": "ParallelDownloads",
    "Color": "Color",
    "VerbosePkgLists": "VerbosePkgLists",
}


class PacmanAction(CompositeV3Action):
    """Configure pacman.conf declaratively (composite v3 domain)."""

    _DOMAIN = "pacman"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        opts = cfg.get("options", {}) or {}
        self.parallel = opts.get("Parallel", True)
        self.color = opts.get("Color", True)
        self.verbose = opts.get("VerbosePkgLists", False)
        self.multilib = cfg.get("multilib", False)

    @property
    def name(self) -> str:
        return "Pacman Configuration"

    @property
    def is_optional(self) -> bool:
        return True

    # --- target-aware paths ------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _p(self, canonical: str) -> str:
        t = self._target()
        return t.path(canonical) if t is not None else "/mnt" + canonical

    def _read(self) -> Optional[str]:
        try:
            with open(self._p(_PACMAN_CONF), "r") as f:
                return f.read()
        except FileNotFoundError:
            return None

    # --- conf parsing helpers ----------------------------------------- #

    @staticmethod
    def _option_active(text: str, token: str) -> bool:
        return bool(re.search(rf"^\s*{token}\b", text, re.MULTILINE))

    @staticmethod
    def _multilib_active(text: str) -> bool:
        return re.search(r"^\[multilib\]\s*\n\s*Include", text, re.MULTILINE) is not None

    # --- composite state ---------------------------------------------- #

    def _desired_state(self) -> dict:
        return {
            "Parallel": bool(self.parallel),
            "Color": bool(self.color),
            "VerbosePkgLists": bool(self.verbose),
            "multilib": bool(self.multilib),
        }

    def _actual_state(self) -> Optional[dict]:
        text = self._read()
        if text is None:
            return None
        return {
            "Parallel": self._option_active(text, "ParallelDownloads"),
            "Color": self._option_active(text, "Color"),
            "VerbosePkgLists": self._option_active(text, "VerbosePkgLists"),
            "multilib": self._multilib_active(text),
        }

    def _import_fragment(self, value) -> dict:
        st = self._actual_state() or self._desired_state()
        return {self._DOMAIN: {
            "options": {
                "Parallel": st["Parallel"],
                "Color": st["Color"],
                "VerbosePkgLists": st["VerbosePkgLists"],
            },
            "multilib": st["multilib"],
        }}

    def _set_value(self) -> None:
        text = self._read() or ""
        desired = self._desired_state()
        for flag, token in _OPTION_TOKENS.items():
            if desired[flag]:
                text = re.sub(rf"^#\s*({token}\b.*)", r"\1", text, flags=re.MULTILINE)
            else:
                text = re.sub(rf"^({token}\b.*)", r"#\1", text, flags=re.MULTILINE)
        if desired["multilib"]:
            text = re.sub(
                r"^#\s*\[multilib\]\s*\n#\s*(Include\s*=.*)",
                r"[multilib]\n\1",
                text, flags=re.MULTILINE,
            )
        else:
            text = re.sub(
                r"^\[multilib\]\s*\n(Include\s*=.*)",
                r"#[multilib]\n#\1",
                text, flags=re.MULTILINE,
            )
        with open(self._p(_PACMAN_CONF), "w") as f:
            f.write(text)
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `pytest tests/lib/actions/test_pacman_action.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/pacman_action.py tests/lib/actions/test_pacman_action.py
git commit -m "feat(pacman): migrate to CompositeV3Action (bidirectional, target-aware)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: NetworkAction → CompositeV3Action

**Files:**
- Modify (full rewrite): `dasik/lib/actions/network_action.py`
- Modify (full rewrite): `tests/lib/actions/test_network_action.py`

- [ ] **Step 1: Replace the test file with the v3 contract tests (failing)**

Overwrite `tests/lib/actions/test_network_action.py` with:

```python
import pytest

from dasik.lib.actions.network_action import NetworkAction
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Op
from dasik.lib.exceptions.exceptions import NetworkTypeNotFoundException

_BLOCK = "127.0.0.1 localhost\n::1 localhost\n127.0.1.1 arch\n"


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


def _cfg(hostname="arch", add_hosts=True, ntype="NetworkManager"):
    return {"hostname": hostname, "network": {"type": ntype, "add_default_hosts": add_hosts}}


def _write(tmp_path, hostname=None, hosts=None):
    etc = tmp_path / "etc"
    etc.mkdir(parents=True, exist_ok=True)
    if hostname is not None:
        (etc / "hostname").write_text(hostname)
    if hosts is not None:
        (etc / "hosts").write_text(hosts)


def test_is_v3_true():
    assert NetworkAction.is_v3() is True


def test_reads_root_hostname_and_network_section():
    a = NetworkAction(_cfg(hostname="box", ntype="systemd-networkd"))
    assert a.hostname == "box" and a.type == "systemd-networkd" and a.add_default_hosts is True


def test_desired_state_excludes_type():
    a = NetworkAction(_cfg(hostname="arch", add_hosts=True))
    assert a._desired_state() == {"hostname": "arch", "default_hosts": True}


def test_actual_state_none_when_hostname_missing(tmp_path):
    a = NetworkAction(_cfg(), _ctx(tmp_path))  # no /etc/hostname
    assert a._actual_state() is None


def test_actual_state_reads_hostname_and_block(tmp_path):
    _write(tmp_path, hostname="arch\n", hosts=_BLOCK)
    a = NetworkAction(_cfg(hostname="arch"), _ctx(tmp_path))
    assert a._actual_state() == {"hostname": "arch", "default_hosts": True}


def test_plan_empty_when_converged(tmp_path):
    _write(tmp_path, hostname="arch\n", hosts=_BLOCK)
    a = NetworkAction(_cfg(hostname="arch", add_hosts=True), _ctx(tmp_path))
    assert a.plan(managed=[]) == []


def test_plan_modify_when_hostname_differs(tmp_path):
    _write(tmp_path, hostname="oldname\n", hosts=_BLOCK)
    a = NetworkAction(_cfg(hostname="arch"), _ctx(tmp_path))
    changes = a.plan(managed=[])
    assert changes and changes[0].op is Op.MODIFY and "hostname" in changes[0].item


def test_plan_modify_when_default_hosts_absent(tmp_path):
    _write(tmp_path, hostname="arch\n", hosts="# empty\n")
    a = NetworkAction(_cfg(hostname="arch", add_hosts=True), _ctx(tmp_path))
    changes = a.plan(managed=[])
    assert changes and changes[0].op is Op.MODIFY and "default_hosts" in changes[0].item


def test_import_fragment_two_keys_with_type_passthrough(tmp_path):
    _write(tmp_path, hostname="arch\n", hosts=_BLOCK)
    a = NetworkAction(_cfg(hostname="arch", ntype="systemd-networkd"), _ctx(tmp_path))
    frag = a.import_state(managed=[])
    assert frag == {
        "hostname": "arch",
        "network": {"type": "systemd-networkd", "add_default_hosts": True},
    }


def test_nothing_declared_guard_empty_plan(tmp_path):
    a = NetworkAction({"packages": ["git"]}, _ctx(tmp_path))  # no hostname
    assert a.hostname == ""
    assert a.plan(managed=[]) == []
    assert a.import_state(managed=[]) == {}


def test_nothing_declared_guard_set_value_noop_no_raise(tmp_path):
    a = NetworkAction({"packages": ["git"]}, _ctx(tmp_path))  # type == "" would raise
    a._set_value()  # must NOT raise NetworkTypeNotFoundException
    assert not (tmp_path / "etc" / "hostname").exists()


def test_set_value_writes_hostname_and_block(tmp_path):
    _write(tmp_path, hosts="192.168.0.1 router\n")
    a = NetworkAction(_cfg(hostname="arch", add_hosts=True), _ctx(tmp_path))
    a._set_value()
    assert (tmp_path / "etc" / "hostname").read_text() == "arch"
    hosts_text = (tmp_path / "etc" / "hosts").read_text()
    assert "127.0.1.1 arch" in hosts_text and "192.168.0.1 router" in hosts_text


def test_set_value_idempotent(tmp_path):
    _write(tmp_path, hosts="")
    a = NetworkAction(_cfg(hostname="arch", add_hosts=True), _ctx(tmp_path))
    a._set_value()
    a._set_value()
    assert a.plan(managed=[]) == []


def test_invalid_type_raises_on_set_value(tmp_path):
    _write(tmp_path, hosts="")
    a = NetworkAction(_cfg(hostname="arch", ntype="bogus"), _ctx(tmp_path))
    with pytest.raises(NetworkTypeNotFoundException):
        a._set_value()


def test_name_and_optional():
    a = NetworkAction(_cfg())
    assert a.name == "Network Configuration"
    assert a.is_optional is True
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `pytest tests/lib/actions/test_network_action.py -q`
Expected: failures/errors (old `NetworkAction` lacks `_desired_state`/`_actual_state`/`is_v3`-True/guard).

- [ ] **Step 3: Rewrite the action**

Overwrite `dasik/lib/actions/network_action.py` with:

```python
"""Action: configure hostname + /etc/hosts (composite v3 domain "network").

Registered under ``__root__``: reads root-level ``hostname`` plus the
``network`` section. The comparison record is (hostname, default_hosts
presence); ``network.type`` is validated on apply but excluded from the record
(no on-disk file) and passed through verbatim on import. Target-aware.

Nothing-declared guard: with no ``hostname`` the action is a no-op (empty plan,
import_state {}, _set_value returns without validating type) so minimal /
package-only configs do not write an empty hostname or raise on an absent type.
"""
from __future__ import annotations
import os
import re
from typing import Any, Dict, Optional
from .composite_action import CompositeV3Action
from ..exceptions.exceptions import NetworkTypeNotFoundException

_HOSTNAME = "/etc/hostname"
_HOSTS = "/etc/hosts"


class NetworkAction(CompositeV3Action):
    """Configure hostname and hosts file declaratively (composite v3 domain)."""

    _DOMAIN = "network"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        net: Dict[str, Any] = cfg.get("network", {}) or {}
        self.type: str = net.get("type", "")
        self.hostname: str = cfg.get("hostname", "")
        self.add_default_hosts: bool = net.get("add_default_hosts", False)

    @property
    def name(self) -> str:
        return "Network Configuration"

    @property
    def is_optional(self) -> bool:
        return True

    def _declared(self) -> bool:
        return bool(self.hostname)

    # --- target-aware paths ------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _p(self, canonical: str) -> str:
        t = self._target()
        return t.path(canonical) if t is not None else "/mnt" + canonical

    def _default_block(self) -> str:
        return (
            "127.0.0.1 localhost\n"
            "::1 localhost\n"
            f"127.0.1.1 {self.hostname}\n"
        )

    def _read(self, canonical: str) -> Optional[str]:
        try:
            with open(self._p(canonical), "r") as f:
                return f.read()
        except FileNotFoundError:
            return None

    # --- composite state ---------------------------------------------- #

    def _desired_state(self) -> dict:
        return {"hostname": self.hostname, "default_hosts": bool(self.add_default_hosts)}

    def _actual_state(self) -> Optional[dict]:
        hn = self._read(_HOSTNAME)
        if hn is None:
            return None
        hosts = self._read(_HOSTS) or ""
        present = re.search(re.escape(self._default_block()), hosts) is not None
        return {"hostname": hn.strip(), "default_hosts": present}

    # --- guards over the base contract -------------------------------- #

    def plan(self, managed):
        if not self._declared():
            return []
        return super().plan(managed)

    def import_state(self, managed=None) -> dict:
        if not self._declared():
            return {}
        st = self._actual_state() or self._desired_state()
        return {
            "hostname": st["hostname"],
            "network": {"type": self.type, "add_default_hosts": st["default_hosts"]},
        }

    def _import_fragment(self, value) -> dict:  # pragma: no cover - import_state overridden
        return self.import_state()

    def _set_value(self) -> None:
        if not self._declared():
            return
        if self.type not in ("NetworkManager", "systemd-networkd"):
            raise NetworkTypeNotFoundException
        self._clear_loopback()
        with open(self._p(_HOSTNAME), "w") as f:
            f.write(self.hostname)
        if self.add_default_hosts:
            with open(self._p(_HOSTS), "a") as f:
                f.write(self._default_block())

    def _clear_loopback(self) -> None:
        path = self._p(_HOSTS)
        if not os.path.exists(path):
            return
        with open(path, "r+") as hf:
            lines = hf.readlines()
            hf.seek(0)
            for line in lines:
                if not re.match(r"^(127\.0\.0\.1|::1|127\.0\.1\.1)", line):
                    hf.write(line)
            hf.truncate()
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `pytest tests/lib/actions/test_network_action.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/network_action.py tests/lib/actions/test_network_action.py
git commit -m "feat(network): migrate to CompositeV3Action (guard, type passthrough)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Full-suite verification (reconciler + verb integration)

Now that both actions are v3, they participate in `plan`/`apply`/`sync`. This task confirms nothing regressed — especially the verb-integration idempotency test (network is `__root__`, so always built; the guard must keep it a no-op for the package-only fixtures).

**Files:**
- Possibly modify: `tests/cli/test_verbs_integration.py` (only if a new mocked command surfaces — none expected)

- [ ] **Step 1: Run the verb-integration suite**

Run: `pytest tests/cli/test_verbs_integration.py -q`
Expected: all PASS. Specifically `test_apply_is_idempotent_second_run_no_generation_2` still passes — pacman is skipped (no `pacman` config slice, no managed entry) and network's guard returns an empty plan for the hostname-less fixture, so no second generation is created.

If a failure shows an unexpected real command being shelled out, add its `(cmd, args[0]) -> b""` entry to the table in that test (network/pacman are file-only; none expected).

- [ ] **Step 2: Run the full suite**

Run: `pytest -q`
Expected: all PASS (the previous ~481 plus the new pacman/network v3 tests; legacy pacman/network tests were replaced in place).

- [ ] **Step 3: Run coverage and confirm the gate**

Run: `pytest --cov=dasik -q`
Expected: total coverage ≥ 80%. `pacman_action.py` and `network_action.py` are exercised by the new round-trip tests (`_set_value`, `_clear_loopback`, `_actual_state`, `import_state`). If `--cov` is unavailable, install dev extras first: `pip install -e .[dev]`.

- [ ] **Step 4: Commit (only if Step 1 required a test-table edit)**

```bash
git add tests/cli/test_verbs_integration.py
git commit -m "test(cli): keep verb integration green after pacman/network v3 migration

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

If no edit was needed, skip this commit.

---

## Self-review notes (spec coverage)

- Spec "PacmanAction" section → Task 1 (target-aware, bidirectional, `_import_fragment` shape, defaults). ✓
- Spec "NetworkAction" section → Task 2 (two-key import, type passthrough, type validation). ✓
- Spec "Nothing-declared guard" → Task 2 (`test_nothing_declared_guard_*`). ✓
- Spec edge "pacman section removed after apply" → behavior inherited from `build_plan` + `empty_config()`; no new code, documented in spec (not separately tested — relies on existing reconciler tests).
- Spec edge "sync bootstrap captures undeclared pacman.conf" → `import_state` reads actual; reconciler `sync_to_config` bootstraps absent slices (existing behavior, covered by reconciler tests).
- Spec edge "network nothing-declared" → Task 2 guard tests. ✓
- Spec "Testing" → Tasks 1–3. ✓
- Spec "Verification (suite green, coverage ≥80%)" → Task 3. ✓
- Method/name consistency: `_p`, `_read`, `_desired_state`, `_actual_state`, `_set_value`, `_import_fragment`, `_declared`, `_default_block`, `_clear_loopback`, `_option_active`, `_multilib_active` — used consistently across action + tests. ✓
- No model changes (PacmanModel/NetworkModel already match). ✓

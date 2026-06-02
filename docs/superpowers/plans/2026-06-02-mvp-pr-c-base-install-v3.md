# MVP PR C: base install v3 domain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Bring `BaseInstallAction` (pacstrap + genfstab) under the v3 verb pipeline so `plan`/`apply` reconcile the base-system bootstrap idempotently and target-aware.

**Architecture:** Add the v3 contract (`actual`/`plan`/`apply`/`managed_keys`/`import_state`) around the existing pacstrap/genfstab logic, which is extracted into a mockable `_install()`. Idempotent via a marker file (`<target>/usr/bin/pacman`): present ⇒ base installed ⇒ no-op. Install-only (no removal). Target-aware paths.

**Tech Stack:** Python 3.10+, pytest, `unittest.mock`. Destructive `_install()` (pacstrap/genfstab) is never run in tests.

**Spec:** `docs/superpowers/specs/2026-06-02-mvp-nixos-expansion-design.md` (slice 5).

**Safety:** `pacstrap` writes a whole base system into the target. `_install()` is asserted via mock, never executed in tests.

**Branch:** `feat-mvp-base-install-v3` (off `main`).

**Pre-flight:** `dasik/lib/actions/base_install_action.py` (current legacy form), `dasik/lib/target/target.py`, `dasik/lib/state/change.py`.

---

## Task C.1: v3 contract around pacstrap/genfstab

**Files:**
- Modify: `dasik/lib/actions/base_install_action.py`
- Test (create): `tests/lib/actions/test_base_install_action.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/actions/test_base_install_action.py`:

```python
from unittest.mock import patch

from dasik.lib.actions.base_install_action import BaseInstallAction
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Op


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


def _marker(tmp_path):
    d = tmp_path / "usr" / "bin"
    d.mkdir(parents=True, exist_ok=True)
    (d / "pacman").write_text("")


def test_is_v3_true():
    assert BaseInstallAction.is_v3() is True


def test_actual_empty_when_not_installed(tmp_path):
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    assert a.actual() == set()


def test_actual_present_when_marker(tmp_path):
    _marker(tmp_path)
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    assert a.actual() == {"base"}


def test_plan_install_when_absent(tmp_path):
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    changes = a.plan(managed=[])
    assert changes and changes[0].op is Op.INSTALL and changes[0].item == "base"


def test_plan_empty_when_present(tmp_path):
    _marker(tmp_path)
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    assert a.plan(managed=[]) == []


def test_apply_runs_install_when_changes(tmp_path):
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    with patch.object(BaseInstallAction, "_install") as inst:
        a.apply(a.plan(managed=[]))
        inst.assert_called_once()


def test_apply_noop_when_no_changes(tmp_path):
    _marker(tmp_path)
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    with patch.object(BaseInstallAction, "_install") as inst:
        a.apply(a.plan(managed=[]))
        inst.assert_not_called()


def test_managed_keys(tmp_path):
    _marker(tmp_path)
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    assert a.managed_keys() == {"base": ["base"]}


def test_import_state_empty(tmp_path):
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    assert a.import_state(managed=[]) == {}


def test_microcode_added_when_enabled():
    with patch.object(BaseInstallAction, "_detect_microcode", return_value="amd-ucode"):
        a = BaseInstallAction({"enable_microcode": True})
    assert "amd-ucode" in a.packages


def test_name_and_optional():
    a = BaseInstallAction({"enable_microcode": False})
    assert a.name == "Base Installation"
    assert a.is_optional is False
```

- [ ] **Step 2: Run, expect failure**

Run: `pytest tests/lib/actions/test_base_install_action.py -q`
Expected: failures (`is_v3` False; no `actual`/`plan`/`_install`/target-aware marker).

- [ ] **Step 3: Rewrite the action to the v3 contract**

Overwrite `dasik/lib/actions/base_install_action.py`:

```python
"""Action: pacstrap the base system into the target (v3 domain "base").

Idempotent: a no-op once the base system is pacstrapped (marker:
``<target>/usr/bin/pacman``). Install-only. Target-aware. The destructive
pacstrap/genfstab lives in ``_install()`` (mocked in tests).
"""
from __future__ import annotations
import os
from typing import Any, Dict, List
from colorama import Fore, Style, init
from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command
from ..state.change import Change, Op

_MARKER = "/usr/bin/pacman"
_DOMAIN = "base"


class BaseInstallAction(AbstractAction):
    """Install the Arch base system (base, linux, firmware, microcode)."""

    _DOMAIN = _DOMAIN

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self.enable_microcode: bool = cfg.get("enable_microcode", False)
        self.packages: List[str] = ["base", "linux", "linux-firmware"]
        init(autoreset=True)
        if self.enable_microcode:
            self.packages += [self._detect_microcode()]

    @property
    def name(self) -> str:
        return "Base Installation"

    @property
    def is_optional(self) -> bool:
        return False

    # --- target-aware paths ------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _target_root(self) -> str:
        t = self._target()
        return t.root if t is not None else "/mnt"

    def _p(self, canonical: str) -> str:
        t = self._target()
        return t.path(canonical) if t is not None else "/mnt" + canonical

    @staticmethod
    def _detect_microcode() -> str:
        with open("/proc/cpuinfo", "r") as cpuinfo:
            content = cpuinfo.read()
        if "AuthenticAMD" in content:
            return "amd-ucode"
        if "GenuineIntel" in content:
            return "intel-ucode"
        print(Fore.RED + "Unknown CPU Vendor. Exiting..." + Style.RESET_ALL)
        raise SystemExit(1)

    def _installed(self) -> bool:
        return os.path.exists(self._p(_MARKER))

    # --- v3 contract -------------------------------------------------- #

    def actual(self) -> set:
        return {"base"} if self._installed() else set()

    def managed_keys(self) -> dict:
        return {self._DOMAIN: sorted(self.actual())}

    def plan(self, managed) -> list:
        if not self._installed():
            return [Change(self._DOMAIN, Op.INSTALL, "base", reason="pacstrap")]
        return []

    def apply(self, changes) -> None:
        if changes:
            self._install()

    def import_state(self, managed=None) -> dict:
        return {}

    # --- legacy executor bridge --------------------------------------- #

    def is_needed(self) -> bool:
        return not self._installed()

    def execute(self) -> None:
        self._install()

    def verify(self) -> bool:
        return self._installed()

    # --- the destructive bit (mocked in tests) ------------------------ #

    def _install(self) -> None:  # pragma: no cover - destructive: pacstrap/genfstab
        root = self._target_root()
        Command.execute("pacman", ["--noconfirm", "-Sy", "archlinux-keyring"])
        Command.execute("pacstrap", ["-K", root] + self.packages)
        fstab = Command.execute("genfstab", ["-U", root]).stdout.decode()
        with open(self._p("/etc/fstab"), "a") as f:
            f.write(fstab)
```

- [ ] **Step 4: Run, expect pass**

Run: `pytest tests/lib/actions/test_base_install_action.py -q`
Expected: all PASS.

- [ ] **Step 5: Full suite + coverage**

Run: `pytest -q` → all PASS.
Run: `pytest --cov=dasik -q` → total ≥ 80% (`_install` is `pragma: no cover`).

- [ ] **Step 6: Commit**

```bash
git add dasik/lib/actions/base_install_action.py tests/lib/actions/test_base_install_action.py
git commit -m "feat(base): v3 domain (pacstrap/genfstab, marker idempotency, target-aware)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-review notes (spec coverage)

- Spec slice 5 "actual=base present in /mnt, plan=install when absent, apply=pacstrap+genfstab" → Task C.1. ✓
- Target-aware marker + pacstrap root (works under `--target`) → `_p` / `_target_root`. ✓
- Destructive body isolated in `_install`, mocked in tests → Steps 1/3. ✓
- Install-only (no removal); `import_state` empty (base not user-synced) → `plan`/`import_state`. ✓
- `is_optional` False (base is mandatory) → asserted. ✓

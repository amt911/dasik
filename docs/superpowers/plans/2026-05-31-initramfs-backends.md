# initramfs generator backends Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate initramfs configuration onto the v3 contract via a pluggable `InitramfsBackend` (mkinitcpio ported + dracut new), selected by a new `initramfs` config field, so `dasik plan/apply` covers it.

**Architecture:** `InitramfsAction(ScalarV3Action)` delegates its four scalar hooks to a backend chosen by `make_backend(config["initramfs"], …)`. Each backend computes a single serialized "desired config" value, the current on-disk value (or None), and an `apply()` that writes the config + regenerates. `sync` ignores the domain (`_import_fragment` → `{}`) since it is derived from the disk config.

**Tech Stack:** Python 3.10+, pydantic, pytest/pytest-cov.

Spec: `docs/superpowers/specs/2026-05-31-initramfs-backends-design.md`.

**Test runner:**
```bash
python -m venv /tmp/dasik-venv && /tmp/dasik-venv/bin/pip install -q pytest pytest-cov colorama pydantic
PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest ...
```

---

## Task 1: `initramfs` config field

**Files:**
- Modify: `dasik/lib/models/json_model.py`
- Test: `tests/lib/models/test_initramfs_field.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/lib/models/test_initramfs_field.py`:

```python
from dasik.lib.models.json_model import JsonModel


def _base(**extra):
    return JsonModel(
        locales={"selected_locales": ["en_US.UTF-8 UTF-8"],
                 "desired_locale": "en_US.UTF-8", "desired_tty_layout": "us"},
        timezone={"region": "Europe", "city": "Madrid"},
        network={"type": "NetworkManager", "add_default_hosts": True},
        hostname="arch",
        **extra,
    )


def test_initramfs_defaults_to_mkinitcpio():
    assert _base().initramfs == "mkinitcpio"


def test_initramfs_accepts_dracut():
    assert _base(initramfs="dracut").initramfs == "dracut"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/models/test_initramfs_field.py -v`
Expected: FAIL — `initramfs` not a field (AttributeError / unexpected behaviour).

- [ ] **Step 3: Add the field**

In `dasik/lib/models/json_model.py`, under the "Toggles" block (next to `enable_trim` /
`remove_home_on_delete`), add:

```python
    initramfs: str = "mkinitcpio"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/models/test_initramfs_field.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/models/json_model.py tests/lib/models/test_initramfs_field.py
git commit -m "feat(models): add initramfs generator selector field (default mkinitcpio)"
```

---

## Task 2: backend base + shared detection + `MkinitcpioBackend` + factory

**Files:**
- Create: `dasik/lib/actions/initramfs/__init__.py`
- Create: `dasik/lib/actions/initramfs/base.py`
- Create: `dasik/lib/actions/initramfs/mkinitcpio.py`
- Test: `tests/lib/actions/initramfs/test_mkinitcpio_backend.py` (create)
- Test: `tests/lib/actions/initramfs/test_factory.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/actions/initramfs/test_mkinitcpio_backend.py`:

```python
from unittest.mock import mock_open, patch

from dasik.lib.actions.initramfs.mkinitcpio import MkinitcpioBackend
from dasik.lib.target.target import Target


_DEFAULT = ("HOOKS=(base udev autodetect modconf kms keyboard keymap "
            "consolefont block filesystems fsck)\n")


def _enc_cfg(fs="ext4"):
    return {"disks": {"disks": [{"partitions": [
        {"encrypt": True, "luks_name": "cryptroot", "mountpoint": "/", "filesystem": fs}]}]}}


def _b(cfg, root="/"):
    return MkinitcpioBackend(cfg, Target(root=root))


def test_desired_moves_keyboard_before_autodetect():
    with patch("builtins.open", mock_open(read_data=_DEFAULT)):
        hooks = _b({}).desired_value().split()
    assert hooks.index("keyboard") < hooks.index("autodetect")


def test_desired_encryption_substitutions():
    with patch("builtins.open", mock_open(read_data=_DEFAULT)):
        hooks = _b(_enc_cfg()).desired_value().split()
    assert "systemd" in hooks and "udev" not in hooks
    assert "sd-vconsole" in hooks and "keymap" not in hooks
    assert "sd-encrypt" in hooks and hooks.index("sd-encrypt") == hooks.index("block") + 1
    assert "consolefont" not in hooks


def test_desired_btrfs_hook_encrypted():
    with patch("builtins.open", mock_open(read_data=_DEFAULT)):
        hooks = _b(_enc_cfg(fs="btrfs")).desired_value().split()
    assert "btrfs" in hooks and hooks.index("btrfs") == hooks.index("systemd") + 1


def test_actual_value_parses_hooks_line():
    with patch("builtins.open", mock_open(read_data=_DEFAULT)):
        assert _b({}).actual_value() == (
            "base udev autodetect modconf kms keyboard keymap "
            "consolefont block filesystems fsck")


def test_actual_value_none_when_file_absent():
    with patch("builtins.open", side_effect=FileNotFoundError):
        assert _b({}).actual_value() is None


def test_apply_rewrites_hooks_and_runs_mkinitcpio():
    a = _b(_enc_cfg(), root="/")
    m = mock_open(read_data=_DEFAULT)
    with patch("builtins.open", m), \
         patch("dasik.lib.actions.initramfs.mkinitcpio.Command.execute") as run:
        a.apply()
    body = "".join(c.args[0] for c in m().write.call_args_list)
    assert "HOOKS=(" in body and "sd-encrypt" in body
    assert ("mkinitcpio", ["-P"]) == (run.call_args.args[0], run.call_args.args[1])
    assert run.call_args.kwargs["target"].root == "/"
```

Create `tests/lib/actions/initramfs/test_factory.py`:

```python
import pytest

from dasik.lib.actions.initramfs import make_backend
from dasik.lib.actions.initramfs.mkinitcpio import MkinitcpioBackend
from dasik.lib.actions.initramfs.dracut import DracutBackend
from dasik.lib.target.target import Target


def test_make_backend_mkinitcpio():
    assert isinstance(make_backend("mkinitcpio", {}, Target(root="/")), MkinitcpioBackend)


def test_make_backend_dracut():
    assert isinstance(make_backend("dracut", {}, Target(root="/")), DracutBackend)


def test_make_backend_unknown_raises():
    with pytest.raises(ValueError):
        make_backend("booster", {}, Target(root="/"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/initramfs/ -v`
Expected: FAIL — `dasik.lib.actions.initramfs` package missing.

- [ ] **Step 3: Implement base + mkinitcpio + factory**

Create `dasik/lib/actions/initramfs/base.py`:

```python
"""Initramfs generator backend interface + shared disk-config detection."""
from __future__ import annotations
from typing import Any, Dict, Optional


def detect_encryption(cfg: Dict[str, Any]) -> bool:
    disks = cfg.get("disks", {})
    if isinstance(disks, dict):
        for disk in disks.get("disks", []):
            for part in disk.get("partitions", []):
                if part.get("encrypt", False):
                    return True
    return False


def detect_root_fs(cfg: Dict[str, Any]) -> Optional[str]:
    disks = cfg.get("disks", {})
    if isinstance(disks, dict):
        for disk in disks.get("disks", []):
            for part in disk.get("partitions", []):
                if part.get("mountpoint") == "/":
                    return part.get("filesystem")
    return None


class InitramfsBackend:
    """Compute + apply the initramfs configuration for one generator."""

    def __init__(self, config: Dict[str, Any], target=None):
        self.config = config if isinstance(config, dict) else {}
        self.target = target
        self.has_encryption = detect_encryption(self.config)
        self.root_fs = detect_root_fs(self.config)

    def _path(self, canonical: str) -> str:
        if self.target is not None:
            return self.target.path(canonical)
        return "/mnt" + canonical

    def desired_value(self) -> str:
        raise NotImplementedError

    def actual_value(self) -> Optional[str]:
        raise NotImplementedError

    def apply(self) -> None:
        raise NotImplementedError
```

Create `dasik/lib/actions/initramfs/mkinitcpio.py`:

```python
"""mkinitcpio backend: derive HOOKS from the disk config + run mkinitcpio -P."""
from __future__ import annotations
import re
from typing import List, Optional
from .base import InitramfsBackend
from ...command_worker.command_worker import Command

_CONF = "/etc/mkinitcpio.conf"
_DEFAULT_HOOKS = ["base", "udev", "autodetect", "modconf", "kms",
                  "keyboard", "keymap", "consolefont", "block",
                  "filesystems", "fsck"]


class MkinitcpioBackend(InitramfsBackend):

    def _raw_hooks(self) -> Optional[List[str]]:
        try:
            with open(self._path(_CONF), "r") as f:
                for line in f:
                    m = re.match(r"^HOOKS=\((.+)\)", line)
                    if m:
                        return m.group(1).split()
        except FileNotFoundError:
            return None
        return None

    def _compute(self, base: List[str]) -> List[str]:
        hooks = list(base)
        if "keyboard" in hooks and "autodetect" in hooks:
            hooks = [h for h in hooks if h != "keyboard"]
            hooks.insert(hooks.index("autodetect"), "keyboard")
        if self.has_encryption:
            new: List[str] = []
            for h in hooks:
                if h == "udev":
                    new.append("systemd")
                elif h == "keymap":
                    new.append("sd-vconsole")
                elif h == "block":
                    new.append(h)
                    new.append("sd-encrypt")
                elif h in ("usr", "resume", "consolefont"):
                    continue
                else:
                    new.append(h)
            hooks = new
        if self.root_fs == "btrfs" and "btrfs" not in hooks:
            if self.has_encryption:
                insert_after = "systemd"
            else:
                insert_after = next((c for c in ("resume", "usr", "udev") if c in hooks), None)
            if insert_after and insert_after in hooks:
                hooks.insert(hooks.index(insert_after) + 1, "btrfs")
            else:
                hooks.insert(1, "btrfs")
        seen: set = set()
        deduped: List[str] = []
        for h in hooks:
            if h not in seen:
                seen.add(h)
                deduped.append(h)
        return deduped

    def desired_value(self) -> str:
        base = self._raw_hooks() or _DEFAULT_HOOKS
        return " ".join(self._compute(base))

    def actual_value(self) -> Optional[str]:
        raw = self._raw_hooks()
        return " ".join(raw) if raw is not None else None

    def apply(self) -> None:
        hooks_str = self.desired_value()
        path = self._path(_CONF)
        try:
            with open(path, "r") as f:
                lines = f.readlines()
        except FileNotFoundError:
            lines = []
        with open(path, "w") as f:
            for line in lines:
                if re.match(r"^HOOKS=", line):
                    f.write(f"# {line}")
                    f.write(f"HOOKS=({hooks_str})\n")
                else:
                    f.write(line)
        if self.target is not None:
            Command.execute("mkinitcpio", ["-P"], target=self.target)
        else:
            Command.execute("mkinitcpio", ["-P"], True)
```

Create `dasik/lib/actions/initramfs/__init__.py`:

```python
"""Pluggable initramfs generator backends."""
from typing import Any, Dict
from .base import InitramfsBackend
from .mkinitcpio import MkinitcpioBackend
from .dracut import DracutBackend

_BACKENDS = {
    "mkinitcpio": MkinitcpioBackend,
    "dracut": DracutBackend,
}


def make_backend(name: str, config: Dict[str, Any], target=None) -> InitramfsBackend:
    try:
        cls = _BACKENDS[name]
    except KeyError:
        raise ValueError(
            f"unknown initramfs generator {name!r}; "
            f"known: {', '.join(sorted(_BACKENDS))}"
        )
    return cls(config, target)


__all__ = ["InitramfsBackend", "MkinitcpioBackend", "DracutBackend", "make_backend"]
```

(The `__init__` imports `DracutBackend` from Task 3; create a minimal stub now so the
package imports, then flesh it out in Task 3. Stub `dasik/lib/actions/initramfs/dracut.py`:)

```python
from .base import InitramfsBackend


class DracutBackend(InitramfsBackend):
    pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/initramfs/ -v`
Expected: PASS (mkinitcpio backend + factory; the dracut stub is enough for the factory tests).

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/initramfs/ tests/lib/actions/initramfs/
git commit -m "feat(initramfs): backend interface + MkinitcpioBackend + factory"
```

---

## Task 3: `DracutBackend`

**Files:**
- Modify: `dasik/lib/actions/initramfs/dracut.py`
- Test: `tests/lib/actions/initramfs/test_dracut_backend.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/actions/initramfs/test_dracut_backend.py`:

```python
from unittest.mock import mock_open, patch

from dasik.lib.actions.initramfs.dracut import DracutBackend
from dasik.lib.target.target import Target


def _cfg(encrypt=False, fs="ext4"):
    part = {"mountpoint": "/", "filesystem": fs}
    if encrypt:
        part["encrypt"] = True
    return {"disks": {"disks": [{"partitions": [part]}]}}


def _b(cfg, root="/"):
    return DracutBackend(cfg, Target(root=root))


def test_desired_includes_crypt_when_encrypted():
    assert "crypt" in _b(_cfg(encrypt=True)).desired_value()


def test_desired_includes_btrfs_when_btrfs_root():
    assert "btrfs" in _b(_cfg(fs="btrfs")).desired_value()


def test_desired_empty_when_nothing_to_add():
    assert _b(_cfg()).desired_value() == ""


def test_desired_is_deterministic():
    b = _b(_cfg(encrypt=True, fs="btrfs"))
    assert b.desired_value() == b.desired_value()
    assert "crypt" in b.desired_value() and "btrfs" in b.desired_value()


def test_actual_value_reads_conf():
    with patch("builtins.open", mock_open(read_data="add_dracutmodules+=\" crypt \"\n")):
        assert _b(_cfg(encrypt=True)).actual_value() == "add_dracutmodules+=\" crypt \"\n"


def test_actual_value_none_when_absent():
    with patch("builtins.open", side_effect=FileNotFoundError):
        assert _b(_cfg(encrypt=True)).actual_value() is None


def test_apply_writes_conf_and_regenerates():
    a = _b(_cfg(encrypt=True), root="/")
    m = mock_open()
    with patch("builtins.open", m), \
         patch("dasik.lib.actions.initramfs.dracut.os.makedirs"), \
         patch("dasik.lib.actions.initramfs.dracut.Command.execute") as run:
        a.apply()
    assert m.call_args_list[0].args[0] == "/etc/dracut.conf.d/dasik.conf"
    body = "".join(c.args[0] for c in m().write.call_args_list)
    assert "crypt" in body
    assert (run.call_args.args[0], run.call_args.args[1]) == (
        "dracut", ["--regenerate-all", "--force"])
    assert run.call_args.kwargs["target"].root == "/"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/initramfs/test_dracut_backend.py -v`
Expected: FAIL — `DracutBackend` is the empty stub.

- [ ] **Step 3: Implement `DracutBackend`**

Replace `dasik/lib/actions/initramfs/dracut.py`:

```python
"""dracut backend: derive /etc/dracut.conf.d/dasik.conf + run dracut."""
from __future__ import annotations
import os
from typing import List, Optional
from .base import InitramfsBackend
from ...command_worker.command_worker import Command

_CONF = "/etc/dracut.conf.d/dasik.conf"


class DracutBackend(InitramfsBackend):

    def _modules(self) -> List[str]:
        mods: List[str] = []
        if self.has_encryption:
            mods.append("crypt")
        if self.root_fs == "btrfs":
            mods.append("btrfs")
        return mods

    def desired_value(self) -> str:
        mods = self._modules()
        if not mods:
            return ""
        return f'# Managed by dasik\nadd_dracutmodules+=" {" ".join(mods)} "\n'

    def actual_value(self) -> Optional[str]:
        try:
            with open(self._path(_CONF), "r") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def apply(self) -> None:
        desired = self.desired_value()
        path = self._path(_CONF)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(desired)
        if self.target is not None:
            Command.execute("dracut", ["--regenerate-all", "--force"], target=self.target)
        else:
            Command.execute("dracut", ["--regenerate-all", "--force"], True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/initramfs/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/initramfs/dracut.py tests/lib/actions/initramfs/test_dracut_backend.py
git commit -m "feat(initramfs): DracutBackend (crypt/btrfs modules + dracut regen)"
```

---

## Task 4: `InitramfsAction` + registration swap (remove `MkinitcpioAction`)

**Files:**
- Create: `dasik/lib/actions/initramfs_action.py`
- Modify: `dasik/lib/actions/actions_handler_v2.py` (swap registration + import)
- Delete: `dasik/lib/actions/mkinitcpio_action.py`
- Delete: `tests/lib/actions/test_mkinitcpio_action.py` (logic now covered by the backend tests)
- Test: `tests/lib/actions/test_initramfs_action.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/actions/test_initramfs_action.py`:

```python
from unittest.mock import patch

from dasik.lib.actions.initramfs_action import InitramfsAction
from dasik.lib.actions.initramfs.mkinitcpio import MkinitcpioBackend
from dasik.lib.actions.initramfs.dracut import DracutBackend
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Op


def _ctx(root="/"):
    return ActionContext(target=Target(root=root))


def test_default_backend_is_mkinitcpio():
    a = InitramfsAction({}, _ctx("/"))
    assert isinstance(a._backend, MkinitcpioBackend)


def test_selects_dracut_backend():
    a = InitramfsAction({"initramfs": "dracut"}, _ctx("/"))
    assert isinstance(a._backend, DracutBackend)


def test_is_v3_true():
    assert InitramfsAction.is_v3() is True


def test_delegates_hooks_to_backend():
    a = InitramfsAction({}, _ctx("/"))
    with patch.object(a._backend, "desired_value", return_value="X"), \
         patch.object(a._backend, "actual_value", return_value="Y"):
        changes = a.plan(managed=[])
    assert [(c.op, c.item) for c in changes] == [(Op.MODIFY, "X")]


def test_plan_empty_when_converged():
    a = InitramfsAction({}, _ctx("/"))
    with patch.object(a._backend, "desired_value", return_value="SAME"), \
         patch.object(a._backend, "actual_value", return_value="SAME"):
        assert a.plan(managed=[]) == []


def test_set_value_calls_backend_apply():
    a = InitramfsAction({}, _ctx("/"))
    with patch.object(a._backend, "apply") as ap:
        a._set_value()
    ap.assert_called_once()


def test_import_fragment_is_empty():
    a = InitramfsAction({}, _ctx("/"))
    assert a._import_fragment("anything") == {}


def test_managed_keys_domain_is_initramfs():
    a = InitramfsAction({}, _ctx("/"))
    with patch.object(a._backend, "desired_value", return_value="X"):
        assert a.managed_keys() == {"initramfs": ["X"]}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_initramfs_action.py -v`
Expected: FAIL — `initramfs_action` module missing.

- [ ] **Step 3: Implement the action + swap registration + delete old files**

Create `dasik/lib/actions/initramfs_action.py`:

```python
"""Action: configure the initramfs via a pluggable generator backend.

Scalar v3 domain "initramfs": the desired config is a single derived value.
The generator (mkinitcpio | dracut | …) is chosen by the root `initramfs`
config field. Registered config_key="__root__" (reads disks + selector).
"""
from typing import Any, Dict
from .scalar_action import ScalarV3Action
from .initramfs import make_backend


class InitramfsAction(ScalarV3Action):
    """Configure + regenerate the initramfs (mkinitcpio/dracut/…)."""

    _DOMAIN = "initramfs"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        target = getattr(context, "target", None) if context else None
        self._backend = make_backend(cfg.get("initramfs", "mkinitcpio"), cfg, target)

    @property
    def name(self) -> str:
        return "Initramfs Configuration"

    @property
    def is_optional(self) -> bool:
        return True

    def _desired_value(self):
        return self._backend.desired_value() or None

    def _actual_value(self):
        return self._backend.actual_value()

    def _set_value(self) -> None:
        self._backend.apply()

    def _import_fragment(self, value) -> dict:
        return {}
```

Note: `_desired_value` returns `None` for an empty desired (e.g. dracut with no modules) so
`ScalarV3Action.plan` yields no change.

In `dasik/lib/actions/actions_handler_v2.py`:
- replace the import `from .mkinitcpio_action import MkinitcpioAction` with
  `from .initramfs_action import InitramfsAction`;
- replace the `register_action(action_class=MkinitcpioAction, config_key='__root__', …)`
  call with `action_class=InitramfsAction` (keep `config_key='__root__'`, `is_optional` as
  it was).

Delete the old files:
```bash
git rm dasik/lib/actions/mkinitcpio_action.py tests/lib/actions/test_mkinitcpio_action.py
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_initramfs_action.py tests/lib/actions/test_setup_actions.py -v`
Expected: PASS (action delegates correctly; `setup_actions` still registers cleanly with
`InitramfsAction` swapped in).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(initramfs): InitramfsAction(ScalarV3Action) replaces MkinitcpioAction"
```

---

## Task 5: Sample + full suite + gate

**Files:**
- Modify: `config/install-megamix.json` (optional: exercise `initramfs`)
- Test: full suite

- [ ] **Step 1: Add the `initramfs` selector to the sample (optional but recommended)**

In `config/install-megamix.json`, near the other toggles, add:

```jsonc
  "initramfs": "mkinitcpio",
```
(Keep the existing default behaviour; this just documents the knob.)

- [ ] **Step 2: Validate the sample parses**

Run:
```bash
PYTHONPATH=. /tmp/dasik-venv/bin/python -c "from dasik.lib.json_parser.json_parser import JsonParser; JsonParser('config/install-megamix.json').debug(); print('OK')"
```
Expected: `OK`.

- [ ] **Step 3: Full suite + coverage**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest --cov=dasik -q`
Expected: all pass; `Required test coverage of 80.0% reached`.

- [ ] **Step 4: Commit**

```bash
git add config/install-megamix.json
git commit -m "docs(config): document initramfs selector in megamix sample"
```

---

## Self-Review notes

- **Spec coverage:** Task 1 = `initramfs` field; Task 2 = backend interface + shared detection + MkinitcpioBackend + factory; Task 3 = DracutBackend; Task 4 = InitramfsAction + registration swap + MkinitcpioAction removal; Task 5 = sample + gate. All spec sections covered.
- **Type consistency:** `InitramfsBackend.desired_value/actual_value/apply`, `make_backend(name, config, target)`, `InitramfsAction._DOMAIN="initramfs"`, scalar hooks `_desired_value/_actual_value/_set_value/_import_fragment` — consistent across tasks.
- **Reconciler integration:** `InitramfsAction` registered `config_key="__root__"`; subclassing `ScalarV3Action` makes `is_v3()` True so `build_plan` includes it; `_domain_for` sees the single `initramfs` key; `import_state` → `{}` so `sync` skips it (derived domain).
- **Scalar empty-desired edge:** dracut with no crypt/btrfs → `desired_value()==""` → `_desired_value()` returns `None` → `plan` empty (no-op). Covered by `test_desired_empty_when_nothing_to_add` + the scalar base's no-desired path.
- **MkinitcpioBackend actual vs default:** `_raw_hooks()` returns `None` when the file/HOOKS line is absent → `actual_value()` is `None` (forces MODIFY), while `desired_value()` still computes off `_DEFAULT_HOOKS`. This preserves the old behaviour where a missing file means "needs configuring".

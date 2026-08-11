# Issue #173 block B — plymouth + pendrive LUKS keyfile: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the two remaining concrete features of the old imperative installer — a boot splash (`plymouth`) and LUKS unlock from a keyfile on a pendrive — as declarative dasik blocks that `plan` shows, `apply` converges idempotently and `sync` reads back.

**Architecture:** No new convergence machinery. `plymouth` rides four existing owners (an expand toggle for the package + `/etc/plymouth/plymouthd.conf`, `KernelCmdlineAction` for `splash`, the initramfs backends for the hook/module, and a capture-only `PlymouthAction` for `sync`). The keyfile gets one new set-based action (`LuksKeyfileAction`) that owns the key material, while the cmdline, the initramfs backends and `DiskPartitionAction.import_state` are fixed to emit and capture a *bootable* `rd.luks.key`.

**Tech Stack:** Python ≥3.10, pydantic v2, pytest (+ monkeypatch/`unittest.mock`), mypy, bandit, mutmut.

## Global Constraints

- TDD is mandatory for every change in `models/`, `json_parser/`, `actions/` (`is_needed`/`verify`/`plan`), `command_worker/`: red → green → refactor.
- Never run `execute()`/`apply()` against real hardware; assert intent through a mocked `Command.execute`.
- Coverage gate ≥80% (`pytest --cov=dasik`); mypy clean; bandit rc=0; `scripts/mutation.sh` clean. The pre-push hook runs all four.
- Every new top-level config field is `Optional`/defaulted in `JsonModel`.
- Every feature must be detectable by `plan` (missing ⇒ planned, present ⇒ silent, owned-but-undeclared ⇒ REMOVE) **and** capturable by `sync` (`sync` → `plan` is silent).
- Never `git add -A` in this repo — stage explicit paths (`config/mysystem.json*`, `test.json`, `test-config.json` are local scratch).
- Exact kernel-cmdline syntax (Arch wiki, dm-crypt/System_configuration#rd.luks.key):
  `rd.luks.key=<luks-uuid>=/path/to/keyfile:UUID=<fs-uuid>` and
  `rd.luks.options=<luks-uuid>=keyfile-timeout=10s`.
- Exact mkinitcpio hook rule (Arch wiki, Plymouth#mkinitcpio): `plymouth` goes **after** `systemd`/`udev` and **before** `sd-encrypt`/`encrypt`.

---

## File structure

| File | Responsibility |
| --- | --- |
| `dasik/lib/models/plymouth_model.py` *(new)* | `PlymouthModel` — the `plymouth` block's shape. |
| `dasik/lib/models/json_model.py` | wire `plymouth` in as `Optional[PlymouthModel]`. |
| `dasik/lib/models/disk_model.py` | new `Partition.unlock_keydev_fs` + its validator. |
| `dasik/lib/expand/toggles.py` | `expand_plymouth` (package + `plymouthd.conf`). |
| `dasik/lib/actions/plymouth_action.py` *(new)* | capture-only action + `plymouth_installed()` helper. |
| `dasik/lib/actions/luks_keyfile_action.py` *(new)* | own the keyfile: generate, enroll, verify. |
| `dasik/lib/actions/kernel_cmdline_action.py` | derive `splash`; fix `rd.luks.key`; derive `keyfile-timeout`; conditional `splash` subtraction on capture. |
| `dasik/lib/actions/initramfs/base.py` | `detect_plymouth`, `detect_keydev_filesystems`, `detect_embedded_keyfiles`. |
| `dasik/lib/actions/initramfs/mkinitcpio.py` | `plymouth` hook, managed `MODULES=`, managed `FILES=`. |
| `dasik/lib/actions/initramfs/dracut.py` | `plymouth` forced module, `filesystems+=`, `install_items+=`. |
| `dasik/lib/actions/disk_partition_action.py` | stop enrolling (moved to the new action); capture the keyfile back. |
| `dasik/lib/actions/actions_handler_v2.py` | register `LuksKeyfileAction` (phase 1) and `PlymouthAction` (phase 4). |
| `dasik/lib/validation/preflight.py` | keydev coherence checks. |
| `tests/lib/…` | one test module per unit + rows in both feature matrices. |
| `docs/config-reference.md`, `config/vm-dracut.json` | user-facing surface. |

---

## Task 1: the `plymouth` model

**Files:**
- Create: `dasik/lib/models/plymouth_model.py`
- Modify: `dasik/lib/models/json_model.py`
- Test: `tests/lib/models/test_plymouth_model.py`

**Interfaces:**
- Produces: `PlymouthModel(theme: Optional[str] = None)`; `JsonModel.plymouth: Optional[PlymouthModel]`.

- [ ] **Step 1: Write the failing test**

```python
"""The `plymouth` block: a boot splash, optionally themed."""
import pytest
from pydantic import ValidationError

from dasik.lib.models.plymouth_model import PlymouthModel
from dasik.lib.models.json_model import JsonModel


def test_an_empty_block_is_valid_and_leaves_the_theme_alone():
    assert PlymouthModel().theme is None


def test_the_theme_is_kept_verbatim():
    assert PlymouthModel(theme="bgrt").theme == "bgrt"


@pytest.mark.parametrize("bad", ["../../etc/passwd", "two words", "semi;colon", ""])
def test_a_theme_that_is_not_a_plain_name_is_rejected(bad):
    """The theme reaches a config file and a themes directory path."""
    with pytest.raises(ValidationError):
        PlymouthModel(theme=bad)


def test_json_model_accepts_the_block_and_defaults_to_absent():
    assert JsonModel(hostname="box").plymouth is None
    assert JsonModel(hostname="box", plymouth={"theme": "spinner"}).plymouth.theme == "spinner"
```

- [ ] **Step 2: Run it, expect ModuleNotFoundError**

Run: `pytest tests/lib/models/test_plymouth_model.py -q`

- [ ] **Step 3: Implement**

```python
"""Model for the `plymouth` block (boot splash)."""
import re
from typing import Optional
from pydantic import BaseModel, Field, field_validator

# The theme name reaches /etc/plymouth/plymouthd.conf and
# /usr/share/plymouth/themes/<name>; keep it a plain token.
_THEME_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")


class PlymouthModel(BaseModel):
    """Boot splash. Absent block = no splash at all."""
    theme: Optional[str] = Field(
        None,
        description="Plymouth theme (e.g. 'bgrt', 'spinner'). Unset leaves "
                    "plymouth's own default in place.",
    )

    @field_validator("theme")
    @classmethod
    def _validate_theme(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _THEME_RE.fullmatch(v):
            raise ValueError(
                f"Invalid plymouth theme {v!r}: must match [A-Za-z0-9_.-]{{1,64}}"
            )
        return v
```

And in `json_model.py`, next to `cpu`/`reflector`: `from .plymouth_model import PlymouthModel` plus `plymouth: Optional[PlymouthModel] = None`.

- [ ] **Step 4: Run the tests, expect PASS**

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/models/plymouth_model.py dasik/lib/models/json_model.py tests/lib/models/test_plymouth_model.py
git commit -m "feat(model): the plymouth block"
```

---

## Task 2: `expand_plymouth` — package and theme file

**Files:**
- Modify: `dasik/lib/expand/toggles.py`
- Test: `tests/lib/expand/test_expand_plymouth.py`

**Interfaces:**
- Produces: `expand_plymouth(config) -> {"packages": ["plymouth"], "files": [{"path": "/etc/plymouth/plymouthd.conf", "content": …}]}`; constant `PLYMOUTHD_CONF = "/etc/plymouth/plymouthd.conf"`.

- [ ] **Step 1: Write the failing test**

```python
from dasik.lib.expand import expand_config
from dasik.lib.expand.toggles import PLYMOUTHD_CONF, expand_plymouth


def test_no_block_contributes_nothing():
    assert expand_plymouth({}) == {}


def test_the_package_is_pulled_in():
    assert expand_plymouth({"plymouth": {}})["packages"] == ["plymouth"]


def test_no_theme_means_no_config_file():
    """An unset theme leaves plymouth's own default alone — writing the file
    with an empty Theme= would override it with nothing."""
    assert "files" not in expand_plymouth({"plymouth": {}})


def test_a_theme_becomes_the_daemon_config():
    files = expand_plymouth({"plymouth": {"theme": "bgrt"}})["files"]
    assert files == [{"path": PLYMOUTHD_CONF,
                      "content": "# Managed by dasik\n[Daemon]\nTheme=bgrt\n"}]


def test_the_toggle_is_wired_into_expand_config():
    assert "plymouth" in expand_config({"plymouth": {"theme": "bgrt"}})["packages"]
```

- [ ] **Step 2: Run it, expect ImportError**

- [ ] **Step 3: Implement** in `toggles.py` (after `expand_reflector`) and append `expand_plymouth` to `TOGGLES`

```python
PLYMOUTHD_CONF = "/etc/plymouth/plymouthd.conf"


def expand_plymouth(config: Dict[str, Any]) -> Dict[str, Any]:
    """Boot splash: the package, plus the daemon config when a theme is declared.

    The theme also has to reach the initramfs image — the wiki is explicit that
    a theme change requires regenerating it — which the initramfs backends do
    (they include the plymouth hook/module and treat this file as an input).
    """
    cfg = config.get("plymouth")
    if cfg is None:
        return {}
    out: Dict[str, Any] = {"packages": ["plymouth"]}
    theme = (cfg or {}).get("theme")
    if theme:
        out["files"] = [{"path": PLYMOUTHD_CONF,
                         "content": f"# Managed by dasik\n[Daemon]\nTheme={theme}\n"}]
    return out
```

- [ ] **Step 4: Run the tests, expect PASS**

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/expand/toggles.py tests/lib/expand/test_expand_plymouth.py
git commit -m "feat(expand): plymouth pulls its package and themes the daemon"
```

---

## Task 3: `splash` on the kernel cmdline

**Files:**
- Modify: `dasik/lib/actions/kernel_cmdline_action.py` (`_derive_from_cpu` sibling + `_derived`)
- Test: `tests/lib/actions/test_kernel_cmdline_plymouth.py`

**Interfaces:**
- Produces: `KernelCmdlineAction._derive_from_plymouth() -> List[str]`.

- [ ] **Step 1: Write the failing test**

```python
from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction


def _derived(config):
    return KernelCmdlineAction(config, None)._derive_from_plymouth()


def test_no_block_derives_nothing():
    assert _derived({}) == []


def test_the_block_derives_splash():
    assert _derived({"plymouth": {"theme": "bgrt"}}) == ["splash"]


def test_an_empty_block_still_derives_splash():
    """`"plymouth": {}` is a declaration: the splash, plymouth's default theme."""
    assert _derived({"plymouth": {}}) == ["splash"]
```

- [ ] **Step 2: Run it, expect AttributeError**

- [ ] **Step 3: Implement**

```python
    def _derive_from_plymouth(self) -> List[str]:
        """`splash` for a declared `plymouth` block.

        `quiet` is NOT derived: hiding kernel messages is the user's policy, and
        `kernel_cmdline` already spells it.
        """
        return ["splash"] if self._cfg.get("plymouth") is not None else []
```

and extend `_derived`:

```python
    def _derived(self) -> List[str]:
        """Everything dasik derives itself — never captured back by `sync`."""
        return (self._derive_from_disks() + self._derive_from_cpu()
                + self._derive_from_plymouth())
```

- [ ] **Step 4: Run the tests, expect PASS**

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/kernel_cmdline_action.py tests/lib/actions/test_kernel_cmdline_plymouth.py
git commit -m "feat(cmdline): a declared plymouth block derives splash"
```

---

## Task 4: plymouth in the initramfs (both generators)

**Files:**
- Modify: `dasik/lib/actions/initramfs/base.py`, `mkinitcpio.py`, `dracut.py`
- Test: `tests/lib/actions/initramfs/test_plymouth_initramfs.py`

**Interfaces:**
- Produces: `detect_plymouth(cfg) -> bool`; `InitramfsBackend.has_plymouth`.

- [ ] **Step 1: Write the failing test**

```python
from dasik.lib.actions.initramfs.base import detect_plymouth
from dasik.lib.actions.initramfs.mkinitcpio import MkinitcpioBackend
from dasik.lib.actions.initramfs.dracut import DracutBackend

_ENCRYPTED = {"disks": {"disks": [{"device": "/dev/vda", "partitions": [
    {"label": "root", "size": "rest", "filesystem": "ext4", "mountpoint": "/",
     "encrypt": True, "luks_name": "cryptroot"}]}]}}


def test_detect_plymouth_follows_the_block():
    assert detect_plymouth({}) is False
    assert detect_plymouth({"plymouth": {}}) is True


def test_mkinitcpio_puts_plymouth_after_udev_when_there_is_no_encryption():
    hooks = MkinitcpioBackend({"plymouth": {}}).desired_value().split()
    assert "plymouth" in hooks
    assert hooks.index("udev") < hooks.index("plymouth")


def test_mkinitcpio_puts_plymouth_after_systemd_and_before_sd_encrypt():
    """Arch wiki, Plymouth#mkinitcpio: systemd must come first, and plymouth
    must come before sd-encrypt or the passphrase prompt is swallowed."""
    hooks = MkinitcpioBackend({**_ENCRYPTED, "plymouth": {}}).desired_value().split()
    assert hooks.index("systemd") < hooks.index("plymouth") < hooks.index("sd-encrypt")


def test_mkinitcpio_without_the_block_has_no_plymouth_hook():
    assert "plymouth" not in MkinitcpioBackend({}).desired_value().split()


def test_dracut_forces_the_plymouth_module():
    """Forced, not added: dracut runs under arch-chroot, where its own
    detection already silently dropped systemd-cryptsetup and resume."""
    conf = DracutBackend({"plymouth": {}}).desired_value()
    assert "force_add_dracutmodules" in conf and "plymouth" in conf


def test_dracut_without_the_block_does_not_mention_plymouth():
    assert "plymouth" not in DracutBackend({}).desired_value()
```

- [ ] **Step 2: Run it, expect ImportError / assertion failures**

- [ ] **Step 3: Implement**

`base.py`:

```python
def detect_plymouth(cfg: Dict[str, Any]) -> bool:
    return cfg.get("plymouth") is not None
```

plus `self.has_plymouth = detect_plymouth(self.config)` in `InitramfsBackend.__init__`.

`mkinitcpio.py` — inside `_compute`, after the encryption rewrite and before the dedupe:

```python
        if self.has_plymouth and "plymouth" not in hooks:
            # Arch wiki (Plymouth#mkinitcpio): after systemd/udev — plymouth
            # needs the device manager up — and BEFORE sd-encrypt/encrypt, or
            # plymouth never takes over the passphrase prompt and an encrypted
            # machine cannot be unlocked at all.
            after = next((h for h in ("systemd", "udev", "base") if h in hooks), None)
            index = hooks.index(after) + 1 if after else 0
            for blocker in ("sd-encrypt", "encrypt"):
                if blocker in hooks:
                    index = min(index, hooks.index(blocker))
            hooks.insert(index, "plymouth")
```

`dracut.py` — in `_force_modules`, before the dedupe:

```python
        if self.has_plymouth:
            mods.append("plymouth")
```

- [ ] **Step 4: Run the tests, expect PASS. Then the whole initramfs suite:**

Run: `pytest tests/lib/actions/initramfs -q`

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/initramfs tests/lib/actions/initramfs/test_plymouth_initramfs.py
git commit -m "feat(initramfs): plymouth hook (mkinitcpio) and forced module (dracut)"
```

---

## Task 5: a theme change rebuilds the image

**Files:**
- Modify: `dasik/lib/actions/initramfs/dracut.py` (`actual_value`)
- Test: `tests/lib/actions/initramfs/test_plymouth_initramfs.py` (append)

**Interfaces:**
- Consumes: `PLYMOUTHD_CONF` from `dasik.lib.expand.toggles`.

- [ ] **Step 1: Write the failing test**

```python
import os

from dasik.lib.expand.toggles import PLYMOUTHD_CONF
from dasik.lib.target.target import Target


def _dracut_target(tmp_path, conf_body="# Managed by dasik\n"):
    (tmp_path / "etc/dracut.conf.d").mkdir(parents=True)
    (tmp_path / "etc/dracut.conf.d/dasik.conf").write_text(conf_body)
    (tmp_path / "usr/lib/modules/6.1.0").mkdir(parents=True)
    (tmp_path / "usr/lib/modules/6.1.0/pkgbase").write_text("linux\n")
    (tmp_path / "boot").mkdir()
    (tmp_path / "boot/initramfs-linux.img").write_text("image")
    return Target(root=str(tmp_path))


def test_a_theme_newer_than_the_image_forces_a_rebuild(tmp_path):
    """The wiki is explicit: every theme change needs the initramfs rebuilt.
    Without this the plan is silent and the splash keeps the old theme."""
    target = _dracut_target(tmp_path)
    backend = DracutBackend({"plymouth": {"theme": "bgrt"}}, target)
    conf = backend.desired_value()
    (tmp_path / "etc/dracut.conf.d/dasik.conf").write_text(conf)
    theme_conf = tmp_path / PLYMOUTHD_CONF.lstrip("/")
    theme_conf.parent.mkdir(parents=True, exist_ok=True)
    theme_conf.write_text("[Daemon]\nTheme=bgrt\n")
    image = tmp_path / "boot/initramfs-linux.img"
    os.utime(image, (1, 1))            # image older than every input

    assert backend.actual_value() is None
```

- [ ] **Step 2: Run it, expect PASS-by-accident or FAIL — read the failure**

If it already passes because `dasik.conf` is newer than the image, make the test honest by touching `dasik.conf` older than the image and only `plymouthd.conf` newer:

```python
    os.utime(tmp_path / "etc/dracut.conf.d/dasik.conf", (1, 1))
    os.utime(image, (2, 2))
    os.utime(theme_conf, (3, 3))
    assert backend.actual_value() is None
```

- [ ] **Step 3: Implement** — in `DracutBackend.actual_value`, pass the theme file as a second input:

```python
        inputs = [self._path(_CONF)]
        if self.has_plymouth:
            # A theme change rewrites plymouthd.conf but not dasik.conf; without
            # counting it as an input the image keeps the previous theme and the
            # plan says nothing (Arch wiki: rebuild on every theme change).
            inputs.append(self._path(_PLYMOUTHD_CONF))
        if not self._images_current(*inputs):
            return None
```

with `_PLYMOUTHD_CONF = "/etc/plymouth/plymouthd.conf"` beside `_CONF` (a literal, not an import from `expand`, to keep the backend free of expand-layer imports).

- [ ] **Step 4: Run the tests, expect PASS**

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/initramfs/dracut.py tests/lib/actions/initramfs/test_plymouth_initramfs.py
git commit -m "fix(initramfs): a plymouth theme change rebuilds the image"
```

---

## Task 6: `PlymouthAction` — capture the block back

**Files:**
- Create: `dasik/lib/actions/plymouth_action.py`
- Modify: `dasik/lib/actions/actions_handler_v2.py`, `dasik/lib/actions/kernel_cmdline_action.py`
- Test: `tests/lib/actions/test_plymouth_action.py`

**Interfaces:**
- Produces: `plymouth_installed(target) -> bool`; `PlymouthAction` with `plan() == []`, `import_state()`.
- Consumes: `KernelCmdlineAction.import_state` calls `plymouth_installed(self._target())` to decide whether `splash` is block-owned.

- [ ] **Step 1: Write the failing test**

```python
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction
from dasik.lib.actions.plymouth_action import PlymouthAction, plymouth_installed
from dasik.lib.target.target import Target


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


def _install_plymouth(root, theme=None):
    (root / "usr/bin").mkdir(parents=True, exist_ok=True)
    (root / "usr/bin/plymouthd").write_text("")
    if theme is not None:
        (root / "etc/plymouth").mkdir(parents=True, exist_ok=True)
        (root / "etc/plymouth/plymouthd.conf").write_text(f"[Daemon]\nTheme={theme}\n")


def _entry(root, options):
    (root / "boot/loader/entries").mkdir(parents=True, exist_ok=True)
    (root / "boot/loader/entries/arch.conf").write_text(f"title Arch\noptions {options}\n")
    (root / "boot/loader/loader.conf").write_text("default arch\n")


def test_nothing_is_invented_on_a_machine_without_plymouth(tmp_path):
    assert PlymouthAction({}, _ctx(tmp_path)).import_state() == {}


def test_an_installed_plymouth_is_captured(tmp_path):
    _install_plymouth(tmp_path)
    assert PlymouthAction({}, _ctx(tmp_path)).import_state() == {"plymouth": {}}


def test_the_theme_is_captured_from_the_daemon_config(tmp_path):
    _install_plymouth(tmp_path, theme="bgrt")
    assert PlymouthAction({}, _ctx(tmp_path)).import_state() == {"plymouth": {"theme": "bgrt"}}


def test_the_action_converges_nothing(tmp_path):
    """Capture-only, like CpuAction: plan() exists so sync visits it."""
    action = PlymouthAction({}, _ctx(tmp_path))
    assert action.plan(managed=[]) == []
    assert action.managed_keys() == {}


def test_splash_is_subtracted_when_plymouth_owns_it(tmp_path):
    _install_plymouth(tmp_path)
    _entry(tmp_path, "root=LABEL=root rw quiet splash")
    captured = KernelCmdlineAction({"bootloader": "sd-boot"}, _ctx(tmp_path)).import_state()
    assert captured["kernel_cmdline"] == ["root=LABEL=root", "rw", "quiet"]


def test_splash_without_plymouth_stays_a_plain_parameter(tmp_path):
    """sync reports reality: nobody owns this splash, so it is not swallowed."""
    _entry(tmp_path, "root=LABEL=root rw splash")
    captured = KernelCmdlineAction({"bootloader": "sd-boot"}, _ctx(tmp_path)).import_state()
    assert "splash" in captured["kernel_cmdline"]
```

- [ ] **Step 2: Run it, expect ModuleNotFoundError**

- [ ] **Step 3: Implement** `plymouth_action.py` (mirroring `cpu_action.py`)

```python
"""Action: capture the `plymouth` block back from the machine (v3 domain "plymouth").

Convergence is owned elsewhere — the expand toggle installs the package and
writes /etc/plymouth/plymouthd.conf, KernelCmdlineAction maintains `splash`, and
the initramfs backends put the hook/module in the image. Nothing owned the way
BACK, so a sync produced a config with a bare `splash` in `kernel_cmdline` and no
`plymouth` block: the same policy, spelled the way dasik cannot reason about.

CAPTURE-ONLY: plan() is deliberately empty (overridden so the Reconciler treats
this as a v3 action and visits it during sync); the work is in import_state.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

from .abstract_action import AbstractAction

_PLYMOUTHD = "/usr/bin/plymouthd"
_CONF = "/etc/plymouth/plymouthd.conf"
_THEME_RE = re.compile(r"^\s*Theme\s*=\s*(\S+)\s*$")


def plymouth_installed(target) -> bool:
    """Whether the target has plymouth installed.

    Probed by the daemon binary rather than `pacman -Qq plymouth`: it needs no
    chroot round trip, works on a target that is only mounted, and cannot be
    fooled by a package database that is mid-transaction.
    """
    path = target.path(_PLYMOUTHD) if target is not None else "/mnt" + _PLYMOUTHD
    return os.path.exists(path)


class PlymouthAction(AbstractAction):
    """Reconstruct the `plymouth` declaration from the live boot splash."""

    _DOMAIN = "plymouth"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        self._cfg: Dict[str, Any] = config if isinstance(config, dict) else {}

    @classmethod
    def empty_config(cls):
        return {}

    @property
    def name(self) -> str:
        return "Plymouth (boot splash)"

    @property
    def is_optional(self) -> bool:
        return True

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def plan(self, managed: Any) -> list:
        return []

    def managed_keys(self) -> dict:
        return {}

    def _theme(self) -> Optional[str]:
        target = self._target()
        path = target.path(_CONF) if target is not None else "/mnt" + _CONF
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    match = _THEME_RE.match(line)
                    if match:
                        return match.group(1)
        except OSError:
            pass
        return None

    def import_state(self, managed=None) -> dict:
        if not plymouth_installed(self._target()):
            return {}
        theme = self._theme()
        return {self._DOMAIN: {"theme": theme} if theme else {}}

    def is_needed(self) -> bool:
        return False

    def execute(self) -> None:
        return None
```

In `kernel_cmdline_action.py`'s `import_state`, keep `splash` out of the captured list only when plymouth owns it:

```python
        from .plymouth_action import plymouth_installed
        owned = list(_BLOCK_OWNED_PARAMS)
        if plymouth_installed(self._target()):
            # `splash` belongs to the plymouth block only when plymouth is
            # actually installed. Elsewhere it is somebody else's parameter and
            # dropping it would silently change the machine on re-apply.
            owned.append("splash")
```

and use `owned` where `_BLOCK_OWNED_PARAMS` was used. Register in `actions_handler_v2.setup_actions()` next to `CpuAction`/`ReflectorAction` (phase 4, `config_key='__root__'`, `is_optional=True`).

- [ ] **Step 4: Run the tests, expect PASS**

Run: `pytest tests/lib/actions/test_plymouth_action.py tests/lib/actions/test_kernel_cmdline_sync.py -q`

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/plymouth_action.py dasik/lib/actions/kernel_cmdline_action.py dasik/lib/actions/actions_handler_v2.py tests/lib/actions/test_plymouth_action.py
git commit -m "feat(sync): capture the plymouth block back from the machine"
```

---

## Task 7: plymouth rows in both feature matrices + docs

**Files:**
- Modify: `tests/lib/test_feature_detectability.py`, `tests/lib/test_feature_sync_capture.py`, `docs/config-reference.md`, `config/vm-dracut.json`

- [ ] **Step 1: Write the failing tests** (detectability)

```python
# --- plymouth -------------------------------------------------------------- #

def test_splash_missing_from_the_entry_is_planned(tmp_path):
    assert _cmdline_plan(tmp_path, {"plymouth": {}}, "root=LABEL=root rw") == [
        ("INSTALL", "splash")]


def test_splash_already_on_the_entry_plans_nothing(tmp_path):
    assert _cmdline_plan(tmp_path, {"plymouth": {}}, "root=LABEL=root rw splash") == []


def test_dropping_the_plymouth_block_removes_splash(tmp_path):
    assert _cmdline_plan(tmp_path, {}, "root=LABEL=root rw splash",
                         managed=["splash"]) == [("REMOVE", "splash")]


def test_the_plymouth_package_is_planned():
    assert "plymouth" in expand_config({"plymouth": {}})["packages"]


def test_the_plymouth_theme_file_is_planned(tmp_path):
    config = expand_config({"plymouth": {"theme": "bgrt"}})
    action = DropFilesAction(config, _ctx(tmp_path))
    assert "/etc/plymouth/plymouthd.conf" in [c.item for c in action.plan(managed=[])]
```

and (sync capture), following the existing file's helpers:

```python
# --- plymouth -------------------------------------------------------------- #

def test_a_machine_with_plymouth_captures_the_block(tmp_path):
    _install_plymouth(tmp_path, theme="bgrt")
    assert PlymouthAction({}, _ctx(tmp_path)).import_state() == {"plymouth": {"theme": "bgrt"}}


def test_a_machine_without_plymouth_invents_nothing(tmp_path):
    assert PlymouthAction({}, _ctx(tmp_path)).import_state() == {}


def test_the_captured_plymouth_config_replans_to_nothing(tmp_path):
    """The real invariant: sync -> plan must be silent."""
    _install_plymouth(tmp_path, theme="bgrt")
    _entry(tmp_path, "root=LABEL=root rw splash")
    captured = {"bootloader": "sd-boot",
                **PlymouthAction({}, _ctx(tmp_path)).import_state(),
                **KernelCmdlineAction({"bootloader": "sd-boot"}, _ctx(tmp_path)).import_state()}
    expanded = expand_config(captured)
    assert KernelCmdlineAction(expanded, _ctx(tmp_path)).plan(managed=["splash"]) == []
```

- [ ] **Step 2: Run them, expect failures for anything not yet wired**

Run: `pytest tests/lib/test_feature_detectability.py tests/lib/test_feature_sync_capture.py -q`

- [ ] **Step 3: Fix whatever the matrix exposes**, then document:
  - `docs/config-reference.md`: a `plymouth` section (fields, what it derives, what `sync` captures) in the same shape as `cpu`/`reflector`.
  - `config/vm-dracut.json`: add `"plymouth": {"theme": "bgrt"}` so the sample exercises it.

- [ ] **Step 4: Verify the samples still parse**

Run: `python -m dasik check config/vm-dracut.json`
Expected: rc 0.

- [ ] **Step 5: Commit**

```bash
git add tests/lib/test_feature_detectability.py tests/lib/test_feature_sync_capture.py docs/config-reference.md config/vm-dracut.json
git commit -m "test(plymouth): pin plan-visibility and sync round-trip"
```

---

## Task 8: `unlock_keydev_fs` + preflight coherence

**Files:**
- Modify: `dasik/lib/models/disk_model.py`, `dasik/lib/validation/preflight.py`
- Test: `tests/lib/models/test_disk_model_keyfile.py`, `tests/lib/validation/test_preflight_keyfile.py`

**Interfaces:**
- Produces: `Partition.unlock_keydev_fs: Optional[str]` restricted to `{"vfat", "exfat", "ext4", "btrfs", "xfs"}`.

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from pydantic import ValidationError

from dasik.lib.models.disk_model import Partition


def _part(**kw):
    base = dict(label="root", size="rest", filesystem="ext4", mountpoint="/",
                encrypt=True, luks_name="cryptroot")
    base.update(kw)
    return Partition(**base)


def test_the_key_device_filesystem_defaults_to_unset():
    assert _part().unlock_keydev_fs is None


def test_a_supported_key_device_filesystem_is_accepted():
    assert _part(unlock_keydev_fs="vfat").unlock_keydev_fs == "vfat"


def test_an_unknown_key_device_filesystem_is_rejected():
    """The value becomes a kernel module name in the initramfs."""
    with pytest.raises(ValidationError):
        _part(unlock_keydev_fs="reiserfs4; rm -rf /")
```

```python
from dasik.lib.validation.preflight import preflight


def _cfg(**part_kw):
    part = dict(label="root", size="rest", filesystem="ext4", mountpoint="/",
                encrypt=True, luks_name="cryptroot")
    part.update(part_kw)
    return {"disks": {"disks": [{"device": "/dev/vda", "partitions": [part]}]}}


def test_a_key_device_without_a_keyfile_is_an_error():
    result = preflight(_cfg(unlock_keydev="1234-ABCD"))
    assert any("unlock_keyfile" in e for e in result.errors)


def test_a_key_device_without_its_filesystem_warns():
    """The initramfs needs the module or it cannot read the pendrive at all."""
    result = preflight(_cfg(unlock_keyfile="/keyfile", unlock_keydev="1234-ABCD"))
    assert any("unlock_keydev_fs" in w for w in result.warnings)


def test_a_fully_declared_pendrive_unlock_is_quiet():
    result = preflight(_cfg(unlock_keyfile="/keyfile", unlock_keydev="1234-ABCD",
                            unlock_keydev_fs="vfat"))
    assert not result.errors
    assert not any("unlock_keydev" in w for w in result.warnings)
```

- [ ] **Step 2: Run them, expect failures**

Note: read `dasik/lib/validation/preflight.py` for the real `preflight()` return shape (`errors`/`warnings` attribute names) and adapt the asserts before implementing — do not guess.

- [ ] **Step 3: Implement**

In `disk_model.py`, beside `unlock_keydev`:

```python
    unlock_keydev_fs: Optional[str] = Field(
        None,
        description="Filesystem of unlock_keydev (vfat, exfat, ext4, btrfs, "
                    "xfs). The initramfs needs this module to read the key "
                    "device at boot (Arch wiki, dm-crypt/System configuration).",
    )
```

with a `field_validator` restricting it to `_KEYDEV_FILESYSTEMS = {"vfat", "exfat", "ext4", "btrfs", "xfs"}` (the value becomes a kernel module name).

In `preflight.py`, a new check function walking `disks.disks[].partitions[]`, appended to the checks the module already runs.

- [ ] **Step 4: Run the tests, expect PASS**

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/models/disk_model.py dasik/lib/validation/preflight.py tests/lib/models/test_disk_model_keyfile.py tests/lib/validation/test_preflight_keyfile.py
git commit -m "feat(disks): declare the key device filesystem, and check the block coheres"
```

---

## Task 9: a bootable `rd.luks.key` (normalization + timeout)

**Files:**
- Modify: `dasik/lib/actions/kernel_cmdline_action.py` (`_derive_from_disks`)
- Test: `tests/lib/actions/test_luks_unlock_keyfile.py` (extend)

**Interfaces:**
- Produces: `KernelCmdlineAction._keydev_spec(value: str) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_bare_key_device_uuid_becomes_a_uuid_spec():
    """Arch wiki: rd.luks.key=<luks-uuid>=/path:UUID=<fs-uuid>. Emitting the
    bare UUID gives systemd-cryptsetup something it cannot resolve, so the
    machine waits forever for a key device it will never find."""
    params = _derive({"unlock_keyfile": "/kf", "unlock_keydev": "1234-ABCD"})
    assert any(p.endswith("=/kf:UUID=1234-ABCD") for p in params)


def test_an_explicit_device_spec_is_passed_through():
    params = _derive({"unlock_keyfile": "/kf", "unlock_keydev": "LABEL=pen"})
    assert any(p.endswith("=/kf:LABEL=pen") for p in params)


def test_a_key_device_unlock_gets_a_keyfile_timeout():
    """Same page: without keyfile-timeout the boot does NOT fall back to the
    passphrase prompt when the pendrive is absent — it hangs."""
    params = _derive({"unlock_keyfile": "/kf", "unlock_keydev": "1234-ABCD"})
    assert any("keyfile-timeout=10s" in p for p in params if p.startswith("rd.luks.options="))


def test_an_explicit_keyfile_timeout_wins():
    params = _derive({"unlock_keyfile": "/kf", "unlock_keydev": "1234-ABCD",
                      "luks_options": ["keyfile-timeout=30s"]})
    options = [p for p in params if p.startswith("rd.luks.options=")]
    assert "keyfile-timeout=30s" in options[0] and "10s" not in options[0]


def test_an_embedded_keyfile_gets_no_timeout():
    """No key device to wait for: the file is inside the initramfs."""
    params = _derive({"unlock_keyfile": "/etc/keyfile"})
    assert not any("keyfile-timeout" in p for p in params)
```

`_derive` is a local helper that builds a one-partition encrypted config and returns `KernelCmdlineAction(cfg, None)._derive_from_disks()`; write it in the same file, mirroring the existing tests there.

- [ ] **Step 2: Run them, expect failures**

- [ ] **Step 3: Implement** in `_derive_from_disks`, replacing the current keyfile branch:

```python
                    keyfile = part.get("unlock_keyfile")
                    keydev = part.get("unlock_keydev")
                    if keyfile:
                        # rd.luks.key=<luks-uuid>=/path[:<keydev spec>]. A bare
                        # UUID is normalized to UUID=<uuid>: that is the form the
                        # kernel documents, and the raw value resolves to nothing.
                        key = f"{keyfile}:{self._keydev_spec(keydev)}" if keydev else keyfile
                        params.append(f"rd.luks.key={uuid}={key}")
                    opts = []
                    if part.get("unlock_tpm2"):
                        opts.append("tpm2-device=auto")
                    if part.get("unlock_fido2"):
                        opts.append("fido2-device=auto")
                    opts.extend(part.get("luks_options", []) or [])
                    # A keyfile on ANOTHER device does not fall back to the
                    # passphrase prompt on its own: without keyfile-timeout a
                    # boot with the pendrive unplugged hangs forever. The user's
                    # own value (in luks_options) wins.
                    if keyfile and keydev and not any(
                            o.startswith("keyfile-timeout=") for o in opts):
                        opts.append("keyfile-timeout=10s")
```

plus:

```python
    @staticmethod
    def _keydev_spec(value: str) -> str:
        """Normalize `unlock_keydev` to a device spec the kernel understands."""
        value = str(value).strip()
        return value if "=" in value else f"UUID={value}"
```

- [ ] **Step 4: Run the tests, expect PASS. Then the whole cmdline suite:**

Run: `pytest tests/lib/actions -k cmdline -q`

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/kernel_cmdline_action.py tests/lib/actions/test_luks_unlock_keyfile.py
git commit -m "fix(cmdline): a pendrive keyfile the kernel can actually resolve"
```

---

## Task 10: the key device's filesystem (and the embedded keyfile) in the initramfs

**Files:**
- Modify: `dasik/lib/actions/initramfs/base.py`, `mkinitcpio.py`, `dracut.py`
- Test: `tests/lib/actions/initramfs/test_keyfile_initramfs.py`

**Interfaces:**
- Produces: `detect_keydev_filesystems(cfg) -> List[str]` (sorted, deduped), `detect_embedded_keyfiles(cfg) -> List[str]`; `InitramfsBackend.keydev_filesystems`, `.embedded_keyfiles`; `MkinitcpioBackend.desired_value()` now returns the full managed block (`HOOKS`, plus `MODULES`/`FILES` when non-empty).

- [ ] **Step 1: Write the failing tests**

```python
_PEN = {"disks": {"disks": [{"device": "/dev/vda", "partitions": [
    {"label": "root", "size": "rest", "filesystem": "btrfs", "mountpoint": "/",
     "encrypt": True, "luks_name": "cryptroot",
     "unlock_keyfile": "/keyfile", "unlock_keydev": "1234-ABCD",
     "unlock_keydev_fs": "vfat"}]}]}}

_EMBEDDED = {"disks": {"disks": [{"device": "/dev/vda", "partitions": [
    {"label": "root", "size": "rest", "filesystem": "ext4", "mountpoint": "/",
     "encrypt": True, "luks_name": "cryptroot",
     "unlock_keyfile": "/etc/keyfile"}]}]}}


def test_the_key_device_filesystem_is_detected():
    assert detect_keydev_filesystems(_PEN) == ["vfat"]
    assert detect_keydev_filesystems({}) == []


def test_only_a_keyfile_without_a_key_device_counts_as_embedded():
    assert detect_embedded_keyfiles(_EMBEDDED) == ["/etc/keyfile"]
    assert detect_embedded_keyfiles(_PEN) == []


def test_mkinitcpio_declares_the_module_for_the_key_device(tmp_path):
    """Arch wiki: if the key device's filesystem differs from root's, its module
    must be in the initramfs — otherwise the pendrive is unreadable at boot."""
    value = MkinitcpioBackend(_PEN).desired_value()
    assert "MODULES=(vfat)" in value


def test_mkinitcpio_embeds_a_keyfile_that_has_no_key_device():
    value = MkinitcpioBackend(_EMBEDDED).desired_value()
    assert "FILES=(/etc/keyfile)" in value


def test_mkinitcpio_without_a_keyfile_declares_neither():
    value = MkinitcpioBackend({}).desired_value()
    assert "MODULES=" not in value and "FILES=" not in value


def test_dracut_declares_the_key_device_filesystem():
    conf = DracutBackend(_PEN).desired_value()
    assert 'filesystems+=" vfat "' in conf


def test_dracut_installs_an_embedded_keyfile():
    conf = DracutBackend(_EMBEDDED).desired_value()
    assert 'install_items+=" /etc/keyfile "' in conf
```

- [ ] **Step 2: Run them, expect failures**

- [ ] **Step 3: Implement**

`base.py`:

```python
def detect_keydev_filesystems(cfg: Dict[str, Any]) -> "list[str]":
    """Filesystems of the key devices declared by any encrypted partition.

    The initramfs must carry these modules or it cannot read the keyfile at
    boot (Arch wiki, dm-crypt/System configuration#rd.luks.key)."""
    found: "list[str]" = []
    disks = cfg.get("disks", {})
    if isinstance(disks, dict):
        for disk in disks.get("disks", []):
            for part in disk.get("partitions", []):
                fs = part.get("unlock_keydev_fs")
                if part.get("unlock_keyfile") and part.get("unlock_keydev") and fs:
                    if fs not in found:
                        found.append(fs)
    return sorted(found)


def detect_embedded_keyfiles(cfg: Dict[str, Any]) -> "list[str]":
    """Keyfiles that live inside the target root, so they must be baked into the
    image: `unlock_keyfile` with no `unlock_keydev`."""
    found: "list[str]" = []
    disks = cfg.get("disks", {})
    if isinstance(disks, dict):
        for disk in disks.get("disks", []):
            for part in disk.get("partitions", []):
                kf = part.get("unlock_keyfile")
                if kf and not part.get("unlock_keydev") and kf not in found:
                    found.append(kf)
    return found
```

plus the two attributes in `InitramfsBackend.__init__`.

`mkinitcpio.py` — `desired_value()` becomes a multi-line managed block, and `apply()` rewrites each managed line the same way it already rewrites `HOOKS=` (comment the old line out, write the new one). Keep `actual_value()` symmetric: read `HOOKS=`, `MODULES=` and `FILES=` from the file and render them the same way, so an unchanged config compares equal.

```python
    def _managed_lines(self) -> "list[str]":
        lines = [f"HOOKS=({' '.join(self._compute(self._raw_hooks() or _DEFAULT_HOOKS))})"]
        modules = self._modules()
        if modules:
            lines.append(f"MODULES=({' '.join(modules)})")
        if self.embedded_keyfiles:
            lines.append(f"FILES=({' '.join(self.embedded_keyfiles)})")
        return lines

    def desired_value(self) -> str:
        return "\n".join(self._managed_lines())
```

where `_modules()` merges the file's existing `MODULES=` entries with `self.keydev_filesystems` (order-preserving dedupe) so a user's own module list is never dropped.

`dracut.py` — in `desired_value()`, after the module lines:

```python
        for fs in self.keydev_filesystems:
            lines.append(f'filesystems+=" {fs} "')
        for keyfile in self.embedded_keyfiles:
            # No key device: the file must travel INSIDE the image, or the
            # rd.luks.key we write points at a path the initramfs cannot see.
            lines.append(f'install_items+=" {keyfile} "')
```

and include them in the `if not add_mods and not force_mods: return ""` early-out condition.

- [ ] **Step 4: Run the tests, expect PASS. Then the full initramfs + reconciler suites:**

Run: `pytest tests/lib/actions/initramfs tests/lib/reconciler -q`

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/initramfs tests/lib/actions/initramfs/test_keyfile_initramfs.py
git commit -m "feat(initramfs): carry the key device's module and the embedded keyfile"
```

---

## Task 11: `LuksKeyfileAction` — own the key material

**Files:**
- Create: `dasik/lib/actions/luks_keyfile_action.py`
- Modify: `dasik/lib/actions/actions_handler_v2.py`, `dasik/lib/actions/disk_partition_action.py`
- Test: `tests/lib/actions/test_luks_keyfile_action.py`

**Interfaces:**
- Produces: `LuksKeyfileAction` with `_DOMAIN = "luks_keyfile"`, `plan(managed) -> List[Change]`, `apply(changes)`, `managed_keys()`, item key `f"{luks_name}:{path}"`.

- [ ] **Step 1: Write the failing tests**

```python
from unittest.mock import MagicMock, patch

from dasik.lib.actions.luks_keyfile_action import LuksKeyfileAction

_CFG = {"disks": {"disks": [{"device": "/dev/vda", "partitions": [
    {"label": "root", "size": "rest", "filesystem": "ext4", "mountpoint": "/",
     "encrypt": True, "luks_name": "cryptroot", "luks_password": "hunter2",
     "unlock_keyfile": "/keyfile", "unlock_keydev": "1234-ABCD",
     "unlock_keydev_fs": "vfat"}]}]}}


def _action(cfg=_CFG):
    return LuksKeyfileAction(cfg, None)


def test_no_keyfile_declared_plans_nothing():
    assert _action({"disks": {"disks": []}}).plan(managed=[]) == []


def test_a_keyfile_that_does_not_unlock_yet_is_planned():
    with patch.object(LuksKeyfileAction, "_key_works", return_value=False):
        planned = [(c.op.name, c.item) for c in _action().plan(managed=[])]
    assert planned == [("INSTALL", "cryptroot:/keyfile")]


def test_an_enrolled_keyfile_plans_nothing():
    """Idempotency: `cryptsetup open --test-passphrase` already succeeds."""
    with patch.object(LuksKeyfileAction, "_key_works", return_value=True):
        assert _action().plan(managed=[]) == []


def test_the_domain_is_owned_so_sync_can_reason_about_it():
    assert _action().managed_keys() == {"luks_keyfile": ["cryptroot:/keyfile"]}


def test_apply_generates_the_keyfile_and_enrolls_it():
    action = _action()
    with patch("dasik.lib.actions.luks_keyfile_action.Command.execute") as run, \
         patch.object(LuksKeyfileAction, "_mounted_keydev") as mount, \
         patch("os.path.exists", return_value=False):
        mount.return_value.__enter__.return_value = "/run/dasik-key"
        action.apply(action.plan(managed=[]))
    commands = [c.args[0] for c in run.call_args_list]
    assert "dd" in commands and "cryptsetup" in commands
    add_key = next(c for c in run.call_args_list
                   if c.args[0] == "cryptsetup" and c.args[1][0] == "luksAddKey")
    assert add_key.args[1][-1] == "/run/dasik-key/keyfile"
    assert add_key.kwargs["input"] == b"hunter2"


def test_apply_does_not_regenerate_an_existing_keyfile():
    action = _action()
    with patch("dasik.lib.actions.luks_keyfile_action.Command.execute") as run, \
         patch.object(LuksKeyfileAction, "_mounted_keydev") as mount, \
         patch("os.path.exists", return_value=True):
        mount.return_value.__enter__.return_value = "/run/dasik-key"
        action.apply(action.plan(managed=[]))
    assert "dd" not in [c.args[0] for c in run.call_args_list]


def test_enrollment_without_an_existing_key_is_refused():
    """luksAddKey needs an existing key; prompting is not an option in an
    unattended installer, so fail loudly instead of hanging."""
    cfg = {"disks": {"disks": [{"device": "/dev/vda", "partitions": [
        {"label": "root", "size": "rest", "filesystem": "ext4", "mountpoint": "/",
         "encrypt": True, "luks_name": "cryptroot",
         "unlock_keyfile": "/keyfile", "unlock_keydev": "1234-ABCD"}]}]}}
    action = LuksKeyfileAction(cfg, None)
    with patch.object(LuksKeyfileAction, "_mounted_keydev"), \
         pytest.raises(CommandExecutionError):
        action.apply([Change("luks_keyfile", Op.INSTALL, "cryptroot:/keyfile")])
```

- [ ] **Step 2: Run them, expect ModuleNotFoundError**

- [ ] **Step 3: Implement** the action. Shape (adapt names to what the tests above pin):

  - `_partitions()` — every encrypted partition declaring `unlock_keyfile`, as `(part, luks_name, path)`.
  - `_device(part)` — the backing block device: `cryptsetup status <luks_name>` (reuse `KernelCmdlineAction._luks_backing_device`'s approach), falling back to the declared disk device + index.
  - `_key_works(device, local_path)` — `cryptsetup open --test-passphrase --key-file <local> <device>`, returncode 0. Any probe failure ⇒ `False` (plan the enrollment; apply fails loudly rather than a machine silently missing its declared unlock).
  - `_mounted_keydev(part)` — a `contextlib.contextmanager` that `mount`s `/dev/disk/by-uuid/<uuid>` (or the explicit spec) on a temp dir and always unmounts; a no-op yielding the target root when there is no key device.
  - `apply(changes)` — inside the mount: `dd bs=512 count=4 if=/dev/random of=<local> iflag=fullblock` + `chmod 600` when the file is missing, then `cryptsetup luksAddKey` authorised by `luks_password` (over stdin, `--key-file -`) or `luks_keyfile`; raise `CommandExecutionError` when neither exists.
  - `import_state()` — `{}`; the capture belongs to the partition (Task 12).
  - `is_needed`/`execute`/`verify` bridges exactly like `PacmanHooksAction`.

  Then register it in `setup_actions()` in phase 1, immediately after `DiskPartitionAction` (the volumes are open by then), and **delete** the `if partition.unlock_keyfile: self._add_unlock_keyfile(...)` call plus the now-unused `_add_unlock_keyfile` from `disk_partition_action.py`, so there is exactly one owner. Update the tests in `tests/lib/actions/test_luks_unlock_keyfile.py` that asserted the old call site.

- [ ] **Step 4: Run the tests, expect PASS**

Run: `pytest tests/lib/actions/test_luks_keyfile_action.py tests/lib/actions/test_luks_unlock_keyfile.py tests/lib/actions/test_disk_partition_action.py -q`

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/luks_keyfile_action.py dasik/lib/actions/actions_handler_v2.py dasik/lib/actions/disk_partition_action.py tests/lib/actions/test_luks_keyfile_action.py tests/lib/actions/test_luks_unlock_keyfile.py
git commit -m "feat(luks): one owner for the unlock keyfile, idempotent by test-passphrase"
```

---

## Task 12: `sync` captures the pendrive unlock

**Files:**
- Modify: `dasik/lib/actions/disk_partition_action.py` (`import_state`, both paths; `_read_luks_options`)
- Test: `tests/lib/actions/test_disk_partition_sync_keyfile.py`

**Interfaces:**
- Produces: `DiskPartitionAction._read_luks_keyfile(uuid) -> tuple[Optional[str], Optional[str]]` (path, keydev spec) parsed from the live cmdline; `_keydev_filesystem(spec) -> Optional[str]` via `lsblk`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_live_pendrive_unlock_is_captured(monkeypatch, tmp_path):
    """rd.luks.key=<uuid>=/keyfile:UUID=1234-ABCD must come back as the three
    fields that produced it — else sync is a one-way street and re-applying the
    captured config silently drops the pendrive unlock."""
    ...
    assert part["unlock_keyfile"] == "/keyfile"
    assert part["unlock_keydev"] == "UUID=1234-ABCD"
    assert part["unlock_keydev_fs"] == "vfat"


def test_a_machine_without_a_keyfile_invents_nothing(monkeypatch, tmp_path):
    assert "unlock_keyfile" not in part


def test_the_derived_keyfile_timeout_is_not_captured_as_a_luks_option(monkeypatch):
    """dasik re-derives keyfile-timeout for a keydev unlock; capturing it too
    would spell the same policy twice."""
    assert part.get("luks_options", []) == []


def test_an_explicit_non_default_timeout_is_kept(monkeypatch):
    """30s is the user's, not dasik's default — dropping it would change the
    machine on the next apply."""
    assert part["luks_options"] == ["keyfile-timeout=30s"]
```

Fill the bodies following the existing `tests/lib/actions/test_disk_partition_sync*.py` fixtures (they already stub `Command.execute` for `lsblk`/`cryptsetup` and the boot entry).

- [ ] **Step 2: Run them, expect failures**

- [ ] **Step 3: Implement** — in both `import_state` paths (the declared-config reflection near the `fido2`/`tpm2` block, and `_discovered_partition`), after the existing token capture:

```python
                    path, keydev = self._read_luks_keyfile(uuid)
                    if path:
                        p["unlock_keyfile"] = path
                        if keydev:
                            p["unlock_keydev"] = keydev
                            fs = self._keydev_filesystem(keydev)
                            if fs:
                                p["unlock_keydev_fs"] = fs
```

`_read_luks_keyfile` reuses `KernelCmdlineAction(...).live_params()` (as `CpuAction` does), matches `rd.luks.key=<uuid>=<rest>` for this volume's UUID and splits `rest` on the last `:` when the tail looks like a device spec (`UUID=`/`PARTUUID=`/`LABEL=`/`/dev/`).

In `_read_luks_options`, extend the subtracted set: drop `keyfile-timeout=10s` when a keyfile with a key device was captured for that UUID (dasik re-derives exactly that token); keep any other value.

- [ ] **Step 4: Run the tests, expect PASS**

Run: `pytest tests/lib/actions -k "sync or keyfile" -q`

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/disk_partition_action.py tests/lib/actions/test_disk_partition_sync_keyfile.py
git commit -m "feat(sync): capture the pendrive unlock back into the partition"
```

---

## Task 13: keyfile rows in both matrices, sample config, docs

**Files:**
- Modify: `tests/lib/test_feature_detectability.py`, `tests/lib/test_feature_sync_capture.py`, `docs/config-reference.md`, `config/vm-luks-keyfile.json` *(new)*

- [ ] **Step 1: Write the failing tests** — the same three-way matrix as every other feature:

```python
# --- pendrive LUKS keyfile ------------------------------------------------- #

def test_the_pendrive_unlock_is_planned_on_the_cmdline(tmp_path):
    planned = _cmdline_plan(tmp_path, _PENDRIVE_CFG, "root=/dev/mapper/cryptroot rw")
    assert any(item.startswith("rd.luks.key=") for _op, item in planned)
    assert any("keyfile-timeout=10s" in item for _op, item in planned)


def test_the_pendrive_unlock_already_present_plans_nothing(tmp_path):
    ...  # entry carrying rd.luks.name/rd.luks.key/rd.luks.options -> []


def test_dropping_the_pendrive_unlock_removes_the_parameter(tmp_path):
    ...  # managed=[the rd.luks.key token] -> [("REMOVE", …)]


def test_the_keyfile_enrollment_is_planned(tmp_path):
    with patch.object(LuksKeyfileAction, "_key_works", return_value=False):
        assert [c.item for c in LuksKeyfileAction(_PENDRIVE_CFG, None).plan(managed=[])] \
            == ["cryptroot:/keyfile"]
```

and in the sync matrix, the invariant that matters:

```python
def test_the_captured_pendrive_config_replans_to_nothing(tmp_path):
    captured = expand_config(<the config sync produced>)
    assert KernelCmdlineAction(captured, _ctx(tmp_path)).plan(managed=<owned>) == []
```

- [ ] **Step 2: Run them, expect failures for anything not yet wired**

- [ ] **Step 3: Make them pass**, then add:
  - `config/vm-luks-keyfile.json` — an encrypted VM config with `unlock_keyfile` / `unlock_keydev` / `unlock_keydev_fs` and destructive flags set the way the other `vm-*.json` samples set them.
  - `docs/config-reference.md` — the three partition fields, the two path semantics (relative to the key device vs embedded in the initramfs), the derived `keyfile-timeout=10s`, and the documented asymmetry: **un-declaring the keyfile removes the kernel parameter but never runs `luksKillSlot`** — dasik will not destroy access to a volume; it prints the keyslot it is leaving behind.

- [ ] **Step 4: Verify the samples parse and plan**

Run: `python -m dasik check config/vm-luks-keyfile.json && python -m dasik plan config/vm-luks-keyfile.json`
Expected: `check` rc 0; `plan` either prints the plan or fails with `CommandNotFoundException` off Arch hardware (expected, documented in CLAUDE.md).

- [ ] **Step 5: Commit**

```bash
git add tests/lib/test_feature_detectability.py tests/lib/test_feature_sync_capture.py docs/config-reference.md config/vm-luks-keyfile.json
git commit -m "test(luks): pin plan-visibility and sync round-trip for the pendrive unlock"
```

---

## Task 14: gates, agentic verification, PR

**Files:** none (verification only), plus `docs/config-reference.md` fixes if the smoke run exposes drift.

- [ ] **Step 1: Run the four gates**

```bash
pytest --cov=dasik -q
mypy dasik
bandit -r dasik -q
scripts/mutation.sh
```
Expected: all green, coverage ≥80%. Fix whatever fails — a surviving mutant in the new `plan()` logic means a missing assert, not a mutmut problem.

- [ ] **Step 2: Smoke the CLI end to end**

```bash
python -m dasik --help
python -m dasik check config/vm-luks-keyfile.json
python -m dasik plan config/install-megamix.json
```

- [ ] **Step 3: VM verification (best effort)** — `scripts/qemu.sh` with a second virtual disk formatted vfat as the pendrive. Confirm: splash on boot, root unlocks with the pendrive attached, falls back to the passphrase prompt ~10s after booting without it, and `dasik plan` on the installed system is silent. Record the result either way; #173 tracks it as its own checkbox.

- [ ] **Step 4: Push and open the PR** (modo desatendido: pushing the feature branch and opening the PR is allowed; merging is not)

```bash
git push -u origin feat/issue-173-plymouth-luks-keyfile
gh pr create --title "feat(issue-173): plymouth splash and pendrive LUKS unlock" --body "…"
```

The body must include a **How to test manually** section: which `dasik check`/`plan` invocations to run, with which sample config, `/mnt` expectations, the re-run no-op check, and the error cases (missing pendrive, absent `unlock_keydev_fs`, invalid theme).

- [ ] **Step 5: Post the agentic verdict comment** with the captured build+smoke output (`gh pr comment`), as CLAUDE.md requires. Never merge.

---

## Self-review

**Spec coverage:** plymouth model (T1), package + theme file (T2), `splash` (T3), initramfs hook/module (T4), theme-triggers-rebuild (T5), capture + conditional `splash` subtraction (T6), plymouth matrices/docs/sample (T7); `unlock_keydev_fs` + preflight (T8), cmdline normalization + timeout (T9), keydev module + embedded keyfile (T10), `LuksKeyfileAction` with single ownership (T11), sync capture (T12), keyfile matrices/docs/sample (T13), gates + VM + PR (T14). Every spec section maps to a task.

**Placeholders:** the two matrix tasks (T7, T13) and T12 deliberately say "follow the existing fixtures in <file>" instead of inventing fixture code — those files exist and their helpers must be reused, not duplicated. Every other step carries the actual code.

**Type consistency:** `detect_plymouth`/`detect_keydev_filesystems`/`detect_embedded_keyfiles` (base) ↔ `has_plymouth`/`keydev_filesystems`/`embedded_keyfiles` (backends); `plymouth_installed(target)` used by both `PlymouthAction` and `KernelCmdlineAction.import_state`; `_keydev_spec` in the cmdline action produces exactly the `unlock_keydev` form `_read_luks_keyfile` captures back.

# MVP PR E: bootloader v3 domain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox (`- [ ]`).

**Goal:** Add a `BootloaderAction` v3 domain that installs the bootloader (systemd-boot or GRUB) and creates the base boot entry, so a from-scratch `apply` produces a **bootable** system. `KernelCmdlineAction` already maintains the entry params afterward.

**Architecture:** New v3 action, `config_key='__root__'` (reads `bootloader`, `disks` for the root label, `enable_microcode`). Idempotent via an install marker (`bootctl`: `/boot/EFI/systemd/systemd-bootx64.efi`; GRUB: `/boot/grub/grub.cfg`). Destructive install in `_install()` (mocked in tests). Registered in the boot phase **before** `KernelCmdlineAction`.

**Tech Stack:** Python 3.10+, pytest, mock. `_install()` (bootctl/grub-install/pacman) never runs in tests.

**Spec:** `docs/superpowers/specs/2026-06-02-mvp-nixos-expansion-design.md` (bootable-MVP follow-up).

**Branch:** `feat-mvp-bootloader-v3` (off `main`).

---

## Task E.1: BootloaderAction v3

**Files:**
- Create: `dasik/lib/actions/bootloader_action.py`
- Modify: `dasik/lib/actions/actions_handler_v2.py` (import + register in boot phase, before KernelCmdlineAction)
- Test (create): `tests/lib/actions/test_bootloader_action.py`

- [ ] **Step 1: Failing tests**

Create `tests/lib/actions/test_bootloader_action.py`:

```python
from unittest.mock import patch

from dasik.lib.actions.bootloader_action import BootloaderAction
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Op


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


def _cfg(bootloader="sd-boot", root_label="root"):
    return {
        "bootloader": bootloader,
        "enable_microcode": False,
        "disks": {"disks": [{
            "device": "/dev/vda", "partition_table": "gpt", "wipe_disk": False,
            "partitions": [
                {"label": "boot", "size": "512MiB", "filesystem": "fat32",
                 "partition_type": "esp", "mountpoint": "/boot", "format": True},
                {"label": root_label, "size": "rest", "filesystem": "ext4",
                 "partition_type": "linux", "mountpoint": "/", "format": True},
            ],
        }]},
    }


def _mark_sdboot(tmp_path):
    d = tmp_path / "boot" / "EFI" / "systemd"
    d.mkdir(parents=True, exist_ok=True)
    (d / "systemd-bootx64.efi").write_text("")


def _mark_grub(tmp_path):
    d = tmp_path / "boot" / "grub"
    d.mkdir(parents=True, exist_ok=True)
    (d / "grub.cfg").write_text("")


def test_is_v3_true():
    assert BootloaderAction.is_v3() is True


def test_root_label_from_disks():
    a = BootloaderAction(_cfg(root_label="myroot"))
    assert a._root_label() == "myroot"


def test_root_label_default_when_no_disks():
    a = BootloaderAction({"bootloader": "sd-boot"})
    assert a._root_label() == "root"


def test_actual_sdboot_absent(tmp_path):
    a = BootloaderAction(_cfg("sd-boot"), _ctx(tmp_path))
    assert a.actual() == set()


def test_actual_sdboot_present(tmp_path):
    _mark_sdboot(tmp_path)
    a = BootloaderAction(_cfg("sd-boot"), _ctx(tmp_path))
    assert a.actual() == {"sd-boot"}


def test_actual_grub_present(tmp_path):
    _mark_grub(tmp_path)
    a = BootloaderAction(_cfg("grub"), _ctx(tmp_path))
    assert a.actual() == {"grub"}


def test_plan_install_when_absent(tmp_path):
    a = BootloaderAction(_cfg("sd-boot"), _ctx(tmp_path))
    changes = a.plan(managed=[])
    assert changes and changes[0].op is Op.INSTALL and changes[0].item == "sd-boot"


def test_plan_empty_when_present(tmp_path):
    _mark_sdboot(tmp_path)
    a = BootloaderAction(_cfg("sd-boot"), _ctx(tmp_path))
    assert a.plan(managed=[]) == []


def test_apply_runs_install_when_changes(tmp_path):
    a = BootloaderAction(_cfg("grub"), _ctx(tmp_path))
    with patch.object(BootloaderAction, "_install") as inst:
        a.apply(a.plan(managed=[]))
        inst.assert_called_once()


def test_apply_noop_when_no_changes(tmp_path):
    _mark_grub(tmp_path)
    a = BootloaderAction(_cfg("grub"), _ctx(tmp_path))
    with patch.object(BootloaderAction, "_install") as inst:
        a.apply(a.plan(managed=[]))
        inst.assert_not_called()


def test_managed_keys(tmp_path):
    _mark_sdboot(tmp_path)
    a = BootloaderAction(_cfg("sd-boot"), _ctx(tmp_path))
    assert a.managed_keys() == {"bootloader": ["sd-boot"]}


def test_import_state_empty(tmp_path):
    a = BootloaderAction(_cfg("sd-boot"), _ctx(tmp_path))
    assert a.import_state(managed=[]) == {}


def test_name_and_optional():
    a = BootloaderAction(_cfg())
    assert a.name == "Bootloader"
    assert a.is_optional is False
```

- [ ] **Step 2: Run, expect failure**

Run: `pytest tests/lib/actions/test_bootloader_action.py -q`
Expected: ImportError (module missing).

- [ ] **Step 3: Implement the action**

Create `dasik/lib/actions/bootloader_action.py`:

```python
"""Action: install the bootloader and create the base boot entry (v3 domain "bootloader").

Installs systemd-boot (`bootctl install`) or GRUB (`grub-install` + `grub-mkconfig`)
and writes the initial loader entry. `KernelCmdlineAction` maintains the entry
params afterward. Idempotent via an install marker. Install-only. Target-aware.
The destructive install lives in `_install()` (mocked in tests).
"""
from __future__ import annotations
import os
from typing import Any, Dict, List, Optional
from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command
from ..state.change import Change, Op

_DOMAIN = "bootloader"
_SDBOOT_MARKER = "/boot/EFI/systemd/systemd-bootx64.efi"
_GRUB_MARKER = "/boot/grub/grub.cfg"


class BootloaderAction(AbstractAction):
    """Install the bootloader (systemd-boot or GRUB) declaratively."""

    _DOMAIN = _DOMAIN

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self._cfg = cfg
        self.bootloader: str = cfg.get("bootloader", "grub")
        self.enable_microcode: bool = cfg.get("enable_microcode", False)

    @property
    def name(self) -> str:
        return "Bootloader"

    @property
    def is_optional(self) -> bool:
        return False

    # --- target-aware paths ------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _p(self, canonical: str) -> str:
        t = self._target()
        return t.path(canonical) if t is not None else "/mnt" + canonical

    # --- config-derived helpers --------------------------------------- #

    def _root_label(self) -> str:
        disks = self._cfg.get("disks") or {}
        for disk in disks.get("disks", []):
            for part in disk.get("partitions", []):
                if part.get("mountpoint") == "/":
                    return part.get("label", "root")
        return "root"

    def _is_sdboot(self) -> bool:
        return self.bootloader in ("sd-boot", "systemd-boot")

    def _installed(self) -> bool:
        marker = _SDBOOT_MARKER if self._is_sdboot() else _GRUB_MARKER
        return os.path.exists(self._p(marker))

    # --- v3 contract -------------------------------------------------- #

    def actual(self) -> set:
        return {self.bootloader} if self._installed() else set()

    def managed_keys(self) -> dict:
        return {self._DOMAIN: sorted(self.actual())}

    def plan(self, managed) -> list:
        if not self._installed():
            return [Change(self._DOMAIN, Op.INSTALL, self.bootloader, reason="install bootloader")]
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

    # --- destructive install (mocked in tests) ------------------------ #

    def _ucode_initrds(self) -> List[str]:
        if not self.enable_microcode:
            return []
        # both are harmless if absent; the present one is used
        return ["/intel-ucode.img", "/amd-ucode.img"]

    def _install(self) -> None:  # pragma: no cover - shells out to bootctl/grub
        t = self._target()
        if self._is_sdboot():
            Command.execute("bootctl", ["install"], target=t)
            loader = self._p("/boot/loader/loader.conf")
            os.makedirs(os.path.dirname(loader), exist_ok=True)
            with open(loader, "w") as f:
                f.write("default arch\ntimeout 3\nconsole-mode max\n")
            entries_dir = self._p("/boot/loader/entries")
            os.makedirs(entries_dir, exist_ok=True)
            lines = ["title Arch Linux", "linux /vmlinuz-linux"]
            for img in self._ucode_initrds():
                lines.append(f"initrd {img}")
            lines.append("initrd /initramfs-linux.img")
            lines.append(f"options root=LABEL={self._root_label()} rw")
            with open(os.path.join(entries_dir, "arch.conf"), "w") as f:
                f.write("\n".join(lines) + "\n")
        else:
            Command.execute("pacman", ["--noconfirm", "--needed", "-S", "grub", "efibootmgr"], target=t)
            Command.execute("grub-install", [
                "--target=x86_64-efi", "--efi-directory=/boot", "--bootloader-id=GRUB",
            ], target=t)
            Command.execute("grub-mkconfig", ["-o", "/boot/grub/grub.cfg"], target=t)
```

- [ ] **Step 4: Run, expect pass**

Run: `pytest tests/lib/actions/test_bootloader_action.py -q`
Expected: all PASS.

- [ ] **Step 5: Register in the boot phase (before KernelCmdlineAction)**

In `dasik/lib/actions/actions_handler_v2.py`, add the import alongside the boot ones:
```python
    from .bootloader_action import BootloaderAction
```
and in the Phase 5 boot block, register it after `InitramfsAction` and **before** `KernelCmdlineAction`:
```python
    register_action(
        action_class=BootloaderAction,
        config_key='__root__',
        is_optional=False,
    )
```

- [ ] **Step 6: Full suite + coverage**

Run: `pytest -q` → all PASS. (Bootloader is `__root__`/mandatory; verb-integration configs lack a bootloader marker, so it will plan an INSTALL during apply — confirm `test_apply_is_idempotent_second_run_no_generation_2` still passes by also marking the fake root as bootloader-installed there if needed; mirror the base-marker fix: create `<tmp>/boot/grub/grub.cfg`.)

If that idempotency test now creates a generation 2, add to it:
```python
    (tmp_path / "boot" / "grub").mkdir(parents=True, exist_ok=True)
    (tmp_path / "boot" / "grub" / "grub.cfg").write_text("")
```
(Default `bootloader` is `grub`, so the grub marker converges it.)

Run: `pytest --cov=dasik -q` → total ≥ 80% (`_install` is `pragma: no cover`).

- [ ] **Step 7: Commit**

```bash
git add dasik/lib/actions/bootloader_action.py tests/lib/actions/test_bootloader_action.py dasik/lib/actions/actions_handler_v2.py tests/cli/test_verbs_integration.py
git commit -m "feat(bootloader): v3 domain (systemd-boot/grub install + base entry)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-review notes

- Installs the bootloader so a from-scratch apply is bootable → Task E.1. ✓
- Complements (does not duplicate) `KernelCmdlineAction` (which maintains entry params); registered before it. ✓
- Idempotent via install marker; install-only; target-aware; destructive body mocked. ✓
- `bootloader` default `grub`; `_root_label` derived from the `/`-mountpoint partition. ✓

# MVP PR A: NixOS-style expansion + toggle migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every feature toggle (bluetooth, cups, trim, kvm, wireguard, firewall, hardware_acceleration) participate in `plan`/`apply`/`sync` by expanding them into the shared `packages`/`systemd`/`files` domains (NixOS module model), and migrate `microsoft_fonts` to its own v3 domain.

**Architecture:** A pure expansion layer (`dasik/lib/expand/`) turns toggle sections into contributions merged into the base domains; `__main__` expands the config before reconcile (plan/apply/rollback) and subtracts toggle contributions after sync so the config file stays clean. The seven toggle action classes are deleted (logic moves to expansion functions). `microsoft_fonts` becomes a dedicated v3 action.

**Tech Stack:** Python 3.10+, pytest, `unittest.mock`. No model changes (all toggle config shapes already exist).

**Spec:** `docs/superpowers/specs/2026-06-02-mvp-nixos-expansion-design.md`

**Pre-flight (read before starting):**
- `dasik/__main__.py` `_cmd_plan` / `_cmd_apply` / `_cmd_sync` / `_cmd_rollback` — the hook points.
- `dasik/lib/state/config_writer.py` — `merge` overrides keys with fragment values; unknown keys pass through.
- `dasik/lib/models/systemd_model.py` (`enable_units`/`enable_sockets`/`disable_units`), `dasik/lib/models/file_model.py` (`FileEntry{name,content}`, `EtcFile{path,content}`).
- `dasik/lib/actions/actions_handler_v2.py` `setup_actions()` — toggle registrations to remove.
- `dasik/lib/actions/abstract_action.py` — v3 contract (`actual`/`plan`/`apply`/`managed_keys`/`import_state`).
- `dasik/lib/state/change.py` — `Change`, `Op`.

**Branch:** `feat-mvp-nixos-expansion` (already created; decomposition spec already committed).

---

## Slice 1 — Expansion infra + simple toggles (bluetooth, cups, trim, kvm)

### Task 1.1: Expansion package + four toggle functions

**Files:**
- Create: `dasik/lib/expand/__init__.py`
- Create: `dasik/lib/expand/toggles.py`
- Test: `tests/lib/expand/__init__.py` (empty), `tests/lib/expand/test_toggles.py`, `tests/lib/expand/test_expand_config.py`, `tests/lib/expand/test_subtract.py`

- [ ] **Step 1: Write the failing toggle tests**

Create `tests/lib/expand/__init__.py` (empty file).

Create `tests/lib/expand/test_toggles.py`:

```python
from dasik.lib.expand.toggles import (
    expand_bluetooth, expand_cups, expand_trim, expand_kvm,
)


def test_bluetooth_disabled_empty():
    assert expand_bluetooth({}) == {}
    assert expand_bluetooth({"bluetooth": {"enable": False}}) == {}


def test_bluetooth_enabled():
    out = expand_bluetooth({"bluetooth": {"enable": True, "package": "bluez"}})
    assert out["packages"] == ["bluez", "bluez-utils"]
    assert out["units"] == ["bluetooth.service"]


def test_cups_disabled_empty():
    assert expand_cups({}) == {}
    assert expand_cups({"cups": {"install": False}}) == {}


def test_cups_enabled():
    out = expand_cups({"cups": {"install": True}})
    assert "cups" in out["packages"] and "sane" in out["packages"]
    assert out["sockets"] == ["cups.socket"]


def test_trim_disabled_empty():
    assert expand_trim({}) == {}
    assert expand_trim({"enable_trim": False}) == {}


def test_trim_enabled():
    assert expand_trim({"enable_trim": True}) == {"units": ["fstrim.timer"]}


def test_kvm_disabled_empty():
    assert expand_kvm({}) == {}
    assert expand_kvm({"kvm": {"install": False}}) == {}


def test_kvm_enabled():
    out = expand_kvm({"kvm": {"install": True}})
    assert "qemu-full" in out["packages"] and "libvirt" in out["packages"]
    assert out["units"] == ["libvirtd.service", "virtlogd.service"]
    assert out["modprobe_conf"][0]["name"] == "dasik-nested-virt.conf"
    assert "nested=1" in out["modprobe_conf"][0]["content"]
```

- [ ] **Step 2: Run, expect failure**

Run: `pytest tests/lib/expand/test_toggles.py -q`
Expected: ImportError / module not found.

- [ ] **Step 3: Implement toggles**

Create `dasik/lib/expand/toggles.py`:

```python
"""Pure expansion functions: one per feature toggle.

Each takes the full config dict and returns a contribution dict with any of:
packages (list[str]), units (list[str]), sockets (list[str]),
modprobe_conf (list[{name, content}]), files (list[{path, content}]).
Returns {} (no contribution) when the toggle is absent or disabled.
"""
from __future__ import annotations
from typing import Any, Dict


def expand_bluetooth(config: Dict[str, Any]) -> Dict[str, Any]:
    cfg = config.get("bluetooth") or {}
    if not cfg.get("enable"):
        return {}
    pkg = cfg.get("package", "bluez")
    return {"packages": [pkg, "bluez-utils"], "units": ["bluetooth.service"]}


def expand_cups(config: Dict[str, Any]) -> Dict[str, Any]:
    cfg = config.get("cups") or {}
    if not cfg.get("install"):
        return {}
    return {
        "packages": ["cups", "cups-pdf", "system-config-printer", "sane", "sane-airscan"],
        "sockets": ["cups.socket"],
    }


def expand_trim(config: Dict[str, Any]) -> Dict[str, Any]:
    if not config.get("enable_trim"):
        return {}
    return {"units": ["fstrim.timer"]}


_KVM_PKGS = [
    "qemu-full", "qemu-block-gluster", "qemu-block-iscsi", "samba",
    "qemu-guest-agent", "qemu-user-static",
    "edk2-ovmf", "swtpm", "virt-firmware",
    "libvirt", "virt-manager",
    "iptables-nft", "dnsmasq", "openbsd-netcat", "dmidecode",
]


def expand_kvm(config: Dict[str, Any]) -> Dict[str, Any]:
    cfg = config.get("kvm") or {}
    if not cfg.get("install"):
        return {}
    return {
        "packages": list(_KVM_PKGS),
        "units": ["libvirtd.service", "virtlogd.service"],
        "modprobe_conf": [{
            "name": "dasik-nested-virt.conf",
            "content": "options kvm_intel nested=1\noptions kvm_amd nested=1\n",
        }],
    }


# Order matters only for deterministic output; aggregation de-dups.
TOGGLES = [expand_bluetooth, expand_cups, expand_trim, expand_kvm]
```

- [ ] **Step 4: Run, expect pass**

Run: `pytest tests/lib/expand/test_toggles.py -q`
Expected: PASS.

- [ ] **Step 5: Write failing expand_config / subtract tests**

Create `tests/lib/expand/test_expand_config.py`:

```python
from dasik.lib.expand import expand_config, contributions


def test_contributions_aggregates_and_dedups():
    cfg = {"bluetooth": {"enable": True}, "cups": {"install": True}}
    c = contributions(cfg)
    assert "bluez" in c["packages"] and "cups" in c["packages"]
    assert "bluetooth.service" in c["units"]
    assert "cups.socket" in c["sockets"]


def test_expand_merges_into_packages_and_systemd():
    cfg = {
        "packages": ["firefox"],
        "systemd": {"enable_units": ["NetworkManager.service"]},
        "bluetooth": {"enable": True},
        "enable_trim": True,
    }
    out = expand_config(cfg)
    assert out["packages"] == ["firefox", "bluez", "bluez-utils"]
    assert "NetworkManager.service" in out["systemd"]["enable_units"]
    assert "bluetooth.service" in out["systemd"]["enable_units"]
    assert "fstrim.timer" in out["systemd"]["enable_units"]


def test_expand_merges_modprobe_conf_for_kvm():
    out = expand_config({"kvm": {"install": True}})
    names = [e["name"] for e in out["modprobe_conf"]]
    assert "dasik-nested-virt.conf" in names


def test_expand_does_not_mutate_input():
    cfg = {"packages": ["firefox"], "bluetooth": {"enable": True}}
    expand_config(cfg)
    assert cfg["packages"] == ["firefox"]  # original untouched


def test_expand_noop_when_no_toggles():
    cfg = {"packages": ["firefox"]}
    assert expand_config(cfg) == cfg
```

Create `tests/lib/expand/test_subtract.py`:

```python
from dasik.lib.expand import subtract_contributions


def test_subtract_removes_toggle_packages_from_capture():
    original = {"packages": ["firefox"], "bluetooth": {"enable": True}}
    captured = {"packages": ["firefox", "bluez", "bluez-utils", "htop"]}
    out = subtract_contributions(captured, original)
    assert out["packages"] == ["firefox", "htop"]  # bluez* attributed to toggle


def test_subtract_keeps_package_user_also_declared():
    original = {"packages": ["bluez"], "bluetooth": {"enable": True}}
    captured = {"packages": ["bluez", "bluez-utils"]}
    out = subtract_contributions(captured, original)
    assert "bluez" in out["packages"]  # user-declared, kept
    assert "bluez-utils" not in out["packages"]  # toggle-only, removed


def test_subtract_removes_units_and_sockets():
    original = {"cups": {"install": True}, "bluetooth": {"enable": True}}
    captured = {"systemd": {"enable_units": ["bluetooth.service", "sshd.service"],
                            "enable_sockets": ["cups.socket", "other.socket"]}}
    out = subtract_contributions(captured, original)
    assert out["systemd"]["enable_units"] == ["sshd.service"]
    assert out["systemd"]["enable_sockets"] == ["other.socket"]


def test_subtract_noop_without_toggles():
    captured = {"packages": ["firefox", "htop"]}
    assert subtract_contributions(captured, {}) == captured
```

- [ ] **Step 6: Run, expect failure**

Run: `pytest tests/lib/expand/test_expand_config.py tests/lib/expand/test_subtract.py -q`
Expected: ImportError (`dasik.lib.expand` has no `expand_config`).

- [ ] **Step 7: Implement the expand package**

Create `dasik/lib/expand/__init__.py`:

```python
"""NixOS-style expansion of feature toggles into the shared base domains.

`expand_config(config)` returns a derived config (used for plan/apply) with each
active toggle's packages/units/sockets/modprobe_conf/files merged into the base
`packages` / `systemd` / `modprobe_conf` / `files` sections.

`subtract_contributions(new_config, original)` removes toggle-owned items from a
captured config so `sync` does not duplicate them into the file — a resource a
toggle contributes is attributed to the toggle, not the base domain.
"""
from __future__ import annotations
import copy
from typing import Any, Dict

from .toggles import TOGGLES

_LIST_KEYS = ("packages", "units", "sockets", "modprobe_conf", "files")


def contributions(config: Dict[str, Any]) -> Dict[str, Any]:
    """Aggregate every active toggle's contribution (order-preserving, de-duped)."""
    out: Dict[str, list] = {k: [] for k in _LIST_KEYS}
    for fn in TOGGLES:
        frag = fn(config) or {}
        for key in _LIST_KEYS:
            for item in frag.get(key, []):
                if item not in out[key]:
                    out[key].append(item)
    return out


def _merge_list(base: list, extra: list) -> list:
    out = list(base)
    for item in extra:
        if item not in out:
            out.append(item)
    return out


def expand_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of config with toggle contributions merged into base domains."""
    merged = copy.deepcopy(config)
    c = contributions(config)

    if c["packages"]:
        merged["packages"] = _merge_list(merged.get("packages", []), c["packages"])

    if c["units"] or c["sockets"]:
        sd = dict(merged.get("systemd", {}) or {})
        sd["enable_units"] = _merge_list(sd.get("enable_units", []), c["units"])
        sd["enable_sockets"] = _merge_list(sd.get("enable_sockets", []), c["sockets"])
        merged["systemd"] = sd

    if c["modprobe_conf"]:
        merged["modprobe_conf"] = _merge_list(merged.get("modprobe_conf", []), c["modprobe_conf"])

    if c["files"]:
        merged["files"] = _merge_list(merged.get("files", []), c["files"])

    return merged


def subtract_contributions(new_config: Dict[str, Any], original: Dict[str, Any]) -> Dict[str, Any]:
    """Drop toggle-contributed items from new_config not present in original base."""
    result = copy.deepcopy(new_config)
    c = contributions(original)

    orig_pkgs = set(original.get("packages", []))
    if "packages" in result:
        result["packages"] = [
            p for p in result["packages"] if p not in c["packages"] or p in orig_pkgs
        ]

    if "systemd" in result and isinstance(result["systemd"], dict):
        sd = result["systemd"]
        orig_sd = original.get("systemd", {}) or {}
        for key, items in (("enable_units", c["units"]), ("enable_sockets", c["sockets"])):
            if key in sd:
                keep = set(orig_sd.get(key, []))
                sd[key] = [x for x in sd[key] if x not in items or x in keep]

    for key, items in (("modprobe_conf", c["modprobe_conf"]), ("files", c["files"])):
        if key in result:
            orig_items = original.get(key, [])
            result[key] = [x for x in result[key] if x not in items or x in orig_items]

    return result
```

- [ ] **Step 8: Run, expect pass**

Run: `pytest tests/lib/expand/ -q`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add dasik/lib/expand/ tests/lib/expand/
git commit -m "feat(expand): NixOS-style toggle expansion layer (bluetooth/cups/trim/kvm)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 1.2: Hook expansion into the verb commands

**Files:**
- Modify: `dasik/__main__.py` (`_cmd_plan`, `_cmd_apply`, `_cmd_sync`, `_cmd_rollback`)
- Test: `tests/cli/test_verbs_integration.py` (add a toggle-expansion case)

- [ ] **Step 1: Write a failing integration test**

Append to `tests/cli/test_verbs_integration.py`:

```python
def test_apply_expands_bluetooth_toggle_into_packages(tmp_path):
    # bluetooth toggle must expand so the packages domain installs bluez
    p = _write(tmp_path, {"packages": ["git"], "bluetooth": {"enable": True}})
    captured = {}

    def run(cmd, args=None, *a, **k):
        if cmd == "pacman" and args and args[0] == "-Qqe":
            return MagicMock(stdout=b"", stderr=b"", returncode=0)
        if cmd == "pacman" and args and "-S" in args:
            captured["installed"] = args
        return MagicMock(stdout=b"", stderr=b"", returncode=0)

    with patch("dasik.lib.command_worker.command_worker.Command.execute", side_effect=run), \
         patch("subprocess.run", side_effect=_fake_exec({("pacman", "-Qqe"): b""})):
        code = main(["apply", str(p), "--target", str(tmp_path), "--yes"])
    assert code == 0
    assert "bluez" in captured.get("installed", [])
```

- [ ] **Step 2: Run, expect failure**

Run: `pytest tests/cli/test_verbs_integration.py::test_apply_expands_bluetooth_toggle_into_packages -q`
Expected: FAIL (bluez not installed — toggle ignored without the hook).

- [ ] **Step 3: Add the import and hook**

In `dasik/__main__.py`, add to the imports block (near the other `from dasik.lib...` lines):

```python
from dasik.lib.expand import expand_config, subtract_contributions
```

In `_cmd_plan`, after `config = json.loads(config_path.read_text())` (inside the try) — i.e. right before `setup_actions()` — add:

```python
    config = expand_config(config)
```

In `_cmd_apply`, same: after the `config = json.loads(...)` load and before `setup_actions()`:

```python
    config = expand_config(config)
```

In `_cmd_rollback`, after `restored_config, _restored_manifest = gen_store.restore(number)` and before `setup_actions()`:

```python
    restored_config = expand_config(restored_config)
```

In `_cmd_sync`, leave the load as-is (reconcile runs on the **original** config), but after `new_config, new_manifest = reconciler.sync()` and before the empty-key cleanup line, add:

```python
    new_config = subtract_contributions(new_config, config)
```

- [ ] **Step 4: Run the integration test + full verb suite**

Run: `pytest tests/cli/test_verbs_integration.py -q`
Expected: all PASS (including the new bluetooth case).

- [ ] **Step 5: Commit**

```bash
git add dasik/__main__.py tests/cli/test_verbs_integration.py
git commit -m "feat(cli): expand toggles before reconcile; subtract on sync

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 1.3: Retire the four legacy toggle actions

**Files:**
- Modify: `dasik/lib/actions/actions_handler_v2.py` (remove imports + registrations for Bluetooth/Cups/Kvm/Trim)
- Delete: `dasik/lib/actions/bluetooth_action.py`, `cups_action.py`, `kvm_action.py`, `trim_action.py`
- Delete: `tests/lib/actions/test_bluetooth_action.py`, `test_cups_action.py`, `test_kvm_action.py`, `test_trim_action.py`
- Check: `dasik/lib/actions/__init__.py` and any other importers

- [ ] **Step 1: Find all importers of the four actions**

Run:
```bash
grep -rn "BluetoothAction\|CupsAction\|KvmAction\|TrimAction\|bluetooth_action\|cups_action\|kvm_action\|trim_action" dasik/
```
Expected references: `actions_handler_v2.py` (imports + registrations) and possibly `actions/__init__.py`. Note every file that needs editing.

- [ ] **Step 2: Remove registrations + imports**

In `dasik/lib/actions/actions_handler_v2.py`, delete the four import lines (`from .bluetooth_action import BluetoothAction`, `cups`, `kvm`, `trim`) and the four `register_action(...)` blocks (`BluetoothAction`, `CupsAction`, `KvmAction`, `TrimAction`).

If `dasik/lib/actions/__init__.py` exports any of them, remove those exports too.

- [ ] **Step 3: Delete the action + test files**

```bash
git rm dasik/lib/actions/bluetooth_action.py dasik/lib/actions/cups_action.py \
       dasik/lib/actions/kvm_action.py dasik/lib/actions/trim_action.py \
       tests/lib/actions/test_bluetooth_action.py tests/lib/actions/test_cups_action.py \
       tests/lib/actions/test_kvm_action.py tests/lib/actions/test_trim_action.py
```

- [ ] **Step 4: Run the full suite**

Run: `pytest -q`
Expected: all PASS (no import errors; toggles now handled by expansion).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(actions): retire bluetooth/cups/kvm/trim actions (now expansion)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Slice 2 — File-emitting toggles (wireguard, firewall, hardware_acceleration)

### Task 2.1: Add three expansion functions

**Files:**
- Modify: `dasik/lib/expand/toggles.py`
- Test: `tests/lib/expand/test_toggles.py` (append cases)

- [ ] **Step 1: Append failing tests**

Append to `tests/lib/expand/test_toggles.py`:

```python
from dasik.lib.expand.toggles import (
    expand_wireguard, expand_firewall, expand_hwaccel,
)


def test_wireguard_disabled_empty():
    assert expand_wireguard({}) == {}
    assert expand_wireguard({"wireguard": {"enable": False}}) == {}


def test_wireguard_enabled():
    out = expand_wireguard({"wireguard": {
        "enable": True, "interface_name": "wg0", "config_content": "[Interface]\n",
    }})
    assert out["packages"] == ["wireguard-tools"]
    assert out["units"] == ["wg-quick@wg0.service"]
    assert out["files"][0]["path"] == "/etc/wireguard/wg0.conf"
    assert out["files"][0]["content"] == "[Interface]\n"


def test_firewall_disabled_empty():
    assert expand_firewall({}) == {}
    assert expand_firewall({"firewall": {"enable": False}}) == {}


def test_firewall_enabled():
    out = expand_firewall({"firewall": {"enable": True}})
    assert out["packages"] == ["firewalld"]
    assert out["units"] == ["firewalld.service"]


def test_hwaccel_disabled_empty():
    assert expand_hwaccel({}) == {}
    assert expand_hwaccel({"hardware_acceleration": {"enable": False}}) == {}


def test_hwaccel_enabled_uses_drivers():
    out = expand_hwaccel({
        "hardware_acceleration": {"enable": True, "install_codecs": True},
        "drivers": ["intel", "amd"],
    })
    assert "intel-media-driver" in out["packages"]
    assert "libva-mesa-driver" in out["packages"]
    assert "libva-utils" in out["packages"]  # common


def test_hwaccel_enabled_no_drivers_only_common():
    out = expand_hwaccel({"hardware_acceleration": {"enable": True}, "drivers": []})
    assert out["packages"] == ["libva-utils", "vdpauinfo"]
```

- [ ] **Step 2: Run, expect failure**

Run: `pytest tests/lib/expand/test_toggles.py -q`
Expected: ImportError for the three new functions.

- [ ] **Step 3: Implement the three functions**

Add to `dasik/lib/expand/toggles.py` (before the `TOGGLES` list):

```python
def expand_wireguard(config: Dict[str, Any]) -> Dict[str, Any]:
    cfg = config.get("wireguard") or {}
    if not cfg.get("enable"):
        return {}
    iface = cfg.get("interface_name", "wg0")
    return {
        "packages": ["wireguard-tools"],
        "units": [f"wg-quick@{iface}.service"],
        "files": [{
            "path": f"/etc/wireguard/{iface}.conf",
            "content": cfg.get("config_content", ""),
        }],
    }


def expand_firewall(config: Dict[str, Any]) -> Dict[str, Any]:
    cfg = config.get("firewall") or {}
    if not cfg.get("enable"):
        return {}
    return {"packages": ["firewalld"], "units": ["firewalld.service"]}


# common HW-accel packages + per-driver extras (mirrors the old action)
_HWACCEL_COMMON = ["libva-utils", "vdpauinfo"]
_HWACCEL_DRIVER_PKGS = {
    "nvidia": ["libva-nvidia-driver", "nvtop"],
    "intel": ["intel-media-driver", "intel-gpu-tools", "libvdpau-va-gl"],
    "amd": ["libva-mesa-driver", "mesa-vdpau"],
}


def expand_hwaccel(config: Dict[str, Any]) -> Dict[str, Any]:
    cfg = config.get("hardware_acceleration") or {}
    if not cfg.get("enable"):
        return {}
    pkgs = list(_HWACCEL_COMMON)
    for drv in config.get("drivers", []):
        for p in _HWACCEL_DRIVER_PKGS.get(drv, []):
            if p not in pkgs:
                pkgs.append(p)
    return {"packages": pkgs}
```

Update the `TOGGLES` list:

```python
TOGGLES = [
    expand_bluetooth, expand_cups, expand_trim, expand_kvm,
    expand_wireguard, expand_firewall, expand_hwaccel,
]
```

- [ ] **Step 4: Run, expect pass**

Run: `pytest tests/lib/expand/ -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/expand/toggles.py tests/lib/expand/test_toggles.py
git commit -m "feat(expand): wireguard/firewall/hwaccel toggle expansion

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 2.2: Retire the three legacy actions

**Files:**
- Modify: `dasik/lib/actions/actions_handler_v2.py`
- Delete: `dasik/lib/actions/wireguard_action.py`, `firewall_action.py`, `hw_accel_action.py`
- Delete: `tests/lib/actions/test_wireguard_action.py`, `test_firewall_action.py`, `test_hw_accel_action.py`

- [ ] **Step 1: Find importers**

Run:
```bash
grep -rn "WireguardAction\|FirewallAction\|HardwareAccelAction\|wireguard_action\|firewall_action\|hw_accel_action" dasik/
```

- [ ] **Step 2: Remove registrations + imports** in `actions_handler_v2.py` (the three imports + three `register_action` blocks); remove any `__init__.py` exports.

- [ ] **Step 3: Delete files**

```bash
git rm dasik/lib/actions/wireguard_action.py dasik/lib/actions/firewall_action.py \
       dasik/lib/actions/hw_accel_action.py \
       tests/lib/actions/test_wireguard_action.py tests/lib/actions/test_firewall_action.py \
       tests/lib/actions/test_hw_accel_action.py
```

- [ ] **Step 4: Run the full suite**

Run: `pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(actions): retire wireguard/firewall/hwaccel actions (now expansion)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Slice 3 — microsoft_fonts v3 domain

### Task 3.1: Migrate MicrosoftFontsAction to the v3 contract

**Files:**
- Modify (full rewrite): `dasik/lib/actions/ms_fonts_action.py`
- Modify (full rewrite): `tests/lib/actions/test_ms_fonts_action.py`

Behavior: `actual()` = `{"windows-fonts"}` when the fonts dir is populated, else
`set()`. `plan()` emits one `INSTALL` when `install` and `source_iso` are set and
the fonts are missing. `apply()` shells out (mocked in tests). `import_state()`
returns `{}` (the section is user-owned; sync leaves it as-is). Target-aware.

- [ ] **Step 1: Replace the test file (failing)**

Overwrite `tests/lib/actions/test_ms_fonts_action.py`:

```python
from unittest.mock import patch

from dasik.lib.actions.ms_fonts_action import MicrosoftFontsAction
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Op

_FONTS = "/usr/local/share/fonts/WindowsFonts"


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


def _populate(tmp_path, n=20):
    d = tmp_path / _FONTS.lstrip("/")
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (d / f"f{i}.ttf").write_text("x")


def test_is_v3_true():
    assert MicrosoftFontsAction.is_v3() is True


def test_actual_empty_when_absent(tmp_path):
    a = MicrosoftFontsAction({"install": True, "source_iso": "/w.iso"}, _ctx(tmp_path))
    assert a.actual() == set()


def test_actual_present_when_populated(tmp_path):
    _populate(tmp_path)
    a = MicrosoftFontsAction({"install": True, "source_iso": "/w.iso"}, _ctx(tmp_path))
    assert a.actual() == {"windows-fonts"}


def test_plan_install_when_declared_and_missing(tmp_path):
    a = MicrosoftFontsAction({"install": True, "source_iso": "/w.iso"}, _ctx(tmp_path))
    changes = a.plan(managed=[])
    assert changes and changes[0].op is Op.INSTALL


def test_plan_empty_when_present(tmp_path):
    _populate(tmp_path)
    a = MicrosoftFontsAction({"install": True, "source_iso": "/w.iso"}, _ctx(tmp_path))
    assert a.plan(managed=[]) == []


def test_plan_empty_when_no_iso(tmp_path):
    a = MicrosoftFontsAction({"install": True, "source_iso": ""}, _ctx(tmp_path))
    assert a.plan(managed=[]) == []


def test_plan_empty_when_not_install(tmp_path):
    a = MicrosoftFontsAction({"install": False, "source_iso": "/w.iso"}, _ctx(tmp_path))
    assert a.plan(managed=[]) == []


def test_apply_runs_install_when_changes(tmp_path):
    a = MicrosoftFontsAction({"install": True, "source_iso": "/w.iso"}, _ctx(tmp_path))
    with patch.object(MicrosoftFontsAction, "_install") as inst:
        a.apply(a.plan(managed=[]))
        inst.assert_called_once()


def test_apply_noop_when_no_changes(tmp_path):
    a = MicrosoftFontsAction({"install": True, "source_iso": "/w.iso"}, _ctx(tmp_path))
    with patch.object(MicrosoftFontsAction, "_install") as inst:
        a.apply([])
        inst.assert_not_called()


def test_import_state_empty(tmp_path):
    a = MicrosoftFontsAction({"install": True, "source_iso": "/w.iso"}, _ctx(tmp_path))
    assert a.import_state(managed=[]) == {}


def test_name_and_optional():
    a = MicrosoftFontsAction({})
    assert a.name == "Microsoft Fonts"
    assert a.is_optional is True
```

- [ ] **Step 2: Run, expect failure**

Run: `pytest tests/lib/actions/test_ms_fonts_action.py -q`
Expected: FAIL (`is_v3` False; no `actual`/`plan`/`_install`).

- [ ] **Step 3: Rewrite the action**

Overwrite `dasik/lib/actions/ms_fonts_action.py`:

```python
"""Action: install Microsoft fonts from a Windows ISO (v3 domain "microsoft_fonts").

Idempotent: a no-op once the fonts directory is populated. Gated on a declared
`source_iso`. `apply()` mounts/extracts the ISO and copies the fonts (shelled
out; covered via mocked `_install`). Target-aware.
"""
from __future__ import annotations
import os
import subprocess
from typing import Any, Dict, Optional
from .abstract_action import AbstractAction
from ..state.change import Change, Op

_FONTS_DIR = "/usr/local/share/fonts/WindowsFonts"
_DOMAIN = "microsoft_fonts"


class MicrosoftFontsAction(AbstractAction):
    """Extract and install MS fonts from a Windows ISO (v3 domain)."""

    _DOMAIN = _DOMAIN

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self.install: bool = cfg.get("install", False)
        self.source_iso: str = cfg.get("source_iso") or ""

    @property
    def name(self) -> str:
        return "Microsoft Fonts"

    @property
    def is_optional(self) -> bool:
        return True

    # --- target-aware paths ------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _p(self, canonical: str) -> str:
        t = self._target()
        return t.path(canonical) if t is not None else "/mnt" + canonical

    def _fonts_present(self) -> bool:
        d = self._p(_FONTS_DIR)
        return os.path.isdir(d) and len(os.listdir(d)) > 10

    # --- v3 contract -------------------------------------------------- #

    def actual(self) -> set:
        return {"windows-fonts"} if self._fonts_present() else set()

    def managed_keys(self) -> dict:
        return {self._DOMAIN: sorted(self.actual())}

    def plan(self, managed) -> list:
        if self.install and self.source_iso and not self._fonts_present():
            return [Change(self._DOMAIN, Op.INSTALL, "windows-fonts", reason="from source_iso")]
        return []

    def apply(self, changes) -> None:
        if changes:
            self._install()

    def import_state(self, managed=None) -> dict:
        # The section is user-owned (install flag + ISO path); sync leaves it.
        return {}

    # --- legacy executor bridge --------------------------------------- #

    def is_needed(self) -> bool:
        return bool(self.plan(managed=[]))

    def execute(self) -> None:
        self._install()

    def verify(self) -> bool:
        return self._fonts_present()

    # --- the destructive bit (shelled out; mocked in tests) ----------- #

    def _install(self) -> None:  # pragma: no cover - shells out to 7z/arch-chroot
        root = self._target().root if self._target() is not None else "/mnt"
        subprocess.run(
            ["arch-chroot", root, "pacman", "--noconfirm", "--needed", "-S", "7zip"],
            check=True,
        )
        work = self._p("/tmp/ms-fonts-work")
        os.makedirs(work, exist_ok=True)
        iso_inner = self.source_iso.replace(root, "", 1) if self.source_iso.startswith(root) \
            else self.source_iso
        subprocess.run(
            ["arch-chroot", root, "7z", "e", iso_inner, "sources/install.wim",
             "-o/tmp/ms-fonts-work"], check=True,
        )
        subprocess.run(
            ["arch-chroot", root, "7z", "e", "/tmp/ms-fonts-work/install.wim",
             "1/Windows/Fonts/*.ttf", "1/Windows/Fonts/*.ttc",
             "-o/tmp/ms-fonts-work/fonts/"], check=True,
        )
        subprocess.run(["arch-chroot", root, "mkdir", "-p", _FONTS_DIR], check=True)
        subprocess.run(
            ["arch-chroot", root, "sh", "-c",
             f"cp /tmp/ms-fonts-work/fonts/* {_FONTS_DIR}/ && chmod 644 {_FONTS_DIR}/*"],
            check=True,
        )
        subprocess.run(["arch-chroot", root, "fc-cache", "--force"], check=True)
```

- [ ] **Step 4: Run, expect pass**

Run: `pytest tests/lib/actions/test_ms_fonts_action.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/ms_fonts_action.py tests/lib/actions/test_ms_fonts_action.py
git commit -m "feat(msfonts): migrate to v3 domain (idempotent fonts-present check)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification (PR A)

- [ ] **Step 1: Full suite**

Run: `pytest -q`
Expected: all PASS.

- [ ] **Step 2: Coverage gate**

Run: `pytest --cov=dasik -q`
Expected: total ≥ 80%. The `expand/` package should be ~100%; deleted action files shrink the denominator.

- [ ] **Step 3: Sanity — sync does not duplicate a toggle package**

Run:
```bash
pytest tests/cli/test_verbs_integration.py -q
```
Expected: PASS, including the bluetooth-expansion case from Task 1.2.

---

## Self-review notes (spec coverage)

- Spec "Expansion layer" → Task 1.1 (`expand/`), Task 2.1 (file toggles). ✓
- Spec "sync subtraction rule" → Task 1.1 `subtract_contributions` + Task 1.2 hook + `test_subtract.py`. ✓
- Spec "expansion applied for plan/apply/rollback; original kept for sync" → Task 1.2. ✓
- Spec "delete legacy toggle classes + tests, replace with expansion tests" → Tasks 1.3, 2.2. ✓
- Spec "microsoft_fonts dedicated v3 domain, gated on source_iso, idempotent" → Task 3.1. ✓
- Spec "coverage ≥ 80%, verb suite green" → Final verification. ✓
- Naming consistency: `expand_config`, `contributions`, `subtract_contributions`, `TOGGLES`, `expand_<toggle>`, `_fonts_present`, `_install`, `_DOMAIN` — consistent across tasks. ✓
- trim drops the old cryptsetup `--persistent refresh` and `util-linux` install (disk-level / base-provided) — documented simplification, not a regression of declarative state. ✓
- No model changes — all toggle config keys already exist in their pydantic models. ✓

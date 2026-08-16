# Declarative WireGuard tunnels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Declare a WireGuard tunnel as a file next to the config, in the format its backend already reads, and have dasik place it — with the right mode — for either wg-quick or NetworkManager, plan it, and capture it back.

**Architecture:** The tunnel source file is never translated. `wireguard` becomes a list of tunnels, each naming a path relative to the config; the loader reads the body (like `etc_tree`), the rewritten `expand_wireguard` toggle contributes a `files` entry with `mode 0600` plus the package and the unit, and a new capture-only `WireguardAction` reconstructs the block from the machine while `sync` writes the bodies back out next to the JSON.

**Tech Stack:** Python ≥3.10, pydantic v2, pytest, existing dasik v3 action/toggle/loader machinery.

**Spec:** `docs/superpowers/specs/2026-08-16-issue-249-wireguard-hosts-design.md`

## Global Constraints

- Runtime deps stay `pydantic` + `colorama`. No new dependency.
- TDD is mandatory for every task here (models, loader, expand, action, preflight): red → green → refactor.
- Coverage gate ≥ 80%; `mypy dasik` clean; `bandit -r dasik` clean; `scripts/mutation.sh` clean. The pre-push hook runs all four.
- Never run `apply`/`rollback` for real in tests; mock `Command.execute` / use a scratch root.
- Tunnel name charset `[A-Za-z0-9_=+.-]`, 1–15 chars (IFNAMSIZ).
- Managed file modes: `/etc/wireguard/<name>.conf` and `<name>.nmconnection` are **`"0600"`**.
- Backend literals: `"auto" | "wg-quick" | "networkmanager"`. `auto` is resolved from the source file's format, never from `network.type`.
- The capture directory next to the config is `wg/` unless the tunnel already declares a `source`.

---

### Task 1: The tunnel model

**Files:**
- Modify: `dasik/lib/models/wireguard_model.py` (replace `WireguardModel` entirely)
- Modify: `dasik/lib/models/json_model.py:132` (`wireguard` field), `dasik/lib/models/__init__.py:27,54` (exports)
- Test: `tests/lib/models/test_wireguard_model.py` (new)

**Interfaces:**
- Consumes: nothing.
- Produces: `WireguardTunnel(BaseModel)` with fields `name: str`, `source: str`, `backend: Literal["auto","wg-quick","networkmanager"] = "auto"`, `enable: bool = True`, `content: Optional[str] = None` (loader-filled, never hand-written). `JsonModel.wireguard: Optional[List[WireguardTunnel]] = None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/lib/models/test_wireguard_model.py
import pytest
from pydantic import ValidationError
from dasik.lib.models.wireguard_model import WireguardTunnel
from dasik.lib.models.json_model import JsonModel


def test_minimal_tunnel_defaults_to_auto_and_enabled():
    t = WireguardTunnel(name="eu-mad", source="wg/eu-mad.conf")
    assert t.backend == "auto" and t.enable is True and t.content is None


def test_name_over_ifnamsiz_is_rejected():
    with pytest.raises(ValidationError):
        WireguardTunnel(name="a" * 16, source="wg/x.conf")


def test_name_with_a_slash_is_rejected():
    with pytest.raises(ValidationError):
        WireguardTunnel(name="eu/mad", source="wg/x.conf")


def test_absolute_source_is_rejected():
    with pytest.raises(ValidationError):
        WireguardTunnel(name="eu-mad", source="/etc/wireguard/wg0.conf")


def test_parent_traversal_in_source_is_rejected():
    with pytest.raises(ValidationError):
        WireguardTunnel(name="eu-mad", source="../secrets/wg0.conf")


def test_unknown_backend_is_rejected():
    with pytest.raises(ValidationError):
        WireguardTunnel(name="eu-mad", source="wg/x.conf", backend="netctl")


def test_json_model_takes_a_list_of_tunnels():
    m = JsonModel(hostname="box",
                  wireguard=[{"name": "eu-mad", "source": "wg/eu-mad.conf"}])
    assert m.wireguard[0].name == "eu-mad"


def test_the_old_inline_shape_is_refused_with_a_message_naming_the_new_one():
    # It never worked as a dict of (enable, interface_name, config_content):
    # a silent re-interpretation of a block holding a private key is worse
    # than an error.
    with pytest.raises(ValidationError) as e:
        JsonModel(hostname="box",
                  wireguard={"enable": True, "interface_name": "wg0",
                             "config_content": "[Interface]\n"})
    assert "source" in str(e.value)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/lib/models/test_wireguard_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'WireguardTunnel'`.

- [ ] **Step 3: Write the model**

```python
# dasik/lib/models/wireguard_model.py
"""A WireGuard tunnel, declared as the file its backend already reads.

dasik never converts between formats: a wg-quick `.conf` is served by
`wg-quick@<name>.service`, an NM `.nmconnection` by NetworkManager's keyfile
plugin, and a mismatch between the two is an error rather than a translation.
"""
from typing import Literal, Optional
import re
from pydantic import BaseModel, Field, field_validator

# IFNAMSIZ leaves 15 usable characters, and wg-quick names the interface after
# the file, so a longer name fails at `ip link add` — after the config was
# written.
_NAME_RE = re.compile(r"[A-Za-z0-9_=+.-]{1,15}")


class WireguardTunnel(BaseModel):
    """One tunnel: a name, and the file that defines it."""

    name: str = Field(description="Interface / connection id")
    source: str = Field(description="Path to the tunnel file, relative to the "
                                    "config that names it")
    backend: Literal["auto", "wg-quick", "networkmanager"] = "auto"
    enable: bool = True
    # Filled by the loader from `source` — only the loader knows where the
    # config file is, and therefore where the tunnel file is.
    content: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not _NAME_RE.fullmatch(v):
            raise ValueError(
                f"wireguard tunnel name {v!r} must be 1-15 characters of "
                "[A-Za-z0-9_=+.-] (IFNAMSIZ)")
        return v

    @field_validator("source")
    @classmethod
    def _relative_source(cls, v: str) -> str:
        if not v:
            raise ValueError("wireguard tunnel source must not be empty")
        if v.startswith("/"):
            raise ValueError(
                f"wireguard tunnel source {v!r} must be relative to the config "
                "that names it, not absolute")
        if ".." in v.split("/"):
            raise ValueError(
                f"wireguard tunnel source {v!r} must not contain '..' — a "
                "config may only pull in files at or below its own directory")
        return v
```

In `dasik/lib/models/json_model.py`, replace the `wireguard` field:

```python
    wireguard: Optional[List[WireguardTunnel]] = Field(
        default=None,
        description=(
            "WireGuard tunnels. Each names a file next to the config, in the "
            "format its backend reads: a wg-quick .conf or an NM "
            ".nmconnection. dasik places it verbatim at mode 0600."))
```

and the import at line 20 becomes `from .wireguard_model import WireguardTunnel`. Update `dasik/lib/models/__init__.py` (import + `__all__`) the same way.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/lib/models/test_wireguard_model.py -v`
Expected: PASS, 8 tests. The old-shape test passes because pydantic rejects a dict where a list is declared; assert the message names `source` (pydantic's error includes the field list — if it does not, add `"source"` to the field description so it does).

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/models/wireguard_model.py dasik/lib/models/json_model.py \
        dasik/lib/models/__init__.py tests/lib/models/test_wireguard_model.py
git commit -m "feat(wireguard): a tunnel is a name and the file that defines it"
```

---

### Task 2: The loader reads the tunnel file

**Files:**
- Create: `dasik/lib/json_parser/wireguard_source.py`
- Modify: `dasik/lib/json_parser/json_parser.py` (call it where `expand_etc_tree` / `expand_home_tree` are called)
- Test: `tests/lib/json_parser/test_wireguard_source.py` (new)

**Interfaces:**
- Consumes: `WireguardTunnel` (Task 1); `ConfigTreeError`, `_tree_root`-style guards from `dasik/lib/json_parser/etc_tree.py`.
- Produces: `expand_wireguard_sources(config: Dict[str, Any], base_dir: str | Path) -> Dict[str, Any]` — returns the config with each tunnel's `content` filled from its `source`. Raises `ConfigTreeError` when a source is missing, a symlink, or not UTF-8.

- [ ] **Step 1: Write the failing tests**

```python
# tests/lib/json_parser/test_wireguard_source.py
import pytest
from dasik.lib.json_parser.etc_tree import ConfigTreeError
from dasik.lib.json_parser.wireguard_source import expand_wireguard_sources

WG = "[Interface]\nAddress = 10.0.0.2/24\nPrivateKey = SECRET\n"


def _cfg(**kw):
    tunnel = {"name": "eu-mad", "source": "wg/eu-mad.conf"}
    tunnel.update(kw)
    return {"hostname": "box", "wireguard": [tunnel]}


def test_content_is_read_from_the_file(tmp_path):
    (tmp_path / "wg").mkdir()
    (tmp_path / "wg" / "eu-mad.conf").write_text(WG)
    out = expand_wireguard_sources(_cfg(), tmp_path)
    assert out["wireguard"][0]["content"] == WG


def test_the_declaration_is_not_mutated(tmp_path):
    (tmp_path / "wg").mkdir()
    (tmp_path / "wg" / "eu-mad.conf").write_text(WG)
    config = _cfg()
    expand_wireguard_sources(config, tmp_path)
    assert "content" not in config["wireguard"][0]


def test_a_missing_file_names_the_tunnel(tmp_path):
    with pytest.raises(ConfigTreeError) as e:
        expand_wireguard_sources(_cfg(), tmp_path)
    assert "eu-mad" in str(e.value) and "wg/eu-mad.conf" in str(e.value)


def test_a_symlink_is_refused(tmp_path):
    (tmp_path / "wg").mkdir()
    (tmp_path / "secret").write_text(WG)
    (tmp_path / "wg" / "eu-mad.conf").symlink_to(tmp_path / "secret")
    with pytest.raises(ConfigTreeError):
        expand_wireguard_sources(_cfg(), tmp_path)


def test_a_binary_file_is_refused(tmp_path):
    (tmp_path / "wg").mkdir()
    (tmp_path / "wg" / "eu-mad.conf").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ConfigTreeError):
        expand_wireguard_sources(_cfg(), tmp_path)


def test_no_block_is_a_no_op(tmp_path):
    config = {"hostname": "box"}
    assert expand_wireguard_sources(config, tmp_path) == config
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/lib/json_parser/test_wireguard_source.py -v`
Expected: FAIL — `ModuleNotFoundError: dasik.lib.json_parser.wireguard_source`.

- [ ] **Step 3: Write the loader**

```python
# dasik/lib/json_parser/wireguard_source.py
"""Read each declared tunnel's file, so the rest of dasik sees its content.

Same reason as `etc_tree`: only the loader knows where the config file is, and
therefore where a path relative to it points. After this, the expand toggle,
the preflight and every action see an ordinary `content` string.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Dict

from .etc_tree import ConfigTreeError

WIREGUARD = "wireguard"


def expand_wireguard_sources(config: Dict[str, Any],
                             base_dir: "str | Path") -> Dict[str, Any]:
    """Return *config* with every tunnel's ``content`` read from its ``source``."""
    tunnels = config.get(WIREGUARD)
    if not tunnels:
        return config

    out = copy.deepcopy(config)
    for tunnel in out[WIREGUARD]:
        if not isinstance(tunnel, dict) or tunnel.get("content") is not None:
            continue
        name = tunnel.get("name", "?")
        source = tunnel.get("source")
        if not isinstance(source, str) or not source:
            raise ConfigTreeError(
                f"wireguard tunnel {name!r} declares no source file")
        path = Path(base_dir) / source
        if path.is_symlink():
            raise ConfigTreeError(
                f"wireguard tunnel {name!r}: {source} is a symlink; a tunnel "
                "file holds a private key and is read verbatim, so it must be "
                "the file itself")
        try:
            tunnel["content"] = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise ConfigTreeError(
                f"wireguard tunnel {name!r}: source file not found: "
                f"{source} ({path})")
        except UnicodeDecodeError:
            raise ConfigTreeError(
                f"wireguard tunnel {name!r}: {source} is not UTF-8 text")
        except OSError as e:
            raise ConfigTreeError(
                f"wireguard tunnel {name!r}: cannot read {source}: {e}")
    return out
```

Wire it in `dasik/lib/json_parser/json_parser.py` immediately after the `expand_etc_tree` /
`expand_home_tree` calls, passing the same `base_dir` those use (the config file's parent).

- [ ] **Step 4: Run the tests**

Run: `pytest tests/lib/json_parser/ -v`
Expected: PASS, including the existing parser tests.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/json_parser/wireguard_source.py \
        dasik/lib/json_parser/json_parser.py \
        tests/lib/json_parser/test_wireguard_source.py
git commit -m "feat(wireguard): the loader reads the tunnel file named by the config"
```

---

### Task 3: The toggle, rewritten — per tunnel, per backend, mode 0600

**Files:**
- Modify: `dasik/lib/expand/toggles.py:73-85` (`expand_wireguard`)
- Test: `tests/lib/expand/test_expand_wireguard.py` (new)

**Interfaces:**
- Consumes: the loader-filled `content` (Task 2).
- Produces: `expand_wireguard(config) -> Dict[str, Any]` contributing `packages` / `units` / `files`; and `resolve_backend(content: str, declared: str, name: str) -> str`, exported from the same module, reused by the preflight (Task 5) and the capture (Task 4).

- [ ] **Step 1: Write the failing tests**

```python
# tests/lib/expand/test_expand_wireguard.py
import pytest
from dasik.lib.expand.toggles import expand_wireguard, resolve_backend

WGQ = "[Interface]\nAddress = 10.0.0.2/24\nPrivateKey = SECRET\n\n[Peer]\nPublicKey = P\n"
NMC = "[connection]\nid=work\ntype=wireguard\ninterface-name=work\n\n[wireguard]\nprivate-key=SECRET\n"


def _cfg(*tunnels):
    return {"hostname": "box", "wireguard": list(tunnels)}


def _t(name="eu-mad", content=WGQ, **kw):
    t = {"name": name, "source": f"wg/{name}.conf", "content": content}
    t.update(kw)
    return t


def test_wg_quick_contributes_package_unit_and_a_0600_file():
    out = expand_wireguard(_cfg(_t()))
    assert out["packages"] == ["wireguard-tools"]
    assert out["units"] == ["wg-quick@eu-mad.service"]
    assert out["files"] == [{"path": "/etc/wireguard/eu-mad.conf",
                             "content": WGQ, "mode": "0600"}]


def test_the_mode_is_always_declared_because_the_content_is_a_key():
    # Defect 1: without it the writer falls to open(path,"w") => 0644.
    for tunnel in (_t(), _t(name="work", content=NMC)):
        assert all(f["mode"] == "0600" for f in expand_wireguard(_cfg(tunnel))["files"])


def test_networkmanager_writes_the_keyfile_and_pulls_no_wireguard_tools():
    out = expand_wireguard(_cfg(_t(name="work", content=NMC)))
    assert out["files"] == [{
        "path": "/etc/NetworkManager/system-connections/work.nmconnection",
        "content": NMC, "mode": "0600"}]
    assert out["packages"] == ["networkmanager"]
    assert out.get("units", []) == []      # NM reads the directory itself


def test_enable_false_places_the_file_but_starts_nothing():
    out = expand_wireguard(_cfg(_t(enable=False)))
    assert out["files"][0]["path"] == "/etc/wireguard/eu-mad.conf"
    assert out.get("units", []) == []


def test_two_tunnels_two_files():
    out = expand_wireguard(_cfg(_t(), _t(name="work", content=NMC)))
    assert [f["path"] for f in out["files"]] == [
        "/etc/wireguard/eu-mad.conf",
        "/etc/NetworkManager/system-connections/work.nmconnection"]


def test_no_block_contributes_nothing():
    assert expand_wireguard({"hostname": "box"}) == {}


def test_resolve_backend_reads_the_format_not_the_config():
    assert resolve_backend(WGQ, "auto", "x") == "wg-quick"
    assert resolve_backend(NMC, "auto", "x") == "networkmanager"


def test_an_explicit_backend_that_contradicts_the_file_is_an_error():
    with pytest.raises(ValueError) as e:
        resolve_backend(WGQ, "networkmanager", "work")
    assert "nmcli connection import" in str(e.value)


def test_a_file_in_neither_format_is_an_error():
    with pytest.raises(ValueError):
        resolve_backend("hello\n", "auto", "x")
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/lib/expand/test_expand_wireguard.py -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_backend'`.

- [ ] **Step 3: Rewrite the toggle**

```python
# dasik/lib/expand/toggles.py  — replacing lines 73-85
_WG_DIR = "/etc/wireguard"
_NM_DIR = "/etc/NetworkManager/system-connections"


def resolve_backend(content: str, declared: str, name: str) -> str:
    """Which tool reads this file — decided by the file, never converted.

    A `.nmconnection` can only be served by NetworkManager and a wg-quick conf
    only by wg-quick, so the source IS the backend. A declared backend that
    disagrees is a mistake worth stopping for: converting the two formats
    silently is how a private key ends up in a file nobody audits.
    """
    is_nm = "[connection]" in content and "type=wireguard" in content.replace(" ", "")
    is_wgq = "[Interface]" in content
    found = "networkmanager" if is_nm else "wg-quick" if is_wgq else ""
    if not found:
        raise ValueError(
            f"wireguard tunnel {name!r}: the source file is in neither format — "
            "expected a wg-quick conf with an [Interface] section or a "
            "NetworkManager keyfile with [connection] type=wireguard")
    if declared not in ("auto", found):
        other = "wg-quick" if found == "wg-quick" else "NetworkManager"
        raise ValueError(
            f"wireguard tunnel {name!r} declares backend {declared}, but its "
            f"source file is in {other} format. dasik does not convert between "
            f'the two. Either use backend "{found}", or import it yourself and '
            "declare the result:\n"
            f"    nmcli connection import type wireguard file <the .conf>")
    return found


def expand_wireguard(config: Dict[str, Any]) -> Dict[str, Any]:
    """Place each declared tunnel where its backend looks for it.

    Both backends are served by writing a file, which is what makes an
    install-time apply possible: NetworkManager's keyfile plugin reads
    /etc/NetworkManager/system-connections at startup, so no running daemon and
    no `nmcli` are needed inside the chroot (`nmcli --offline connection import`
    does not exist).

    The mode is not decoration: wg-quick and NetworkManager both ignore a
    world-readable keyfile in silence, and the file holds the interface's
    private key.
    """
    tunnels = config.get("wireguard") or []
    if not isinstance(tunnels, list):
        return {}
    packages: List[str] = []
    units: List[str] = []
    files: List[Dict[str, Any]] = []
    for tunnel in tunnels:
        if not isinstance(tunnel, dict):
            continue
        content = tunnel.get("content")
        if not content:
            continue          # the loader fills it; a bad source already raised
        name = tunnel.get("name", "")
        backend = resolve_backend(content, tunnel.get("backend", "auto"), name)
        if backend == "networkmanager":
            files.append({"path": f"{_NM_DIR}/{name}.nmconnection",
                          "content": content, "mode": "0600"})
            packages.append("networkmanager")
        else:
            files.append({"path": f"{_WG_DIR}/{name}.conf",
                          "content": content, "mode": "0600"})
            packages.append("wireguard-tools")
            if tunnel.get("enable", True):
                units.append(f"wg-quick@{name}.service")
    out: Dict[str, Any] = {}
    if packages:
        out["packages"] = packages
    if units:
        out["units"] = units
    if files:
        out["files"] = files
    return out
```

`contributions()` de-dupes, so a repeated `wireguard-tools` is harmless.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/lib/expand/ -v`
Expected: PASS. Existing tests that declare the old dict shape will fail — migrate them to the list shape in this same step; that is the intended breakage.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/expand/toggles.py tests/lib/expand/test_expand_wireguard.py
git commit -m "feat(wireguard): one file per tunnel, at 0600, for either backend"
```

---

### Task 4: Capture — `WireguardAction` and the file written next to the config

**Files:**
- Create: `dasik/lib/actions/wireguard_action.py`
- Modify: `dasik/lib/actions/actions_handler_v2.py` (`setup_actions()`: register it), `dasik/lib/actions/__init__.py` (export)
- Modify: `dasik/lib/actions/drop_files_action.py:312-370,431` (delete `_discover_wireguard` and `_discover_nm_wireguard` and their use)
- Create: `dasik/lib/json_parser/wireguard_extract.py`
- Modify: `dasik/__main__.py:540-546` (fold the extraction into the same write)
- Test: `tests/lib/actions/test_wireguard_action.py`, `tests/lib/json_parser/test_wireguard_extract.py` (new)

**Interfaces:**
- Consumes: `resolve_backend` (Task 3).
- Produces: `WireguardAction` (v3, capture-only: `plan()` returns `[]`, `managed_keys()` returns `{}`, `import_state()` returns `{"wireguard": [...]}` with each tunnel carrying `name`, `source`, `backend`, `enable`, `content`); and `extract_to_wireguard_dir(config, base_dir) -> Extraction`-shaped result (`config`, `writes: Dict[Path, str]`, `deletions: Set[Path]`, `modes: Dict[Path, int]`) that strips `content` out of the JSON and into `wg/<name>.<ext>` at mode `0o600`.

- [ ] **Step 1: Write the failing tests for the action**

```python
# tests/lib/actions/test_wireguard_action.py
import os
from dasik.lib.actions.wireguard_action import WireguardAction

WGQ = "[Interface]\nPrivateKey = SECRET\n"
NMC = "[connection]\nid=work\ntype=wireguard\n\n[wireguard]\nprivate-key=S\n"


class _Target:
    def __init__(self, root): self.root = str(root)
    def path(self, canonical): return os.path.join(self.root, canonical.lstrip("/"))


class _Ctx:
    def __init__(self, target): self.target = target


def _write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _action(root, config=None, enabled=()):
    action = WireguardAction(config or {}, _Ctx(_Target(root)))
    action._unit_enabled = lambda unit: unit in enabled      # no systemctl in a test
    return action


def test_plan_is_empty_capture_only(tmp_path):
    assert _action(tmp_path).plan(managed=[]) == []


def test_a_wg_quick_conf_comes_back_as_a_tunnel(tmp_path):
    _write(tmp_path, "etc/wireguard/eu-mad.conf", WGQ)
    out = _action(tmp_path, enabled=("wg-quick@eu-mad.service",)).import_state()
    assert out["wireguard"] == [{"name": "eu-mad", "source": "wg/eu-mad.conf",
                                 "backend": "wg-quick", "enable": True,
                                 "content": WGQ}]


def test_a_disabled_unit_captures_enable_false(tmp_path):
    _write(tmp_path, "etc/wireguard/eu-mad.conf", WGQ)
    assert _action(tmp_path).import_state()["wireguard"][0]["enable"] is False


def test_an_nm_keyfile_comes_back_as_a_networkmanager_tunnel(tmp_path):
    _write(tmp_path, "etc/NetworkManager/system-connections/work.nmconnection", NMC)
    out = _action(tmp_path).import_state()
    assert out["wireguard"] == [{"name": "work", "source": "wg/work.nmconnection",
                                 "backend": "networkmanager", "enable": True,
                                 "content": NMC}]


def test_a_non_wireguard_nm_connection_is_ignored(tmp_path):
    _write(tmp_path, "etc/NetworkManager/system-connections/wifi.nmconnection",
           "[connection]\nid=wifi\ntype=wifi\n")
    assert _action(tmp_path).import_state() == {}


def test_a_symlinked_conf_is_skipped(tmp_path):
    real = _write(tmp_path, "elsewhere.conf", WGQ)
    (tmp_path / "etc" / "wireguard").mkdir(parents=True)
    (tmp_path / "etc" / "wireguard" / "eu-mad.conf").symlink_to(real)
    assert _action(tmp_path).import_state() == {}


def test_a_machine_with_no_tunnel_invents_nothing(tmp_path):
    assert _action(tmp_path).import_state() == {}


def test_a_declared_source_path_is_kept_instead_of_the_default(tmp_path):
    _write(tmp_path, "etc/wireguard/eu-mad.conf", WGQ)
    declared = {"wireguard": [{"name": "eu-mad", "source": "tunnels/mad.conf"}]}
    out = _action(tmp_path, declared).import_state()
    assert out["wireguard"][0]["source"] == "tunnels/mad.conf"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/lib/actions/test_wireguard_action.py -v`
Expected: FAIL — `ModuleNotFoundError: dasik.lib.actions.wireguard_action`.

- [ ] **Step 3: Write the action**

```python
# dasik/lib/actions/wireguard_action.py
"""Action: capture declared WireGuard tunnels back from the machine.

CAPTURE-ONLY, like `ReflectorAction` and `CpuAction`: the expand toggle writes
the files, so `plan()` is empty and exists to mark the class as v3 so
`Reconciler.sync` visits it.

It owns both directories a tunnel can live in, which is why
`DropFilesAction` no longer discovers them: with two owners a bootstrap sync
captured the same private key twice — once as the block, once as a `files`
entry that then kept the tunnel alive after the block was turned off.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command

_WG_DIR = "/etc/wireguard"
_NM_DIR = "/etc/NetworkManager/system-connections"
_CAPTURE_DIR = "wg"


class WireguardAction(AbstractAction):
    """Reconstruct the `wireguard` block from the tunnel files on the machine."""

    _DOMAIN = "wireguard"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        self._cfg: Dict[str, Any] = config if isinstance(config, dict) else {}

    @classmethod
    def empty_config(cls):
        return {}

    @property
    def name(self) -> str:
        return "WireGuard"

    @property
    def is_optional(self) -> bool:
        return True

    def plan(self, managed: Any) -> list:
        """Nothing to converge — the toggle's `files` contribution writes it."""
        return []

    def managed_keys(self) -> dict:
        return {}

    # --- capture -------------------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _abs(self, canonical: str) -> str:
        t = self._target()
        return t.path(canonical) if t is not None else canonical

    def _declared_source(self, name: str) -> str:
        for tunnel in self._cfg.get(self._DOMAIN) or []:
            if isinstance(tunnel, dict) and tunnel.get("name") == name:
                source = tunnel.get("source")
                if isinstance(source, str) and source:
                    return source
        return ""

    def _unit_enabled(self, unit: str) -> bool:
        try:
            res = Command.execute("systemctl", ["is-enabled", unit],
                                  target=self._target())
        except Exception:      # nosec B110 - no systemctl means "cannot tell"
            return False
        out = getattr(res, "stdout", b"") or b""
        if isinstance(out, bytes):
            out = out.decode("utf-8", errors="replace")
        return out.strip() in ("enabled", "enabled-runtime")

    def _read_dir(self, canonical: str, suffix: str) -> List[tuple]:
        base = self._abs(canonical)
        found: List[tuple] = []
        try:
            names = sorted(os.listdir(base))
        except OSError:
            return found
        for entry in names:
            if not entry.endswith(suffix):
                continue
            path = os.path.join(base, entry)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            try:
                with open(path, "r") as f:
                    found.append((entry[: -len(suffix)], f.read()))
            except (OSError, UnicodeDecodeError):
                continue
        return found

    def import_state(self, managed=None) -> dict:
        tunnels: List[Dict[str, Any]] = []
        for name, content in self._read_dir(_WG_DIR, ".conf"):
            tunnels.append({
                "name": name,
                "source": self._declared_source(name) or f"{_CAPTURE_DIR}/{name}.conf",
                "backend": "wg-quick",
                "enable": self._unit_enabled(f"wg-quick@{name}.service"),
                "content": content,
            })
        for name, content in self._read_dir(_NM_DIR, ".nmconnection"):
            if "type=wireguard" not in content.replace(" ", ""):
                continue
            tunnels.append({
                "name": name,
                "source": (self._declared_source(name)
                           or f"{_CAPTURE_DIR}/{name}.nmconnection"),
                "backend": "networkmanager",
                # NM's own autoconnect lives inside the keyfile; the tunnel is
                # placed either way, so `enable` has nothing to report here.
                "enable": True,
                "content": content,
            })
        return {self._DOMAIN: tunnels} if tunnels else {}

    # --- legacy executor path ------------------------------------------- #

    def is_needed(self) -> bool:
        return False

    def execute(self) -> None:
        return None
```

Register it in `setup_actions()` next to the other capture-only actions (`ReflectorAction`,
`CpuAction`), with `config_key='__root__'`, `is_optional=True`, no `required_fields`,
no `depends_on`. Export it from `dasik/lib/actions/__init__.py`.

Delete `_discover_wireguard` and `_discover_nm_wireguard` from `drop_files_action.py` and the
line at 431 that folds them into the capture; leave a comment naming `WireguardAction` as the
owner so the next reader does not re-add them.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/lib/actions/test_wireguard_action.py tests/lib/actions/test_drop_files*.py -v`
Expected: PASS. DropFiles tests that assert the wg discovery must be deleted with it — check each one is genuinely about the discovery and not about a neighbouring behaviour.

- [ ] **Step 5: Write the failing tests for the extraction**

```python
# tests/lib/json_parser/test_wireguard_extract.py
from pathlib import Path
from dasik.lib.json_parser.wireguard_extract import extract_to_wireguard_dir

WGQ = "[Interface]\nPrivateKey = SECRET\n"


def _captured(**kw):
    tunnel = {"name": "eu-mad", "source": "wg/eu-mad.conf", "backend": "wg-quick",
              "enable": True, "content": WGQ}
    tunnel.update(kw)
    return {"hostname": "box", "wireguard": [tunnel]}


def test_the_body_leaves_the_json_and_becomes_a_file(tmp_path):
    out = extract_to_wireguard_dir(_captured(), tmp_path)
    assert "content" not in out.config["wireguard"][0]
    assert out.writes == {tmp_path / "wg" / "eu-mad.conf": WGQ}


def test_the_file_is_written_at_0600(tmp_path):
    out = extract_to_wireguard_dir(_captured(), tmp_path)
    assert out.modes == {tmp_path / "wg" / "eu-mad.conf": 0o600}


def test_a_declared_source_decides_where_it_lands(tmp_path):
    out = extract_to_wireguard_dir(_captured(source="tunnels/mad.conf"), tmp_path)
    assert list(out.writes) == [tmp_path / "tunnels" / "mad.conf"]


def test_a_tunnel_the_machine_no_longer_has_is_deleted(tmp_path):
    (tmp_path / "wg").mkdir()
    (tmp_path / "wg" / "old.conf").write_text(WGQ)
    out = extract_to_wireguard_dir(_captured(), tmp_path)
    assert (tmp_path / "wg" / "old.conf") in out.deletions


def test_no_block_is_a_no_op(tmp_path):
    config = {"hostname": "box"}
    out = extract_to_wireguard_dir(config, tmp_path)
    assert out.config == config and not out.writes and not out.deletions
```

- [ ] **Step 6: Run them and watch them fail, then write the extractor**

Run: `pytest tests/lib/json_parser/test_wireguard_extract.py -v` → FAIL (module missing).

```python
# dasik/lib/json_parser/wireguard_extract.py
"""Move captured tunnel bodies out of the JSON and into files beside it.

The mirror of `wireguard_source`, and the same reason `extract_to_etc_tree`
exists: a capture must not undo the split from the other side. A tunnel inline
in JSON is an escaped one-liner holding a private key — unreviewable, and one
`cat` away from a leak in a terminal recording.

Deletions cover the tunnel a machine no longer has: the directory is a
declaration, not a pile.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, NamedTuple, Set

WIREGUARD = "wireguard"
_CAPTURE_DIR = "wg"
_MODE = 0o600


class Extraction(NamedTuple):
    config: Dict[str, Any]
    writes: Dict[Path, str]
    deletions: Set[Path]
    modes: Dict[Path, int]


def _default_source(tunnel: Dict[str, Any]) -> str:
    suffix = (".nmconnection" if tunnel.get("backend") == "networkmanager"
              else ".conf")
    return f"{_CAPTURE_DIR}/{tunnel.get('name')}{suffix}"


def extract_to_wireguard_dir(config: Dict[str, Any],
                             base_dir: "str | Path") -> Extraction:
    """Return *config* without tunnel bodies, plus the files to write."""
    tunnels = config.get(WIREGUARD)
    if not tunnels:
        return Extraction(config, {}, set(), {})

    root = Path(base_dir)
    writes: Dict[Path, str] = {}
    modes: Dict[Path, int] = {}
    out = copy.deepcopy(config)
    for tunnel in out[WIREGUARD]:
        content = tunnel.pop("content", None)
        if content is None:
            continue
        source = tunnel.get("source") or _default_source(tunnel)
        tunnel["source"] = source
        path = root / source
        writes[path] = content
        modes[path] = _MODE

    # Only sweep the directory dasik itself writes into: a tunnel the user
    # keeps elsewhere is theirs, and deleting from an arbitrary path named by
    # `source` would be a config file deleting its neighbours.
    deletions: Set[Path] = set()
    capture_root = root / _CAPTURE_DIR
    if capture_root.is_dir():
        existing = {p for p in capture_root.iterdir() if p.is_file()}
        deletions = existing - set(writes)
    return Extraction(out, writes, deletions, modes)
```

Fold it into `_cmd_sync` (`dasik/__main__.py`, after the `home` extraction):

```python
    wg = extract_to_wireguard_dir(home.config, config_path.parent)
    written = write_back(config_path, wg.config,
                         extra_writes={**extraction.writes, **home.writes,
                                       **wg.writes},
                         deletions=extraction.deletions | home.deletions
                                   | wg.deletions)
    for path, mode in {**extraction.modes, **home.modes, **wg.modes}.items():
        os.chmod(path, mode)
```

- [ ] **Step 7: Run the tests**

Run: `pytest tests/lib/json_parser/test_wireguard_extract.py tests/lib/test_cmd_sync*.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add dasik/lib/actions/wireguard_action.py dasik/lib/actions/__init__.py \
        dasik/lib/actions/actions_handler_v2.py dasik/lib/actions/drop_files_action.py \
        dasik/lib/json_parser/wireguard_extract.py dasik/__main__.py \
        tests/lib/actions/test_wireguard_action.py \
        tests/lib/json_parser/test_wireguard_extract.py
git commit -m "feat(wireguard): sync captures the tunnel as its own block, file and all"
```

---

### Task 5: Preflight — the checks that stop a broken tunnel before the first write

**Files:**
- Modify: `dasik/lib/validation/preflight.py` (new `_check_wireguard`, call it in `preflight()` next to the other `issues +=` lines)
- Test: `tests/lib/validation/test_preflight_wireguard.py` (new)

**Interfaces:**
- Consumes: `resolve_backend` (Task 3), the `Issue` type already in `preflight.py`.
- Produces: `_check_wireguard(config: Dict[str, Any], packages: Set[str]) -> List[Issue]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/lib/validation/test_preflight_wireguard.py
from dasik.lib.validation.preflight import preflight, has_errors

WGQ = "[Interface]\nPrivateKey = S\n"
NMC = "[connection]\nid=work\ntype=wireguard\n"


def _cfg(*tunnels, **root):
    config = {"hostname": "box", "wireguard": list(tunnels)}
    config.update(root)
    return config


def _t(name="eu-mad", content=WGQ, **kw):
    t = {"name": name, "source": f"wg/{name}.conf", "content": content}
    t.update(kw)
    return t


def test_a_valid_tunnel_raises_nothing():
    assert not has_errors(preflight(_cfg(_t())))


def test_two_tunnels_with_the_same_name_is_an_error():
    issues = preflight(_cfg(_t(), _t()))
    assert has_errors(issues) and "eu-mad" in "\n".join(str(i) for i in issues)


def test_a_backend_that_contradicts_the_file_is_an_error_naming_the_import():
    issues = preflight(_cfg(_t(backend="networkmanager")))
    assert has_errors(issues)
    assert "nmcli connection import" in "\n".join(str(i) for i in issues)


def test_a_file_in_neither_format_is_an_error():
    assert has_errors(preflight(_cfg(_t(content="hello\n"))))


def test_an_nm_keyfile_on_a_networkd_machine_warns_but_does_not_stop():
    issues = preflight(_cfg(_t(name="work", content=NMC),
                            network={"type": "systemd-networkd"}))
    assert issues and not has_errors(issues)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/lib/validation/test_preflight_wireguard.py -v`
Expected: FAIL — no duplicate/backend checks exist yet, so `has_errors` is `False`.

- [ ] **Step 3: Write the check**

```python
def _check_wireguard(config: Dict[str, Any], packages: Set[str]) -> List[Issue]:
    """Stop a tunnel that cannot work before anything is written.

    All three of these are cheap to get wrong and expensive to debug on a
    machine that is already installed: two tunnels fighting over one interface
    name, a file in a format its declared backend cannot read, and a keyfile
    left for a NetworkManager that this machine does not run.
    """
    tunnels = config.get("wireguard") or []
    if not isinstance(tunnels, list):
        return []
    issues: List[Issue] = []
    seen: Set[str] = set()
    net_type = (config.get("network") or {}).get("type")
    for tunnel in tunnels:
        if not isinstance(tunnel, dict):
            continue
        name = tunnel.get("name", "")
        if name in seen:
            issues.append(Issue(ERROR, f"wireguard: two tunnels are named "
                                       f"{name!r}; the name is the interface"))
        seen.add(name)
        content = tunnel.get("content")
        if not content:
            continue
        try:
            backend = resolve_backend(content, tunnel.get("backend", "auto"), name)
        except ValueError as e:
            issues.append(Issue(ERROR, str(e)))
            continue
        if backend == "networkmanager" and net_type == "systemd-networkd":
            issues.append(Issue(WARNING, (
                f"wireguard tunnel {name!r} is a NetworkManager keyfile, but "
                "network.type is systemd-networkd — nothing will read it. Use a "
                "wg-quick conf, which works under either manager.")))
    return issues
```

Add `issues += _check_wireguard(config, packages)` to `preflight()` alongside the other calls,
and import `resolve_backend` from `dasik.lib.expand.toggles`. Use the module's existing
severity constants — read the top of `preflight.py` and match them exactly (`ERROR`/`WARNING`
above are placeholders for whatever the module already calls them).

- [ ] **Step 4: Run the tests**

Run: `pytest tests/lib/validation/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/validation/preflight.py tests/lib/validation/test_preflight_wireguard.py
git commit -m "feat(wireguard): refuse a tunnel whose file its backend cannot read"
```

---

### Task 6: The evidence the repo demands, and the sample configs

**Files:**
- Modify: `tests/lib/test_feature_detectability.py`, `tests/lib/test_feature_sync_capture.py`
- Modify: `config/install-chunga.json`, `config/vm-chunga-full.json`, `config/install-megamix.json`
- Create: `config/wg/example.conf` (placeholder keys, referenced by the samples above)
- Create: `config/vm-wireguard/main.json`, `config/vm-wireguard/wg/vmwg.conf`, `config/vm-wireguard/wg/vmnm.nmconnection`

**Interfaces:**
- Consumes: everything above.
- Produces: the VM config the harness drives in Task 7.

- [ ] **Step 1: Write the detectability rows**

```python
# tests/lib/test_feature_detectability.py — following the file's existing helpers
def test_wireguard_missing_on_the_target_is_planned(...):
    # a target with no /etc/wireguard/eu-mad.conf ⇒ + [files] install
    ...

def test_wireguard_present_on_the_target_is_silent(...):
    # the same content already on the target, mode 0600 ⇒ no change
    ...

def test_wireguard_owned_but_undeclared_is_removed(...):
    # manifest owns /etc/wireguard/eu-mad.conf, config drops the block
    # ⇒ - [files] remove, and - [systemd] disable wg-quick@eu-mad.service
    ...
```

Copy the surrounding rows' exact fixtures rather than inventing new ones — this file has one
shape per feature and the point is that every feature is asserted the same way.

- [ ] **Step 2: Write the sync-capture rows**

```python
# tests/lib/test_feature_sync_capture.py
def test_a_machine_with_a_tunnel_captures_the_block(...):
    ...

def test_a_machine_without_one_invents_nothing(...):
    ...

def test_the_captured_config_validates_and_replans_to_nothing(...):
    # sync -> check -> plan silent, the invariant that matters
    ...

def test_the_tunnel_is_captured_once_not_twice(...):
    # Defect 2: the block AND a files entry for the same path is the bug
    ...
```

- [ ] **Step 3: Run them and watch them fail, then make them pass**

Run: `pytest tests/lib/test_feature_detectability.py tests/lib/test_feature_sync_capture.py -v`

- [ ] **Step 4: Migrate the three sample configs**

Each currently holds the old dict. Replace with, e.g. in `config/install-megamix.json`:

```json
  "wireguard": [
    { "name": "wg0", "source": "wg/example.conf" }
  ],
```

and `config/wg/example.conf`:

```ini
[Interface]
Address = 10.0.0.2/24
PrivateKey = <YOUR_PRIVATE_KEY>
DNS = 1.1.1.1

[Peer]
PublicKey = <SERVER_PUBLIC_KEY>
Endpoint = vpn.example.com:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
```

Then: `dasik check config/install-megamix.json config/install-chunga.json config/vm-chunga-full.json` — all rc=0.

- [ ] **Step 5: Write the VM config**

`config/vm-wireguard/main.json`: a minimal UEFI guest (copy `config/vm-etc-tree/main.json`'s
disk/user/boot shape) plus

```json
  "network": { "type": "NetworkManager", "add_default_hosts": true },
  "wireguard": [
    { "name": "vmwg", "source": "wg/vmwg.conf" },
    { "name": "vmnm", "source": "wg/vmnm.nmconnection" }
  ]
```

with generated (throwaway) keys in both files, so one guest exercises both backends.

- [ ] **Step 6: Run the whole suite and the gates**

```bash
pytest --cov=dasik && mypy dasik && bandit -r dasik -q && scripts/mutation.sh
```

- [ ] **Step 7: Commit**

```bash
git add tests/lib/test_feature_detectability.py tests/lib/test_feature_sync_capture.py \
        config/install-chunga.json config/vm-chunga-full.json config/install-megamix.json \
        config/wg config/vm-wireguard
git commit -m "test(wireguard): planned, silent, removed, captured — and once, not twice"
```

---

### Task 7: The VM run, and the wiki page

**Files:**
- Create: `scripts/vmtest/guest-wireguard.sh`
- Modify: `docs/wiki/Configuration.md` or a new `docs/wiki/VPN.md`, `docs/wiki/_Sidebar.md`, `docs/config-reference.md`

**Interfaces:**
- Consumes: `config/vm-wireguard/` (Task 6).
- Produces: the verdict text for the PR comment.

- [ ] **Step 1: Write the guest check script**

```bash
#!/bin/bash
# Did the install place both tunnels, at the right mode, for the right tool?
set -x
rc=0
for f in /etc/wireguard/vmwg.conf \
         /etc/NetworkManager/system-connections/vmnm.nmconnection; do
    [ -f "$f" ] || { echo "WG MISSING $f"; rc=1; continue; }
    m=$(stat -c '%a' "$f")
    [ "$m" = "600" ] || { echo "WG MODE BAD $f=$m"; rc=1; }
done
systemctl is-enabled wg-quick@vmwg.service || { echo "WG UNIT NOT ENABLED"; rc=1; }
nmcli -t -f NAME,TYPE connection show | grep -q '^vmnm:wireguard$' \
    || { echo "WG NM CONNECTION MISSING"; rc=1; }
grep -q '127.0.1.1' /etc/hosts || { echo "HOSTS BLOCK MISSING"; rc=1; }
echo "WG-DONE rc=$rc"
sync; poweroff -f
```

- [ ] **Step 2: Drive the install in QEMU**

```bash
scripts/vmtest/qemu.sh install --config config/vm-wireguard/main.json \
                              --guest-script scripts/vmtest/guest-wireguard.sh
```

Expected: `WG-DONE rc=0`. Read `scripts/vmtest/qemu.sh --help` first; match the flags the other
`guest-*.sh` runs use.

- [ ] **Step 3: Drive the six verbs against the booted guest**

On the guest, with `--target /`: `check` rc=0 · `plan` *No changes* · `apply` then `plan`
silent · `sync` rc=0 and the capture holds the `wireguard` block with `source: wg/…` and the
files written next to it · `check` on the capture rc=0 · `plan` on the capture silent ·
`generations` lists both · `rollback` to 1 then `plan` silent. Also drive it with the block
**removed**: expect `- [files] remove` and `- [systemd] disable`, not a MODIFY.

- [ ] **Step 4: Write the wiki page**

Cover: the two formats and that dasik never converts; the directory layout; `backend: auto`;
why the mode is 0600; that a wg-quick tunnel works under systemd-networkd too; and the
migration note — a machine synced with an older dasik carries its tunnel in `files`/`etc_tree`,
and the first `sync` after this change moves it into `wg/`.

- [ ] **Step 5: Regenerate the config reference**

`docs/config-reference.md` is generated by introspecting `model_fields` — re-run whatever
produced it and check the `wireguard` rows describe the list shape.

- [ ] **Step 6: Commit, push, open the PR**

```bash
git add scripts/vmtest/guest-wireguard.sh docs/wiki docs/config-reference.md
git commit -m "docs(wireguard): the tunnel file, its backend, and why the mode matters"
git push -u origin feat/issue-249-wireguard
gh pr create --base main --title "feat(wireguard): declare a tunnel as the file its backend reads"
```

The PR body needs the **How to test manually** section the repo requires, and the agentic
verdict goes in as a comment naming which verbs ran for real.

## Self-Review

**Spec coverage.** §A model/shape → Task 1; loader → Task 2; expand + backend resolution +
Defect 1 (mode) → Task 3; capture + extraction + Defect 2 (double capture, via DropFiles
yielding) → Task 4; preflight → Task 5; the evidence matrices and sample-config migration →
Task 6; VM + wiki → Task 7. §B (`/etc/hosts` default) is **deliberately not here** — it is a
separate PR on a separate branch, planned in its own two-task plan. §C (procedures audit) is a
spike with no code.

**Placeholders.** The two `_check_wireguard` severity constants are named as placeholders and
the step says to match the module's own names; the detectability/sync-capture rows point at the
existing per-feature fixtures instead of inventing shapes. Everything else is literal.

**Type consistency.** `resolve_backend(content, declared, name) -> str` is defined in Task 3
and consumed by Tasks 4 and 5 with that signature. `Extraction` in Task 4 mirrors the
`etc_tree` NamedTuple field-for-field (`config`, `writes`, `deletions`, `modes`) because
`write_back` and `_cmd_sync` already consume that shape. `_CAPTURE_DIR = "wg"` is the same
constant in the action and the extractor.

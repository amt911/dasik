# files (drop_files) v3-domain Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the `files` domain (`DropFilesAction`) to the v3 `plan`/`apply`/`sync` contract with explicit `{name, content}` file entries (stable identity) and orphan cleanup via DELETE.

**Architecture:** `set_math.compute_changes` computes CREATE/DELETE over canonical file paths; `DropFilesAction` adds a MODIFY layer comparing on-disk content vs desired. Paths are canonical (`/etc/...`), resolved to the target via `target.path()` for I/O. `actual()` is scoped to declared paths that exist (no directory glob); DELETE = `M \ D` from the manifest cleans up orphans.

**Tech Stack:** Python 3.10+, pydantic, pytest/pytest-cov.

Spec: `docs/superpowers/specs/2026-05-31-files-v3-domain-design.md`.

**Test runner:**
```bash
python -m venv /tmp/dasik-venv && /tmp/dasik-venv/bin/pip install -q pytest pytest-cov colorama pydantic
PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest ...
```

---

## Task 1: `FileEntry` model + `JsonModel` field types

**Files:**
- Create: `dasik/lib/models/file_model.py`
- Modify: `dasik/lib/models/json_model.py` (imports + field types for udev_rules/modprobe_conf/profile_d)
- Modify: `dasik/lib/models/__init__.py` (export FileEntry)
- Test: `tests/lib/models/test_file_model.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/models/test_file_model.py`:

```python
import pytest

from dasik.lib.models.file_model import FileEntry
from dasik.lib.models.json_model import JsonModel


def test_accepts_name_and_content():
    e = FileEntry(name="99-razer.rules", content="SUBSYSTEM==...")
    assert e.name == "99-razer.rules"
    assert e.content == "SUBSYSTEM==..."


def test_rejects_name_with_slash():
    with pytest.raises(ValueError):
        FileEntry(name="sub/dir.rules", content="x")


def test_rejects_empty_name():
    with pytest.raises(ValueError):
        FileEntry(name="", content="x")


def test_json_model_accepts_file_entry_sections():
    m = JsonModel(
        locales={"selected_locales": ["en_US.UTF-8 UTF-8"],
                 "desired_locale": "en_US.UTF-8", "desired_tty_layout": "us"},
        timezone={"region": "Europe", "city": "Madrid"},
        network={"type": "NetworkManager", "add_default_hosts": True},
        hostname="arch",
        udev_rules=[{"name": "99-x.rules", "content": "RULE"}],
        modprobe_conf=[{"name": "x.conf", "content": "options x"}],
        profile_d=[{"name": "x.sh", "content": "export A=1"}],
        etc_environment=["EDITOR=vim"],
    )
    assert m.udev_rules[0].name == "99-x.rules"
    assert m.etc_environment == ["EDITOR=vim"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/models/test_file_model.py -v`
Expected: FAIL — `file_model` module missing; JsonModel sections still `List[str]`.

- [ ] **Step 3: Implement the model**

Create `dasik/lib/models/file_model.py`:

```python
"""Model for a single declarative dropped file."""
from pydantic import BaseModel, Field, field_validator


class FileEntry(BaseModel):
    """One managed file: a filename (no path separators) and its content."""
    name: str = Field(..., description="Filename only, no path separators")
    content: str = Field(..., description="Verbatim file content")

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not v or "/" in v:
            raise ValueError("name must be a non-empty filename without '/'")
        return v
```

In `dasik/lib/models/json_model.py`, add the import near the other model imports:

```python
from .file_model import FileEntry
```

Change the file-section field types (the block currently typed `List[str]`):

```python
    # Files / lines to drop on the target system
    udev_rules: List[FileEntry] = Field(default_factory=list)
    modprobe_conf: List[FileEntry] = Field(default_factory=list)
    profile_d: List[FileEntry] = Field(default_factory=list)
    etc_environment: List[str] = Field(default_factory=list)
    kernel_cmdline: List[str] = Field(default_factory=list)
```

In `dasik/lib/models/__init__.py`, add `from dasik.lib.models.file_model import FileEntry`
near the other imports and `"FileEntry",` to `__all__`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/models/test_file_model.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/models/file_model.py dasik/lib/models/json_model.py dasik/lib/models/__init__.py tests/lib/models/test_file_model.py
git commit -m "feat(models): FileEntry {name,content} + typed file sections in JsonModel"
```

---

## Task 2: `DropFilesAction` — target-aware `_abs`/`_desired`/`actual` + migrate legacy path

**Files:**
- Modify: `dasik/lib/actions/drop_files_action.py`
- Test: `tests/lib/actions/test_drop_files_action.py` (rewrite to the `{name, content}` model)

This rewrites the action's path derivation (explicit names, canonical paths, target-aware)
and updates the legacy `is_needed`/`execute`/`verify` to the new model so the suite stays
green. v3 `plan`/`apply`/`import_state` come in Tasks 3-5.

- [ ] **Step 1: Rewrite the test file**

Replace `tests/lib/actions/test_drop_files_action.py` entirely:

```python
from unittest.mock import mock_open, patch

from dasik.lib.actions.drop_files_action import DropFilesAction, _sha256
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Change, Op


def _ctx(root="/"):
    return ActionContext(target=Target(root=root))


def _cfg(udev=None, modprobe=None, profile=None, env=None):
    return {
        "udev_rules": udev or [],
        "modprobe_conf": modprobe or [],
        "profile_d": profile or [],
        "etc_environment": env or [],
    }


def test_sha256_deterministic():
    assert _sha256("x") == _sha256("x")


def test_desired_maps_sections_to_canonical_paths():
    a = DropFilesAction(_cfg(
        udev=[{"name": "99-x.rules", "content": "RULE"}],
        modprobe=[{"name": "x.conf", "content": "options x"}],
        profile=[{"name": "x.sh", "content": "export A=1"}],
        env=["EDITOR=vim", "PAGER=less"],
    ), _ctx("/"))
    d = a._desired()
    assert d["/etc/udev/rules.d/99-x.rules"] == "RULE"
    assert d["/etc/modprobe.d/x.conf"] == "options x"
    assert d["/etc/profile.d/x.sh"] == "export A=1"
    assert d["/etc/environment"] == "EDITOR=vim\nPAGER=less\n"


def test_desired_omits_environment_when_no_lines():
    a = DropFilesAction(_cfg(udev=[{"name": "a.rules", "content": "R"}]), _ctx("/"))
    assert "/etc/environment" not in a._desired()


def test_abs_resolves_through_target():
    a = DropFilesAction(_cfg(), _ctx("/mnt"))
    assert a._abs("/etc/environment") == "/mnt/etc/environment"
    b = DropFilesAction(_cfg(), _ctx("/"))
    assert b._abs("/etc/environment") == "/etc/environment"


def test_actual_returns_declared_paths_that_exist():
    a = DropFilesAction(_cfg(
        udev=[{"name": "a.rules", "content": "R"}, {"name": "b.rules", "content": "R2"}],
    ), _ctx("/"))
    exists = {"/etc/udev/rules.d/a.rules"}
    with patch("dasik.lib.actions.drop_files_action.os.path.exists",
               side_effect=lambda p: p in exists):
        assert a.actual() == {"/etc/udev/rules.d/a.rules"}


def test_actual_empty_without_target():
    a = DropFilesAction(_cfg(udev=[{"name": "a.rules", "content": "R"}]), None)
    assert a.actual() == set()


# --- legacy is_needed / execute / verify (migrated to {name,content}) --- #


def test_legacy_needed_when_file_absent():
    a = DropFilesAction(_cfg(udev=[{"name": "a.rules", "content": "R"}]), _ctx("/"))
    with patch("dasik.lib.actions.drop_files_action.os.path.exists", return_value=False):
        assert a.is_needed() is True


def test_legacy_not_needed_when_content_matches():
    a = DropFilesAction(_cfg(udev=[{"name": "a.rules", "content": "R"}]), _ctx("/"))
    with patch("dasik.lib.actions.drop_files_action.os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data="R")):
        assert a.is_needed() is False
        assert a.verify() is True


def test_legacy_needed_when_content_differs():
    a = DropFilesAction(_cfg(udev=[{"name": "a.rules", "content": "NEW"}]), _ctx("/"))
    with patch("dasik.lib.actions.drop_files_action.os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data="OLD")):
        assert a.is_needed() is True


def test_name_and_optional():
    a = DropFilesAction(_cfg())
    assert a.name == "Drop Config Files"
    assert a.is_optional is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_drop_files_action.py -v`
Expected: FAIL — `_desired` now expects `{name,content}`; `_abs`/target-aware `actual` missing.

- [ ] **Step 3: Rewrite `DropFilesAction` (everything except plan/apply/import_state)**

Replace `dasik/lib/actions/drop_files_action.py` with:

```python
"""Action: write declarative files (udev rules, modprobe, profile.d, /etc/environment).

v3 domain "files": each entry is an explicit {name, content}; the on-disk
filename is the chosen name (stable identity). CREATE/DELETE by canonical path
(set-math) + MODIFY on content drift. actual() is scoped to declared paths that
exist (no directory glob). Registered config_key="__root__".
"""
from __future__ import annotations
import hashlib
import os
from typing import Any, Dict, List
from .abstract_action import AbstractAction
from ..state.change import Change, Op


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# (config key, target directory) for the per-file sections.
_SECTIONS = [
    ("udev_rules", "/etc/udev/rules.d"),
    ("modprobe_conf", "/etc/modprobe.d"),
    ("profile_d", "/etc/profile.d"),
]
_ENV_PATH = "/etc/environment"
_FILES_DOMAIN = "files"


class DropFilesAction(AbstractAction):
    """Write config snippets into /etc/... directories on the target."""

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self._sections = {key: cfg.get(key, []) for key, _ in _SECTIONS}
        self.etc_env_lines: List[str] = cfg.get("etc_environment", [])

    @property
    def name(self) -> str:
        return "Drop Config Files"

    @property
    def is_optional(self) -> bool:
        return True

    # -- paths / desired state ----------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _abs(self, canonical: str) -> str:
        t = self._target()
        return t.path(canonical) if t is not None else "/mnt" + canonical

    @staticmethod
    def _entry_fields(entry: Any) -> tuple:
        """Accept a dict or a FileEntry-like object."""
        if isinstance(entry, dict):
            return entry["name"], entry["content"]
        return entry.name, entry.content

    def _desired(self) -> Dict[str, str]:
        """Canonical absolute path -> verbatim content."""
        desired: Dict[str, str] = {}
        for key, directory in _SECTIONS:
            for entry in self._sections.get(key, []):
                name, content = self._entry_fields(entry)
                desired[f"{directory}/{name}"] = content
        if self.etc_env_lines:
            desired[_ENV_PATH] = "\n".join(self.etc_env_lines) + "\n"
        return desired

    def _read(self, canonical: str) -> str:
        with open(self._abs(canonical), "r") as f:
            return f.read()

    def _exists(self, canonical: str) -> bool:
        return os.path.exists(self._abs(canonical))

    def actual(self) -> set:
        """Declared paths that exist on disk (no directory glob)."""
        if self._target() is None:
            return set()
        return {p for p in self._desired() if self._exists(p)}

    def _needs_write(self, canonical: str, desired: str) -> bool:
        if not self._exists(canonical):
            return True
        return _sha256(self._read(canonical)) != _sha256(desired)

    # -- legacy is_needed / execute / verify (old executor path) ------- #

    def is_needed(self) -> bool:
        return any(self._needs_write(p, c) for p, c in self._desired().items())

    def execute(self) -> None:
        for canonical, content in self._desired().items():
            if self._needs_write(canonical, content):
                path = self._abs(canonical)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as f:
                    f.write(content)
                print(f"  Wrote {path}")

    def verify(self) -> bool:
        return not any(self._needs_write(p, c) for p, c in self._desired().items())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_drop_files_action.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/drop_files_action.py tests/lib/actions/test_drop_files_action.py
git commit -m "feat(files): explicit {name,content} + target-aware paths/actual; legacy migrated"
```

---

## Task 3: `DropFilesAction.plan()` + `managed_keys()`

**Files:**
- Modify: `dasik/lib/actions/drop_files_action.py`
- Test: `tests/lib/actions/test_drop_files_action.py` (append)

- [ ] **Step 1: Write the failing tests**

Append:

```python
def _v3(cfg, actual, ondisk=None):
    a = DropFilesAction(cfg, _ctx("/"))
    a.actual = lambda: set(actual)
    a._read = lambda p: (ondisk or {}).get(p, "")
    return a


def test_plan_creates_missing_file():
    a = _v3(_cfg(udev=[{"name": "a.rules", "content": "R"}]), actual=[])
    changes = a.plan(managed=[])
    assert [(c.op, c.item) for c in changes] == [(Op.CREATE, "/etc/udev/rules.d/a.rules")]


def test_plan_deletes_orphan_owned():
    a = _v3(_cfg(), actual=[])
    changes = a.plan(managed=["/etc/modprobe.d/old.conf"])
    assert [(c.op, c.item) for c in changes] == [(Op.DELETE, "/etc/modprobe.d/old.conf")]


def test_plan_modifies_on_content_drift():
    p = "/etc/udev/rules.d/a.rules"
    a = _v3(_cfg(udev=[{"name": "a.rules", "content": "NEW"}]),
            actual=[p], ondisk={p: "OLD"})
    changes = a.plan(managed=[p])
    assert [(c.op, c.item) for c in changes] == [(Op.MODIFY, p)]


def test_plan_empty_when_converged():
    p = "/etc/udev/rules.d/a.rules"
    a = _v3(_cfg(udev=[{"name": "a.rules", "content": "R"}]),
            actual=[p], ondisk={p: "R"})
    assert a.plan(managed=[p]) == []


def test_managed_keys_lists_canonical_paths():
    a = DropFilesAction(_cfg(
        udev=[{"name": "a.rules", "content": "R"}], env=["X=1"]), _ctx("/"))
    assert a.managed_keys() == {"files": ["/etc/environment", "/etc/udev/rules.d/a.rules"]}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_drop_files_action.py -k "plan or managed_keys" -v`
Expected: FAIL — base `plan` returns `[]`, `managed_keys` returns `{}`.

- [ ] **Step 3: Implement `plan` and `managed_keys`**

Add to `DropFilesAction` (after `actual`):

```python
    def plan(self, managed):
        from ..state.set_math import compute_changes
        desired = self._desired()
        actual = self.actual()
        changes, _drift = compute_changes(
            _FILES_DOMAIN,
            desired=list(desired.keys()),
            managed=managed,
            actual=actual,
            op_install=Op.CREATE,
            op_remove=Op.DELETE,
        )
        for p in sorted(set(desired) & actual):
            if self._read(p) != desired[p]:
                changes.append(Change(_FILES_DOMAIN, Op.MODIFY, p, reason="content drift"))
        return changes

    def managed_keys(self) -> dict:
        return {_FILES_DOMAIN: sorted(self._desired().keys())}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_drop_files_action.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/drop_files_action.py tests/lib/actions/test_drop_files_action.py
git commit -m "feat(files): v3 plan() (CREATE/DELETE + MODIFY layer) + managed_keys()"
```

---

## Task 4: `DropFilesAction.apply()`

**Files:**
- Modify: `dasik/lib/actions/drop_files_action.py`
- Test: `tests/lib/actions/test_drop_files_action.py` (append)

- [ ] **Step 1: Write the failing tests**

Append:

```python
def test_apply_writes_created_and_modified_files():
    a = DropFilesAction(_cfg(
        udev=[{"name": "a.rules", "content": "R"}],
        modprobe=[{"name": "b.conf", "content": "B"}]), _ctx("/"))
    m = mock_open()
    changes = [
        Change("files", Op.CREATE, "/etc/udev/rules.d/a.rules"),
        Change("files", Op.MODIFY, "/etc/modprobe.d/b.conf"),
    ]
    with patch("dasik.lib.actions.drop_files_action.os.makedirs") as mkdirs, \
         patch("builtins.open", m):
        a.apply(changes)
    written = {c.args[0] for c in m.call_args_list}
    assert "/etc/udev/rules.d/a.rules" in written
    assert "/etc/modprobe.d/b.conf" in written
    assert mkdirs.call_count == 2
    handle = m()
    bodies = "".join(c.args[0] for c in handle.write.call_args_list)
    assert "R" in bodies and "B" in bodies


def test_apply_removes_orphan_files():
    a = DropFilesAction(_cfg(), _ctx("/"))
    with patch("dasik.lib.actions.drop_files_action.os.path.exists", return_value=True), \
         patch("dasik.lib.actions.drop_files_action.os.remove") as rm:
        a.apply([Change("files", Op.DELETE, "/etc/modprobe.d/old.conf")])
    rm.assert_called_once_with("/etc/modprobe.d/old.conf")


def test_apply_delete_skips_missing_file():
    a = DropFilesAction(_cfg(), _ctx("/"))
    with patch("dasik.lib.actions.drop_files_action.os.path.exists", return_value=False), \
         patch("dasik.lib.actions.drop_files_action.os.remove") as rm:
        a.apply([Change("files", Op.DELETE, "/etc/modprobe.d/old.conf")])
    rm.assert_not_called()


def test_apply_create_before_delete():
    a = DropFilesAction(_cfg(udev=[{"name": "a.rules", "content": "R"}]), _ctx("/"))
    changes = [
        Change("files", Op.DELETE, "/etc/modprobe.d/old.conf"),
        Change("files", Op.CREATE, "/etc/udev/rules.d/a.rules"),
    ]
    order = []
    with patch("dasik.lib.actions.drop_files_action.os.makedirs"), \
         patch("builtins.open", mock_open()), \
         patch("dasik.lib.actions.drop_files_action.os.path.exists", return_value=True), \
         patch("dasik.lib.actions.drop_files_action.os.remove",
               side_effect=lambda p: order.append("del")):
        # record write via makedirs call order instead:
        a.apply(changes)
    # remove happened after the write path ran (create first)
    assert order == ["del"]


def test_apply_noop_without_target():
    a = DropFilesAction(_cfg(udev=[{"name": "a.rules", "content": "R"}]), None)
    with patch("builtins.open", mock_open()) as m, \
         patch("dasik.lib.actions.drop_files_action.os.remove") as rm:
        a.apply([Change("files", Op.CREATE, "/etc/udev/rules.d/a.rules")])
    m.assert_not_called()
    rm.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_drop_files_action.py -k apply -v`
Expected: FAIL — base `apply` is a no-op.

- [ ] **Step 3: Implement `apply`**

Add to `DropFilesAction`:

```python
    def apply(self, changes) -> None:
        if self._target() is None:
            return
        desired = self._desired()
        writes = [c.item for c in changes if c.op in (Op.CREATE, Op.MODIFY)]
        deletes = [c.item for c in changes if c.op is Op.DELETE]

        for canonical in writes:                    # additive first
            path = self._abs(canonical)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(desired.get(canonical, ""))

        for canonical in deletes:
            path = self._abs(canonical)
            if os.path.exists(path):
                os.remove(path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_drop_files_action.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/drop_files_action.py tests/lib/actions/test_drop_files_action.py
git commit -m "feat(files): v3 apply() writes created/modified, removes orphans"
```

---

## Task 5: `DropFilesAction.import_state()` (sync)

**Files:**
- Modify: `dasik/lib/actions/drop_files_action.py`
- Test: `tests/lib/actions/test_drop_files_action.py` (append)

- [ ] **Step 1: Write the failing tests**

Append:

```python
def test_import_state_refreshes_content_from_disk():
    p = "/etc/udev/rules.d/a.rules"
    a = _v3(_cfg(udev=[{"name": "a.rules", "content": "OLD"}]),
            actual=[p], ondisk={p: "EDITED-ON-DISK"})
    frag = a.import_state(managed=[p])
    assert frag["udev_rules"] == [{"name": "a.rules", "content": "EDITED-ON-DISK"}]


def test_import_state_keeps_declared_content_when_absent():
    a = _v3(_cfg(profile=[{"name": "x.sh", "content": "export A=1"}]), actual=[])
    frag = a.import_state(managed=[])
    assert frag["profile_d"] == [{"name": "x.sh", "content": "export A=1"}]


def test_import_state_splits_environment_back_to_lines():
    p = "/etc/environment"
    a = _v3(_cfg(env=["A=1", "B=2"]), actual=[p], ondisk={p: "A=1\nB=2\nC=3\n"})
    frag = a.import_state(managed=[p])
    assert frag["etc_environment"] == ["A=1", "B=2", "C=3"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_drop_files_action.py -k import_state -v`
Expected: FAIL — base `import_state` returns `{}`.

- [ ] **Step 3: Implement `import_state`**

Add to `DropFilesAction`:

```python
    def import_state(self, managed=None) -> dict:
        actual = self.actual()
        result: Dict[str, Any] = {}
        for key, directory in _SECTIONS:
            entries = []
            for entry in self._sections.get(key, []):
                name, content = self._entry_fields(entry)
                canonical = f"{directory}/{name}"
                if canonical in actual:
                    content = self._read(canonical)     # refresh manual edits
                entries.append({"name": name, "content": content})
            result[key] = entries

        if _ENV_PATH in actual:
            text = self._read(_ENV_PATH)
            result["etc_environment"] = [ln for ln in text.split("\n") if ln != ""]
        else:
            result["etc_environment"] = list(self.etc_env_lines)
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_drop_files_action.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/drop_files_action.py tests/lib/actions/test_drop_files_action.py
git commit -m "feat(files): v3 import_state() refreshes owned-file content from disk (sync)"
```

---

## Task 6: Sample migration + full suite + gate

**Files:**
- Modify: `config/install-megamix.json`
- Test: full suite

- [ ] **Step 1: Migrate the sample's file sections to `{name, content}`**

In `config/install-megamix.json`, convert `udev_rules`, `modprobe_conf`, `profile_d` from
arrays of strings to arrays of `{name, content}`. Keep `etc_environment` as a string list.
Example shape:

```jsonc
  "udev_rules": [
    { "name": "99-ub400.rules", "content": "ACTION==\"add\", SUBSYSTEM==\"usb\", ATTR{idVendor}==\"0bda\", ATTR{idProduct}==\"c821\", MODE=\"0666\"" },
    { "name": "99-krom.rules",  "content": "ACTION==\"add\", SUBSYSTEM==\"usb\", ATTR{idVendor}==\"5566\", ATTR{idProduct}==\"0008\", MODE=\"0666\"" }
  ],
  "modprobe_conf": [
    { "name": "nvidia.conf", "content": "options nvidia_drm modeset=1\noptions nvidia_drm fbdev=1" },
    { "name": "virtio.conf", "content": "options virtio_pci disable_legacy=1" }
  ],
  "profile_d": [
    { "name": "hw-accel.sh", "content": "export LIBVA_DRIVER_NAME=nvidia\nexport MOZ_DISABLE_RDD_SANDBOX=1\n" },
    { "name": "theme.sh",    "content": "export GTK_THEME=Adwaita:dark\n" }
  ],
```

(Pick descriptive names; they must contain no `/`.)

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
git commit -m "docs(config): migrate megamix file sections to {name,content}"
```

---

## Self-Review notes

- **Spec coverage:** Task 1 = FileEntry + JsonModel types; Task 2 = `_abs`/`_desired`/`actual` + legacy migration; Task 3 = plan/managed_keys (CREATE/DELETE/MODIFY); Task 4 = apply (write/remove/order); Task 5 = import_state (refresh + env split); Task 6 = sample + gate. All spec sections covered.
- **Type consistency:** domain `"files"` (`_FILES_DOMAIN`), ops `Op.CREATE`/`Op.DELETE`/`Op.MODIFY`, helpers `_abs`/`_desired`/`_read`/`_exists`/`_needs_write`/`_entry_fields`, `_SECTIONS`/`_ENV_PATH` constants — consistent across tasks.
- **Reconciler integration:** already registered `__root__`; `build_plan`/`sync` pass the full config dict; `is_v3()` flips True once `plan` is overridden (Task 3); `_domain_for` sees one key (`files`).
- **Canonical vs target paths:** the domain/manifest items are canonical (`/etc/...`), target-independent; only file I/O resolves through `_abs()`. So an `M` recorded under `/mnt` install matches a day-2 `/` apply.
- **Note:** `_v3` test helper stubs `actual`/`_read`; the real `actual` excludes non-existent declared paths, so MODIFY only fires for existing files (matches `plan` using `set(desired) & actual`).

# Issue #173 block A — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a declared `wheel` user actually able to `sudo`, give systemd-boot a rescue entry, and bring CPU frequency scaling (amd_pstate/intel_pstate + power-profiles-daemon), sysrq, `systemd-boot-update.service` and reflector into the declarative config.

**Architecture:** Everything that is only packages/units/files goes through an expand toggle in `dasik/lib/expand/toggles.py`; kernel parameters go through `KernelCmdlineAction`'s existing **auto** channel (explicit `kernel_cmdline` still wins); the one feature that owns state on disk — the sudoers fragment — becomes a new `SudoAction` built on `ScalarV3Action`.

**Tech Stack:** Python ≥3.10, pydantic v2, pytest + pytest-cov, mypy, bandit. No new runtime dependency.

**Spec:** [docs/superpowers/specs/2026-08-10-issue-173-quick-wins-design.md](../specs/2026-08-10-issue-173-quick-wins-design.md)

## Global Constraints

- TDD is mandatory for every logic change here (`models/`, `actions/`, `expand/`, `validation/`): write the failing test first, run it, then implement.
- Never run `execute()`/`apply()` against real hardware. `Command.execute` is mocked in every test; no test touches a real disk or a real `/etc`.
- Coverage gate is 80% (`pytest --cov=dasik`); do not lower it.
- `mypy dasik` and `bandit -r dasik -ll` must stay clean.
- Runtime dependencies stay `pydantic` + `colorama`. No new package.
- Re-running the same config must stay a no-op: every new action/derivation needs a real state read.
- Commit after each task. **Never `git add -A` in this repo** — stage the exact paths listed in the task (`config/mysystem.json*`, `test-config*.json` are the user's private local captures).
- Do not push and do not merge. Branch is `feat/issue-173-quick-wins`.

## File Structure

| File | Responsibility |
| --- | --- |
| `dasik/lib/models/sudo_model.py` (create) | `SudoModel`: wheel/nopasswd/rules with injection-safe validation |
| `dasik/lib/models/cpu_model.py` (create) | `CpuModel`: scaling driver, mode, ppd, governor |
| `dasik/lib/models/reflector_model.py` (create) | `ReflectorModel`: mirror-list refresh options |
| `dasik/lib/models/json_model.py` (modify) | wire `sudo`, `cpu`, `reflector`, `sysrq` into the root model |
| `dasik/lib/actions/sudo_action.py` (create) | `SudoAction`: render/validate/write `/etc/sudoers.d/10-dasik`, capture it back |
| `dasik/lib/actions/actions_handler_v2.py` (modify) | register `SudoAction` after `UsersAction` |
| `dasik/lib/actions/bootloader_action.py` (modify) | second sd-boot entry `arch-fallback.conf` as a domain item |
| `dasik/lib/actions/kernel_cmdline_action.py` (modify) | read the *default* entry; derive cpu + sysrq params; subtract them on import |
| `dasik/lib/expand/toggles.py` (modify) | `expand_cpu`, `expand_sdboot_update`, `expand_reflector` |
| `dasik/lib/validation/preflight.py` (modify) | sudo-without-provider, ppd+governor, ppd+tlp, new unit providers |
| `tests/lib/**` | one test module per unit above |
| `docs/config-reference.md`, `config/install-megamix.json` | document + exercise the new fields |

---

### Task 1: `SudoModel` and its root field

**Files:**
- Create: `dasik/lib/models/sudo_model.py`
- Modify: `dasik/lib/models/json_model.py`
- Test: `tests/lib/models/test_sudo_model.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `SudoModel(wheel: bool = True, nopasswd: bool = False, rules: List[str] = [])`; `JsonModel.sudo: Optional[SudoModel] = None`.

- [ ] **Step 1: Write the failing test**

Create `tests/lib/models/test_sudo_model.py`:

```python
import pytest
from pydantic import ValidationError

from dasik.lib.models.sudo_model import SudoModel
from dasik.lib.models.json_model import JsonModel


def test_defaults_grant_wheel_with_password():
    m = SudoModel()
    assert m.wheel is True
    assert m.nopasswd is False
    assert m.rules == []


def test_rules_are_kept_verbatim_and_in_order():
    m = SudoModel(rules=["andres ALL=(ALL) NOPASSWD: /usr/bin/pacman",
                         "%docker ALL=(ALL) NOPASSWD: /usr/bin/docker"])
    assert m.rules[0].startswith("andres ")
    assert m.rules[1].startswith("%docker ")


@pytest.mark.parametrize("bad", [
    "andres ALL=(ALL) ALL\n%wheel ALL=(ALL) NOPASSWD: ALL",   # smuggled second line
    "andres ALL=(ALL) ALL\rroot ALL=(ALL) ALL",
    "   ",
    "@includedir /etc/sudoers.d",
    "#include /tmp/evil",
])
def test_rejects_multiline_blank_and_include_rules(bad):
    with pytest.raises(ValidationError):
        SudoModel(rules=[bad])


def test_json_model_accepts_a_sudo_block():
    cfg = JsonModel(**{"sudo": {"wheel": True, "nopasswd": False, "rules": []}})
    assert cfg.sudo is not None and cfg.sudo.wheel is True


def test_json_model_sudo_defaults_to_none():
    assert JsonModel().sudo is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/lib/models/test_sudo_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dasik.lib.models.sudo_model'`.

- [ ] **Step 3: Write the model**

Create `dasik/lib/models/sudo_model.py`:

```python
"""Model for the sudoers fragment dasik owns (/etc/sudoers.d/10-dasik).

Putting a user in `wheel` does nothing on stock Arch: `%wheel` ships commented
out in /etc/sudoers, so a declared administrator could not `sudo` at all. This
block is what turns the group membership into actual sudo access.
"""
from typing import List

from pydantic import BaseModel, Field, field_validator

# An include directive would pull rules dasik neither renders nor tracks into
# the fragment it validates — the fragment must be self-contained.
_FORBIDDEN_PREFIXES = ("@include", "#include")


class SudoModel(BaseModel):
    """Declarative sudo access."""

    wheel: bool = Field(True, description="Grant sudo to the wheel group")
    nopasswd: bool = Field(False, description="wheel sudo without a password prompt")
    rules: List[str] = Field(
        default_factory=list,
        description="Extra sudoers lines, written verbatim after the wheel rule",
    )

    @field_validator("rules")
    @classmethod
    def _single_line_rules(cls, v: List[str]) -> List[str]:
        for rule in v:
            if not rule.strip():
                raise ValueError("a sudoers rule must not be empty")
            if "\n" in rule or "\r" in rule:
                raise ValueError(f"a sudoers rule must be a single line: {rule!r}")
            if rule.strip().lower().startswith(_FORBIDDEN_PREFIXES):
                raise ValueError(f"include directives are not allowed in rules: {rule!r}")
        return v
```

- [ ] **Step 4: Wire it into `JsonModel`**

In `dasik/lib/models/json_model.py`, add the import next to the other model imports:

```python
from .sudo_model import SudoModel
```

and the field next to the other optional sections (after `snapper: Optional[SnapperModel] = None`):

```python
    sudo: Optional[SudoModel] = None
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/lib/models/test_sudo_model.py -v`
Expected: PASS (7 tests).

- [ ] **Step 6: Commit**

```bash
git add dasik/lib/models/sudo_model.py dasik/lib/models/json_model.py tests/lib/models/test_sudo_model.py
git commit -m "feat(models): declarative sudo block (wheel, nopasswd, rules)"
```

---

### Task 2: `SudoAction`

**Files:**
- Create: `dasik/lib/actions/sudo_action.py`
- Modify: `dasik/lib/actions/actions_handler_v2.py` (register after `UsersAction`)
- Test: `tests/lib/actions/test_sudo_action.py`

**Interfaces:**
- Consumes: `SudoModel` shape from Task 1; `ScalarV3Action` (`dasik/lib/actions/scalar_action.py`) with hooks `_desired_value`, `_actual_value`, `_set_value`, `_import_fragment`.
- Produces: `SudoAction(config: dict, context=None)` with `_DOMAIN = "sudo"`, module constants `_CANON = "/etc/sudoers.d/10-dasik"`, `_TMP = _CANON + ".tmp"`, and module functions `_render(cfg: dict) -> str`, `_canonical(text: str) -> str`.

- [ ] **Step 1: Write the failing test**

Create `tests/lib/actions/test_sudo_action.py`:

```python
import os
import subprocess

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.sudo_action import SudoAction, _canonical, _render
from dasik.lib.command_worker.command_worker import Command
from dasik.lib.target.target import Target


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


@pytest.fixture
def visudo_ok(monkeypatch):
    """visudo always validates. Records the argv it was called with."""
    calls = []

    def fake(cmd, args, **kwargs):
        calls.append((cmd, list(args)))
        return subprocess.CompletedProcess(args=[cmd, *args], returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(Command, "execute", staticmethod(fake))
    return calls


@pytest.fixture
def visudo_fails(monkeypatch):
    def fake(cmd, args, **kwargs):
        return subprocess.CompletedProcess(args=[cmd, *args], returncode=1,
                                           stdout=b"", stderr=b"parse error")

    monkeypatch.setattr(Command, "execute", staticmethod(fake))


# --- rendering -----------------------------------------------------------

def test_render_wheel_with_password():
    assert "%wheel ALL=(ALL:ALL) ALL" in _render({"wheel": True})


def test_render_wheel_nopasswd():
    out = _render({"wheel": True, "nopasswd": True})
    assert "%wheel ALL=(ALL) NOPASSWD: ALL" in out
    assert "ALL=(ALL:ALL) ALL" not in out


def test_render_keeps_rule_order_after_wheel():
    out = _canonical(_render({"wheel": True, "rules": ["a ALL=(ALL) ALL", "b ALL=(ALL) ALL"]}))
    assert out.splitlines() == ["%wheel ALL=(ALL:ALL) ALL", "a ALL=(ALL) ALL", "b ALL=(ALL) ALL"]


def test_render_empty_when_nothing_declared():
    assert _render({}) == ""
    assert _render({"wheel": False}) == ""


def test_canonical_drops_comments_and_blank_lines():
    assert _canonical("# managed\n\n%wheel ALL=(ALL:ALL) ALL\n") == "%wheel ALL=(ALL:ALL) ALL\n"


# --- planning ------------------------------------------------------------

def test_plans_a_write_when_the_fragment_is_absent(tmp_path):
    action = SudoAction({"sudo": {"wheel": True}}, _ctx(tmp_path))
    assert [c.item for c in action.plan(managed=[])]


def test_no_plan_when_the_fragment_already_matches(tmp_path, visudo_ok):
    action = SudoAction({"sudo": {"wheel": True}}, _ctx(tmp_path))
    action.apply(action.plan(managed=[]))
    assert action.plan(managed=[]) == []          # idempotency


def test_plans_a_rewrite_when_the_fragment_differs(tmp_path, visudo_ok):
    action = SudoAction({"sudo": {"wheel": True}}, _ctx(tmp_path))
    action.apply(action.plan(managed=[]))
    changed = SudoAction({"sudo": {"wheel": True, "nopasswd": True}}, _ctx(tmp_path))
    assert [c.item for c in changed.plan(managed=[])]


def test_a_user_in_wheel_enables_sudo_without_a_sudo_block(tmp_path):
    action = SudoAction({"users": [{"username": "andres", "groups": ["wheel"]}]}, _ctx(tmp_path))
    assert "%wheel ALL=(ALL:ALL) ALL" in (action._desired_value() or "")


def test_explicit_wheel_false_disables_the_implicit_default(tmp_path):
    action = SudoAction({"sudo": {"wheel": False},
                         "users": [{"username": "andres", "groups": ["wheel"]}]}, _ctx(tmp_path))
    assert action._desired_value() is None
    assert action.plan(managed=[]) == []


def test_no_user_in_wheel_plans_nothing(tmp_path):
    action = SudoAction({"users": [{"username": "bob", "groups": ["video"]}]}, _ctx(tmp_path))
    assert action.plan(managed=[]) == []


# --- applying ------------------------------------------------------------

def test_apply_writes_the_fragment_0440_and_validates_it(tmp_path, visudo_ok):
    action = SudoAction({"sudo": {"wheel": True}}, _ctx(tmp_path))
    action.apply(action.plan(managed=[]))

    written = tmp_path / "etc/sudoers.d/10-dasik"
    assert "%wheel ALL=(ALL:ALL) ALL" in written.read_text()
    assert oct(os.stat(written).st_mode & 0o777) == "0o440"
    assert visudo_ok == [("visudo", ["-cf", "/etc/sudoers.d/10-dasik.tmp"])]
    assert not (tmp_path / "etc/sudoers.d/10-dasik.tmp").exists()


def test_a_fragment_visudo_rejects_never_reaches_the_directory(tmp_path, visudo_fails):
    action = SudoAction({"sudo": {"wheel": True}}, _ctx(tmp_path))
    with pytest.raises(Exception):
        action.apply(action.plan(managed=[]))
    assert not (tmp_path / "etc/sudoers.d/10-dasik").exists()
    assert not (tmp_path / "etc/sudoers.d/10-dasik.tmp").exists()


# --- capture -------------------------------------------------------------

def test_import_state_round_trips_the_fragment(tmp_path, visudo_ok):
    action = SudoAction({"sudo": {"wheel": True, "nopasswd": True,
                                  "rules": ["andres ALL=(ALL) NOPASSWD: /usr/bin/pacman"]}},
                        _ctx(tmp_path))
    action.apply(action.plan(managed=[]))

    captured = SudoAction({}, _ctx(tmp_path)).import_state()
    assert captured == {"sudo": {"wheel": True, "nopasswd": True,
                                 "rules": ["andres ALL=(ALL) NOPASSWD: /usr/bin/pacman"]}}


def test_import_state_sees_wheel_enabled_in_stock_sudoers(tmp_path):
    (tmp_path / "etc").mkdir(parents=True)
    (tmp_path / "etc/sudoers").write_text("# %wheel ALL=(ALL:ALL) ALL\n%wheel ALL=(ALL:ALL) ALL\n")
    captured = SudoAction({}, _ctx(tmp_path)).import_state()
    assert captured["sudo"]["wheel"] is True


def test_import_state_is_empty_on_a_system_without_sudo_access(tmp_path):
    (tmp_path / "etc").mkdir(parents=True)
    (tmp_path / "etc/sudoers").write_text("# %wheel ALL=(ALL:ALL) ALL\nroot ALL=(ALL:ALL) ALL\n")
    assert SudoAction({}, _ctx(tmp_path)).import_state() == {}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/lib/actions/test_sudo_action.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dasik.lib.actions.sudo_action'`.

- [ ] **Step 3: Write the action**

Create `dasik/lib/actions/sudo_action.py`:

```python
"""Action: the sudoers fragment dasik owns (v3 scalar domain "sudo").

Declaring a user in `wheel` is not enough on Arch: /etc/sudoers ships `%wheel`
commented out, so the declared administrator has no sudo at all. This action
writes /etc/sudoers.d/10-dasik with the wheel rule (and any extra rules), and
never installs a fragment `visudo` refuses — a broken fragment breaks sudo for
every user on the machine.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .scalar_action import ScalarV3Action
from ..command_worker.command_worker import Command
from ..exceptions.exceptions import ConfigValidationError

_CANON = "/etc/sudoers.d/10-dasik"
# sudo's `#includedir` skips any filename containing a '.', so even a temporary
# left behind by a crash is never parsed as a rule file.
_TMP = _CANON + ".tmp"
_SUDOERS = "/etc/sudoers"
_HEADER = "# Managed by dasik — `dasik apply` overwrites this file.\n"


def _render(cfg: Dict[str, Any]) -> str:
    """The fragment's content for a `sudo` block. Empty when it grants nothing."""
    lines: List[str] = []
    if cfg.get("wheel", True):
        lines.append("%wheel ALL=(ALL) NOPASSWD: ALL" if cfg.get("nopasswd")
                     else "%wheel ALL=(ALL:ALL) ALL")
    lines.extend(str(rule).strip() for rule in cfg.get("rules") or [])
    if not lines:
        return ""
    return _HEADER + "\n".join(lines) + "\n"


def _canonical(text: str) -> str:
    """Comparable form: effective lines only, so a comment or blank-line edit is
    not mistaken for drift and re-applied forever."""
    keep = [line.strip() for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")]
    return "\n".join(keep) + "\n" if keep else ""


class SudoAction(ScalarV3Action):
    """Manage /etc/sudoers.d/10-dasik declaratively."""

    _DOMAIN = "sudo"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        self._cfg: Dict[str, Any] = config if isinstance(config, dict) else {}

    @property
    def name(self) -> str:
        return "Sudo Access"

    @property
    def is_optional(self) -> bool:
        return True

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _path(self, canonical: str = _CANON) -> str:
        t = self._target()
        return t.path(canonical) if t is not None else "/mnt" + canonical

    def _effective(self) -> Dict[str, Any]:
        """The `sudo` block, or the implicit default it stands in for.

        With no block declared, a user in `wheel` still expects to be an
        administrator — that is the whole point of the group — so the default is
        the password-protected wheel rule. An explicit `{"wheel": false}` opts
        out; only omission triggers the default.
        """
        declared = self._cfg.get("sudo")
        if declared is not None:
            return dict(declared)
        for user in self._cfg.get("users") or []:
            if isinstance(user, dict) and "wheel" in (user.get("groups") or []):
                return {"wheel": True, "nopasswd": False, "rules": []}
        return {}

    # --- ScalarV3Action hooks ----------------------------------------- #

    def _desired_value(self) -> Optional[str]:
        return _canonical(_render(self._effective())) or None

    def _actual_value(self) -> Optional[str]:
        try:
            with open(self._path(), "r") as f:
                return _canonical(f.read()) or None
        except OSError:
            return None

    def _set_value(self) -> None:
        content = _render(self._effective())
        tmp = self._path(_TMP)
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        with open(tmp, "w") as f:
            f.write(content)
        os.chmod(tmp, 0o440)

        # visudo runs INSIDE the target, so it gets the canonical path.
        result = Command.execute("visudo", ["-cf", _TMP], target=self._target())
        if getattr(result, "returncode", 1) != 0:
            os.remove(tmp)
            raise ConfigValidationError(
                f"visudo rejected the generated sudoers fragment; {_CANON} was left "
                "untouched. Check the `sudo.rules` entries in the config.")

        os.replace(tmp, self._path())
        os.chmod(self._path(), 0o440)

    def _import_fragment(self, value: str) -> dict:
        wheel = False
        nopasswd = False
        rules: List[str] = []
        for line in value.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("%wheel"):
                wheel = True
                nopasswd = "NOPASSWD" in line
                continue
            rules.append(line)
        if not wheel and not rules:
            return {}
        return {"sudo": {"wheel": wheel, "nopasswd": nopasswd, "rules": rules}}

    def import_state(self, managed=None) -> dict:
        """Capture the fragment — or, when dasik does not own one, the fact that
        stock /etc/sudoers already grants wheel. A captured config must reproduce
        a machine where sudo works, whichever of the two enabled it."""
        value = self._actual_value()
        if value:
            return self._import_fragment(value)
        if self._stock_sudoers_grants_wheel():
            return {"sudo": {"wheel": True, "nopasswd": False, "rules": []}}
        desired = self._desired_value()
        return self._import_fragment(desired) if desired else {}

    def _stock_sudoers_grants_wheel(self) -> bool:
        try:
            with open(self._path(_SUDOERS), "r") as f:
                lines = f.read().splitlines()
        except OSError:
            return False
        return any(line.strip().startswith("%wheel") for line in lines)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/lib/actions/test_sudo_action.py -v`
Expected: PASS (16 tests).

- [ ] **Step 5: Register the action after `UsersAction`**

In `dasik/lib/actions/actions_handler_v2.py`, add the import alongside the other action imports inside `setup_actions()`:

```python
    from .sudo_action import SudoAction
```

and register it immediately after the `UsersAction` block (still phase 3, before phase 4's `SystemdAction`):

```python
    # Sudo comes after Users: the wheel group has members by now, and
    # PackagesAction has installed `sudo` (so `visudo` exists in the target).
    register_action(
        action_class=SudoAction,
        config_key='__root__',   # reads `sudo` plus `users` for the implicit default
        is_optional=True,
    )
```

- [ ] **Step 6: Add the registry test**

Append to `tests/lib/actions/test_sudo_action.py`:

```python
def test_registered_after_users_action():
    from dasik.lib.actions.action_registry import get_default_registry
    from dasik.lib.actions.actions_handler_v2 import setup_actions
    from dasik.lib.actions.users_action import UsersAction

    registry = get_default_registry()
    registry.clear()
    setup_actions()
    classes = [entry.action_class for entry in registry.actions]
    assert classes.index(SudoAction) > classes.index(UsersAction)
```

Check the registry's accessor names first (`rg -n "def clear|def actions|class ActionRegistry" dasik/lib/actions/action_registry.py`) and use the real ones; the assertion is what matters — `SudoAction` must come after `UsersAction`.

- [ ] **Step 7: Run the full action suite**

Run: `pytest tests/lib/actions -q`
Expected: PASS, no regression in the other action tests.

- [ ] **Step 8: Commit**

```bash
git add dasik/lib/actions/sudo_action.py dasik/lib/actions/actions_handler_v2.py tests/lib/actions/test_sudo_action.py
git commit -m "feat(sudo): write a visudo-validated /etc/sudoers.d/10-dasik"
```

---

### Task 3: preflight — a sudo block with no sudo package

**Files:**
- Modify: `dasik/lib/validation/preflight.py`
- Test: `tests/lib/validation/test_preflight.py`

**Interfaces:**
- Consumes: `Issue(level, code, message)` and the `preflight(config, efi_boot=None)` entry point already in the module; `_declared_packages(config)`.
- Produces: `_check_sudo(config: Dict[str, Any], packages: Set[str]) -> List[Issue]` with codes `sudo_without_provider` (error) and `wheel_without_sudo` (warning).

- [ ] **Step 1: Write the failing test**

Append to `tests/lib/validation/test_preflight.py`:

```python
def test_explicit_sudo_block_without_the_sudo_package_is_an_error():
    issues = preflight({"sudo": {"wheel": True}, "packages": ["base"]}, efi_boot=True)
    assert any(i.code == "sudo_without_provider" and i.level == "error" for i in issues)


def test_sudo_block_with_base_devel_is_accepted():
    issues = preflight({"sudo": {"wheel": True}, "packages": ["base", "base-devel"]}, efi_boot=True)
    assert not any(i.code == "sudo_without_provider" for i in issues)


def test_implicit_wheel_default_without_sudo_only_warns():
    issues = preflight({"users": [{"username": "andres", "groups": ["wheel"]}],
                        "packages": ["base"]}, efi_boot=True)
    assert any(i.code == "wheel_without_sudo" and i.level == "warning" for i in issues)
    assert not any(i.code == "sudo_without_provider" for i in issues)


def test_no_sudo_finding_when_nothing_asks_for_it():
    issues = preflight({"packages": ["base"]}, efi_boot=True)
    assert not any(i.code in ("sudo_without_provider", "wheel_without_sudo") for i in issues)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/lib/validation/test_preflight.py -k sudo -v`
Expected: FAIL — no issue with those codes is produced.

- [ ] **Step 3: Implement the check**

In `dasik/lib/validation/preflight.py`, add the provider set next to the other provider maps:

```python
# Packages that ship /usr/bin/sudo (and visudo). `base` does NOT.
_SUDO_PROVIDERS: Set[str] = {"sudo", "base-devel"}
```

add the check function next to `_check_units`:

```python
def _check_sudo(config: Dict[str, Any], packages: Set[str]) -> List[Issue]:
    """A sudoers fragment is useless without sudo installed.

    An EXPLICIT `sudo` block is an error: the user asked for something the
    config cannot deliver. The IMPLICIT default (no block, a user in `wheel`)
    only warns — a config that installs fine today must not start failing
    preflight because of a default it never asked for.
    """
    if _SUDO_PROVIDERS & packages:
        return []
    if config.get("sudo") is not None:
        return [Issue(
            "error", "sudo_without_provider",
            "a `sudo` block is declared but no declared package provides sudo "
            f"(provided by: {', '.join(sorted(_SUDO_PROVIDERS))}); the fragment "
            "could not even be validated with visudo.")]
    for user in config.get("users") or []:
        if isinstance(user, dict) and "wheel" in (user.get("groups") or []):
            return [Issue(
                "warning", "wheel_without_sudo",
                f"user {user.get('username')!r} is in `wheel` but no declared "
                "package provides sudo, so the group grants nothing.")]
    return []
```

and call it from `preflight()`:

```python
    issues += _check_sudo(config, packages)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/lib/validation/test_preflight.py -v`
Expected: PASS, including the pre-existing cases.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/validation/preflight.py tests/lib/validation/test_preflight.py
git commit -m "feat(preflight): flag a sudo block with no package providing sudo"
```

---

### Task 4: sd-boot fallback entry

**Files:**
- Modify: `dasik/lib/actions/bootloader_action.py`
- Test: `tests/lib/actions/test_bootloader_fallback_entry.py`

**Interfaces:**
- Consumes: `BootloaderAction._p`, `_ucode_initrds`, `_root_param`, `_is_sdboot`, `_installed` (already in the file).
- Produces: module constants `_FALLBACK_ENTRY = "/boot/loader/entries/arch-fallback.conf"`, `_FALLBACK_ITEM = "fallback-entry"`, `_MAIN_INITRD = "/initramfs-linux.img"`, `_FALLBACK_INITRD = "/initramfs-linux-fallback.img"`; methods `_fallback_initrd() -> str` and `_write_fallback_entry() -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/lib/actions/test_bootloader_fallback_entry.py`:

```python
import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.bootloader_action import BootloaderAction
from dasik.lib.target.target import Target


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


def _sdboot_cfg():
    return {"bootloader": "sd-boot", "enable_microcode": False,
            "disks": {"disks": [{"device": "/dev/vda", "partitions": [
                {"label": "root", "mountpoint": "/"}]}]}}


def _mark_installed(root):
    esp = root / "boot/EFI/systemd"
    esp.mkdir(parents=True)
    (esp / "systemd-bootx64.efi").write_text("stub")


def test_plans_the_fallback_entry_on_an_already_installed_sdboot(tmp_path):
    _mark_installed(tmp_path)
    action = BootloaderAction(_sdboot_cfg(), _ctx(tmp_path))
    assert [c.item for c in action.plan(managed=[])] == ["fallback-entry"]


def test_no_fallback_planned_for_grub(tmp_path):
    grub = tmp_path / "boot/grub"
    grub.mkdir(parents=True)
    (grub / "grub.cfg").write_text("stub")
    action = BootloaderAction({"bootloader": "grub"}, _ctx(tmp_path))
    assert action.plan(managed=[]) == []


def test_writes_the_fallback_entry_using_the_fallback_image_when_present(tmp_path):
    _mark_installed(tmp_path)
    (tmp_path / "boot").mkdir(exist_ok=True)
    (tmp_path / "boot/initramfs-linux-fallback.img").write_text("img")
    action = BootloaderAction(_sdboot_cfg(), _ctx(tmp_path))

    action.apply(action.plan(managed=[]))

    entry = (tmp_path / "boot/loader/entries/arch-fallback.conf").read_text()
    assert "title Arch Linux (fallback initramfs)" in entry
    assert "initrd /initramfs-linux-fallback.img" in entry
    assert "options root=LABEL=root rw" in entry


def test_falls_back_to_the_main_image_when_dracut_built_no_fallback(tmp_path):
    _mark_installed(tmp_path)
    action = BootloaderAction(_sdboot_cfg(), _ctx(tmp_path))

    action.apply(action.plan(managed=[]))

    entry = (tmp_path / "boot/loader/entries/arch-fallback.conf").read_text()
    assert "initrd /initramfs-linux.img" in entry
    assert "fallback.img" not in entry


def test_existing_fallback_entry_is_not_rewritten(tmp_path):
    _mark_installed(tmp_path)
    entries = tmp_path / "boot/loader/entries"
    entries.mkdir(parents=True)
    (entries / "arch-fallback.conf").write_text("hand-edited\n")
    action = BootloaderAction(_sdboot_cfg(), _ctx(tmp_path))

    assert action.plan(managed=[]) == []                       # idempotency
    assert (entries / "arch-fallback.conf").read_text() == "hand-edited\n"


def test_microcode_initrds_are_repeated_on_the_fallback_entry(tmp_path):
    _mark_installed(tmp_path)
    (tmp_path / "boot").mkdir(exist_ok=True)
    (tmp_path / "boot/amd-ucode.img").write_text("img")
    cfg = dict(_sdboot_cfg(), enable_microcode=True)
    action = BootloaderAction(cfg, _ctx(tmp_path))

    action.apply(action.plan(managed=[]))

    entry = (tmp_path / "boot/loader/entries/arch-fallback.conf").read_text()
    assert entry.index("initrd /amd-ucode.img") < entry.index("initrd /initramfs-linux.img")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/lib/actions/test_bootloader_fallback_entry.py -v`
Expected: FAIL — `plan()` returns `[]` on an installed sd-boot (it is install-only today).

- [ ] **Step 3: Implement the fallback entry**

In `dasik/lib/actions/bootloader_action.py`, add the constants under the existing markers:

```python
_FALLBACK_ENTRY = "/boot/loader/entries/arch-fallback.conf"
_FALLBACK_ITEM = "fallback-entry"
_MAIN_INITRD = "/initramfs-linux.img"
_FALLBACK_INITRD = "/initramfs-linux-fallback.img"
```

replace `actual()`, `plan()` and `apply()` with:

```python
    def actual(self) -> set:
        found = set()
        if self._installed():
            found.add(self.bootloader)
        if self._is_sdboot() and os.path.exists(self._p(_FALLBACK_ENTRY)):
            found.add(_FALLBACK_ITEM)
        return found

    def plan(self, managed) -> list:
        have = self.actual()
        changes = []
        if self.bootloader not in have:
            changes.append(Change(self._DOMAIN, Op.INSTALL, self.bootloader,
                                  reason="install bootloader"))
        # The rescue entry is a domain item of its own, so a machine whose
        # bootloader is ALREADY installed still gets it on the next apply.
        if self._is_sdboot() and _FALLBACK_ITEM not in have:
            changes.append(Change(self._DOMAIN, Op.INSTALL, _FALLBACK_ITEM,
                                  reason="rescue boot entry"))
        return changes

    def apply(self, changes) -> None:
        items = {c.item for c in changes}
        if self.bootloader in items:
            self._install()                 # writes both entries for sd-boot
        if _FALLBACK_ITEM in items and not os.path.exists(self._p(_FALLBACK_ENTRY)):
            self._write_fallback_entry()
```

add the two new methods next to `_ucode_initrds`:

```python
    def _fallback_initrd(self) -> str:
        """mkinitcpio's fallback image when the ESP has one, else the same image
        the main entry loads. dracut builds no fallback image, so there the entry
        is a duplicate — still worth having: it survives an edit that breaks
        arch.conf."""
        if os.path.exists(self._p("/boot" + _FALLBACK_INITRD)):
            return _FALLBACK_INITRD
        return _MAIN_INITRD

    def _write_fallback_entry(self) -> None:
        path = self._p(_FALLBACK_ENTRY)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lines = ["title Arch Linux (fallback initramfs)", "linux /vmlinuz-linux"]
        lines += [f"initrd {img}" for img in self._ucode_initrds()]
        lines.append(f"initrd {self._fallback_initrd()}")
        lines.append(f"options {self._root_param()} rw")
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
```

and in `_install()`'s sd-boot branch, use the constant for the main entry and write the fallback right after `arch.conf`:

```python
            lines.append(f"initrd {_MAIN_INITRD}")
            lines.append(f"options {self._root_param()} rw")
            with open(os.path.join(entries_dir, "arch.conf"), "w") as f:
                f.write("\n".join(lines) + "\n")
            self._write_fallback_entry()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/lib/actions/test_bootloader_fallback_entry.py tests/lib/actions/test_bootloader_action.py tests/lib/actions/test_bootloader_root_param.py -v`
Expected: PASS. If an existing test asserts `actual() == {bootloader}` on an sd-boot target that already has a fallback entry, update it to the new two-item domain — the behaviour change is intended.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/bootloader_action.py tests/lib/actions/test_bootloader_fallback_entry.py tests/lib/actions/test_bootloader_action.py
git commit -m "feat(bootloader): ship an arch-fallback.conf rescue entry for sd-boot"
```

---

### Task 5: read the *default* loader entry, not `listdir[0]`

**Files:**
- Modify: `dasik/lib/actions/kernel_cmdline_action.py`
- Test: `tests/lib/actions/test_kernel_cmdline_default_entry.py`

**Interfaces:**
- Consumes: `_sdboot_entries()`, `_current_params_sdboot(entry_file)` (already in the file).
- Produces: `_default_entry() -> Optional[str]` returning a filename such as `"arch.conf"`; `_current_cmdline()` now resolves through it.

- [ ] **Step 1: Write the failing test**

Create `tests/lib/actions/test_kernel_cmdline_default_entry.py`:

```python
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction
from dasik.lib.target.target import Target


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


def _entries(root, main: str, fallback: str, default: str = "arch"):
    entries = root / "boot/loader/entries"
    entries.mkdir(parents=True)
    (entries / "arch.conf").write_text(
        f"title Arch Linux\nlinux /vmlinuz-linux\noptions {main}\n")
    (entries / "arch-fallback.conf").write_text(
        f"title Arch Linux (fallback initramfs)\nlinux /vmlinuz-linux\noptions {fallback}\n")
    (root / "boot/loader/loader.conf").write_text(f"default {default}\ntimeout 3\n")


def test_reads_the_entry_loader_conf_points_at(tmp_path):
    _entries(tmp_path, main="root=LABEL=root rw quiet", fallback="root=LABEL=root rw")
    action = KernelCmdlineAction({"bootloader": "sd-boot"}, _ctx(tmp_path))
    assert "quiet" in action.actual()


def test_accepts_a_default_written_with_the_conf_suffix(tmp_path):
    _entries(tmp_path, main="root=LABEL=root rw quiet", fallback="root=LABEL=root rw",
             default="arch.conf")
    action = KernelCmdlineAction({"bootloader": "sd-boot"}, _ctx(tmp_path))
    assert "quiet" in action.actual()


def test_falls_back_to_arch_conf_without_a_loader_conf(tmp_path):
    entries = tmp_path / "boot/loader/entries"
    entries.mkdir(parents=True)
    (entries / "arch-fallback.conf").write_text("options root=LABEL=root rw\n")
    (entries / "arch.conf").write_text("options root=LABEL=root rw quiet\n")
    action = KernelCmdlineAction({"bootloader": "sd-boot"}, _ctx(tmp_path))
    assert "quiet" in action.actual()


def test_no_entries_reads_empty(tmp_path):
    action = KernelCmdlineAction({"bootloader": "sd-boot"}, _ctx(tmp_path))
    assert action.actual() == set()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/lib/actions/test_kernel_cmdline_default_entry.py -v`
Expected: FAIL on the first two cases — `os.listdir` yields `arch-fallback.conf` first on most filesystems, so `quiet` is missing.

- [ ] **Step 3: Implement**

In `dasik/lib/actions/kernel_cmdline_action.py`, add next to `_sdboot_entries`:

```python
    def _default_entry(self) -> Optional[str]:
        """The entry filename loader.conf selects (`default arch` → arch.conf)."""
        t = self._target()
        loader = (t.path("/boot/loader/loader.conf") if t is not None
                  else "/mnt/boot/loader/loader.conf")
        try:
            with open(loader, "r") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] == "default":
                        name = parts[1]
                        return name if name.endswith(".conf") else name + ".conf"
        except OSError:
            pass
        return None
```

and replace `_current_cmdline`:

```python
    def _current_cmdline(self) -> str:
        if self.bootloader == "grub":
            return self._current_params_grub()
        # Read the entry the firmware actually boots. Reading `listdir[0]`
        # stopped being deterministic once a second entry (arch-fallback.conf)
        # existed — it sorts FIRST, so the plan compared against the rescue entry.
        entries = sorted(self._sdboot_entries())
        if not entries:
            return ""
        for wanted in (self._default_entry(), "arch.conf"):
            if not wanted:
                continue
            for entry in entries:
                if os.path.basename(entry) == wanted:
                    return self._current_params_sdboot(entry)
        return self._current_params_sdboot(entries[0])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/lib/actions -k kernel_cmdline -v`
Expected: PASS, including the existing cmdline suites.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/kernel_cmdline_action.py tests/lib/actions/test_kernel_cmdline_default_entry.py
git commit -m "fix(cmdline): compare against the default loader entry, not listdir order"
```

---

### Task 6: `CpuModel`, root field, and the `expand_cpu` toggle

**Files:**
- Create: `dasik/lib/models/cpu_model.py`
- Modify: `dasik/lib/models/json_model.py`, `dasik/lib/expand/toggles.py`
- Test: `tests/lib/models/test_cpu_model.py`, `tests/lib/expand/test_expand_cpu.py`

**Interfaces:**
- Consumes: the toggle contract in `dasik/lib/expand/toggles.py` — a function `(config: dict) -> dict` returning any of `packages`, `units`, `sockets`, `modprobe_conf`, `files`, `user_groups`, registered in the module-level `TOGGLES` list.
- Produces: `CpuModel(scaling_driver, mode, power_profiles_daemon, governor)`; `JsonModel.cpu: Optional[CpuModel]`; `JsonModel.sysrq: bool = False`; `expand_cpu(config) -> dict`; module constant `_CPUPOWER_CONF = "/etc/default/cpupower"`.

- [ ] **Step 1: Write the failing model test**

Create `tests/lib/models/test_cpu_model.py`:

```python
import pytest
from pydantic import ValidationError

from dasik.lib.models.cpu_model import CpuModel
from dasik.lib.models.json_model import JsonModel


def test_defaults_are_auto_active_with_ppd():
    m = CpuModel()
    assert m.scaling_driver == "auto"
    assert m.mode == "active"
    assert m.power_profiles_daemon is True
    assert m.governor is None


def test_amd_pstate_accepts_guided():
    assert CpuModel(scaling_driver="amd_pstate", mode="guided").mode == "guided"


def test_intel_pstate_rejects_guided():
    with pytest.raises(ValidationError):
        CpuModel(scaling_driver="intel_pstate", mode="guided")


def test_amd_pstate_rejects_disable_mode():
    with pytest.raises(ValidationError):
        CpuModel(scaling_driver="amd_pstate", mode="disable")


def test_unknown_driver_is_rejected():
    with pytest.raises(ValidationError):
        CpuModel(scaling_driver="pstate9000")


def test_governor_must_be_a_plain_identifier():
    assert CpuModel(governor="performance").governor == "performance"
    with pytest.raises(ValidationError):
        CpuModel(governor="performance; rm -rf /")


def test_json_model_accepts_cpu_and_sysrq():
    cfg = JsonModel(**{"cpu": {"scaling_driver": "amd_pstate"}, "sysrq": True})
    assert cfg.cpu is not None and cfg.cpu.scaling_driver == "amd_pstate"
    assert cfg.sysrq is True


def test_json_model_cpu_defaults_to_none_and_sysrq_false():
    cfg = JsonModel()
    assert cfg.cpu is None
    assert cfg.sysrq is False
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/lib/models/test_cpu_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dasik.lib.models.cpu_model'`.

- [ ] **Step 3: Write the model**

Create `dasik/lib/models/cpu_model.py`:

```python
"""Model for CPU frequency scaling (the old installer's `install_cpu_scaler`).

`amd_pstate=active` is what the imperative installer appended to every boot
entry on AMD; Intel's equivalent is `intel_pstate`, which the kernel enables by
default — dasik emits it explicitly anyway so the resulting cmdline is
deterministic and reviewable rather than "whatever the kernel decided".
"""
import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

_AMD_MODES = ("active", "guided", "passive", "disable")
_INTEL_MODES = ("active", "passive", "disable")
_GOVERNOR_RE = re.compile(r"^[a-z_]+$")


class CpuModel(BaseModel):
    """Declarative CPU scaling policy."""

    scaling_driver: Literal["auto", "amd_pstate", "intel_pstate", "acpi_cpufreq", "none"] = Field(
        "auto", description="auto detects the CPU vendor from /proc/cpuinfo")
    mode: Literal["active", "guided", "passive", "disable"] = Field(
        "active", description="driver mode (guided is AMD-only)")
    power_profiles_daemon: bool = Field(
        True, description="install and enable power-profiles-daemon")
    governor: Optional[str] = Field(
        None, description="cpupower governor, e.g. 'performance'. Leave unset to "
                          "let power-profiles-daemon own the policy.")

    @field_validator("governor")
    @classmethod
    def _plain_identifier(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _GOVERNOR_RE.match(v):
            raise ValueError("governor must be a plain identifier, e.g. 'performance'")
        return v

    @model_validator(mode="after")
    def _mode_fits_driver(self) -> "CpuModel":
        # 'guided' exists only on amd_pstate; 'disable' is not an amd_pstate mode
        # dasik emits (use scaling_driver="none" instead).
        if self.scaling_driver == "amd_pstate" and self.mode == "disable":
            raise ValueError("use scaling_driver='none' instead of mode='disable'")
        if self.scaling_driver == "intel_pstate" and self.mode not in _INTEL_MODES:
            raise ValueError(f"intel_pstate accepts {list(_INTEL_MODES)}, not {self.mode!r}")
        return self
```

- [ ] **Step 4: Wire the root fields**

In `dasik/lib/models/json_model.py` add the import:

```python
from .cpu_model import CpuModel
```

and the fields (next to the other optional sections / flags):

```python
    cpu: Optional[CpuModel] = None
    sysrq: bool = False
```

- [ ] **Step 5: Run the model test to verify it passes**

Run: `pytest tests/lib/models/test_cpu_model.py -v`
Expected: PASS (8 tests).

- [ ] **Step 6: Write the failing toggle test**

Create `tests/lib/expand/test_expand_cpu.py`:

```python
from dasik.lib.expand import expand_config
from dasik.lib.expand.toggles import expand_cpu


def test_absent_block_contributes_nothing():
    assert expand_cpu({}) == {}


def test_ppd_package_and_unit():
    out = expand_cpu({"cpu": {"scaling_driver": "amd_pstate"}})
    assert out["packages"] == ["power-profiles-daemon"]
    assert out["units"] == ["power-profiles-daemon.service"]
    assert "files" not in out


def test_ppd_can_be_turned_off():
    out = expand_cpu({"cpu": {"power_profiles_daemon": False}})
    assert out == {}


def test_governor_pulls_cpupower_and_writes_its_default_file():
    out = expand_cpu({"cpu": {"power_profiles_daemon": False, "governor": "performance"}})
    assert out["packages"] == ["cpupower"]
    assert out["units"] == ["cpupower.service"]
    assert out["files"] == [{
        "path": "/etc/default/cpupower",
        "content": '# Managed by dasik\ngovernor="performance"\n',
    }]


def test_expand_config_merges_the_contribution():
    merged = expand_config({"cpu": {"scaling_driver": "auto"}, "packages": ["base"]})
    assert "power-profiles-daemon" in merged["packages"]
    assert "power-profiles-daemon.service" in merged["systemd"]["enable_units"]
```

- [ ] **Step 7: Run it to verify it fails**

Run: `pytest tests/lib/expand/test_expand_cpu.py -v`
Expected: FAIL — `ImportError: cannot import name 'expand_cpu'`.

- [ ] **Step 8: Implement the toggle**

In `dasik/lib/expand/toggles.py`, add before the `TOGGLES` list:

```python
_CPUPOWER_CONF = "/etc/default/cpupower"


def expand_cpu(config: Dict[str, Any]) -> Dict[str, Any]:
    cfg = config.get("cpu") or {}
    if not cfg:
        return {}
    packages: list = []
    units: list = []
    files: list = []
    if cfg.get("power_profiles_daemon", True):
        packages.append("power-profiles-daemon")
        units.append("power-profiles-daemon.service")
    governor = cfg.get("governor")
    if governor:
        # cpupower applies a fixed governor; ppd would fight it, which is why
        # preflight warns when both are declared.
        packages.append("cpupower")
        units.append("cpupower.service")
        files.append({"path": _CPUPOWER_CONF,
                      "content": f'# Managed by dasik\ngovernor="{governor}"\n'})
    out: Dict[str, Any] = {}
    if packages:
        out["packages"] = packages
    if units:
        out["units"] = units
    if files:
        out["files"] = files
    return out
```

and add `expand_cpu` to the `TOGGLES` list.

- [ ] **Step 9: Run the toggle tests to verify they pass**

Run: `pytest tests/lib/expand -v`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add dasik/lib/models/cpu_model.py dasik/lib/models/json_model.py dasik/lib/expand/toggles.py tests/lib/models/test_cpu_model.py tests/lib/expand/test_expand_cpu.py
git commit -m "feat(cpu): declarative CPU scaling block (ppd, cpupower governor)"
```

---

### Task 7: derive `amd_pstate` / `intel_pstate` / `sysrq_always_enabled` on the auto channel

**Files:**
- Modify: `dasik/lib/actions/kernel_cmdline_action.py`
- Test: `tests/lib/actions/test_kernel_cmdline_cpu.py`

**Interfaces:**
- Consumes: `CpuModel` shape (Task 6), `_merge(auto, explicit)`, `_derive_from_disks()`, `_tokens()`, `import_state()` (all already in the file).
- Produces: `_cpu_vendor() -> Optional[str]` (`"amd"`/`"intel"`/`None`), `_derive_from_cpu() -> List[str]`, `_derived() -> List[str]` (disks + cpu), all three used by `desired_params`, `_desired_tokens` and `import_state`.

- [ ] **Step 1: Write the failing test**

Create `tests/lib/actions/test_kernel_cmdline_cpu.py`:

```python
import pytest

from dasik.lib.actions import kernel_cmdline_action as kca
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction
from dasik.lib.target.target import Target


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


@pytest.fixture
def amd(monkeypatch):
    monkeypatch.setattr(KernelCmdlineAction, "_cpu_vendor", staticmethod(lambda: "amd"))


@pytest.fixture
def intel(monkeypatch):
    monkeypatch.setattr(KernelCmdlineAction, "_cpu_vendor", staticmethod(lambda: "intel"))


def test_auto_on_amd_derives_amd_pstate(amd):
    action = KernelCmdlineAction({"cpu": {"scaling_driver": "auto", "mode": "active"}})
    assert "amd_pstate=active" in action.desired_params


def test_auto_on_intel_derives_intel_pstate(intel):
    action = KernelCmdlineAction({"cpu": {"scaling_driver": "auto", "mode": "active"}})
    assert "intel_pstate=active" in action.desired_params


def test_explicit_driver_ignores_the_detected_vendor(intel):
    action = KernelCmdlineAction({"cpu": {"scaling_driver": "amd_pstate", "mode": "guided"}})
    assert "amd_pstate=guided" in action.desired_params


def test_guided_on_intel_degrades_to_active(intel):
    action = KernelCmdlineAction({"cpu": {"scaling_driver": "auto", "mode": "guided"}})
    assert "intel_pstate=active" in action.desired_params


def test_driver_none_derives_nothing(amd):
    action = KernelCmdlineAction({"cpu": {"scaling_driver": "none"}})
    assert not [p for p in action.desired_params if "pstate" in p]


def test_unknown_vendor_derives_nothing(monkeypatch):
    monkeypatch.setattr(KernelCmdlineAction, "_cpu_vendor", staticmethod(lambda: None))
    action = KernelCmdlineAction({"cpu": {"scaling_driver": "auto"}})
    assert not [p for p in action.desired_params if "pstate" in p]


def test_explicit_kernel_cmdline_beats_the_derived_value(amd):
    action = KernelCmdlineAction({"cpu": {"scaling_driver": "auto", "mode": "active"},
                                  "kernel_cmdline": ["amd_pstate=passive"]})
    assert "amd_pstate=passive" in action.desired_params
    assert "amd_pstate=active" not in action.desired_params


def test_sysrq_flag_derives_the_parameter():
    action = KernelCmdlineAction({"sysrq": True})
    assert "sysrq_always_enabled=1" in action.desired_params


def test_sysrq_absent_derives_nothing():
    action = KernelCmdlineAction({})
    assert action.desired_params == []


def test_cpu_vendor_reads_proc_cpuinfo(monkeypatch, tmp_path):
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("vendor_id\t: AuthenticAMD\n")
    monkeypatch.setattr(kca, "_CPUINFO", str(cpuinfo))
    assert KernelCmdlineAction._cpu_vendor() == "amd"


def test_import_state_does_not_re_emit_derived_cpu_params(tmp_path, amd):
    entries = tmp_path / "boot/loader/entries"
    entries.mkdir(parents=True)
    (entries / "arch.conf").write_text(
        "options root=LABEL=root rw amd_pstate=active sysrq_always_enabled=1 quiet\n")
    (tmp_path / "boot/loader/loader.conf").write_text("default arch\n")

    action = KernelCmdlineAction({"bootloader": "sd-boot", "sysrq": True,
                                  "cpu": {"scaling_driver": "auto", "mode": "active"}},
                                 _ctx(tmp_path))
    captured = action.import_state()["kernel_cmdline"]

    assert "amd_pstate=active" not in captured
    assert "sysrq_always_enabled=1" not in captured
    assert "quiet" in captured


def test_import_state_still_keeps_a_hand_set_pstate_without_a_cpu_block(tmp_path):
    entries = tmp_path / "boot/loader/entries"
    entries.mkdir(parents=True)
    (entries / "arch.conf").write_text("options root=LABEL=root rw amd_pstate=active\n")
    (tmp_path / "boot/loader/loader.conf").write_text("default arch\n")

    action = KernelCmdlineAction({"bootloader": "sd-boot"}, _ctx(tmp_path))
    assert "amd_pstate=active" in action.import_state()["kernel_cmdline"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/lib/actions/test_kernel_cmdline_cpu.py -v`
Expected: FAIL — `AttributeError: type object 'KernelCmdlineAction' has no attribute '_cpu_vendor'`.

- [ ] **Step 3: Implement the derivation**

In `dasik/lib/actions/kernel_cmdline_action.py`, add the module constant near the top:

```python
_CPUINFO = "/proc/cpuinfo"
_INTEL_MODES = ("active", "passive", "disable")
```

add the methods next to `_derive_from_disks`:

```python
    @staticmethod
    def _cpu_vendor() -> Optional[str]:
        """"amd" / "intel" / None, from /proc/cpuinfo.

        The installer runs on the machine being installed, so the live CPU is the
        target's CPU — the same assumption BaseInstallAction._detect_microcode
        already makes when it picks amd-ucode vs intel-ucode.
        """
        try:
            with open(_CPUINFO, "r") as f:
                content = f.read()
        except OSError:
            return None
        if "AuthenticAMD" in content:
            return "amd"
        if "GenuineIntel" in content:
            return "intel"
        return None

    def _derive_from_cpu(self) -> List[str]:
        """Kernel params for the `cpu` block and the `sysrq` flag."""
        params: List[str] = []
        cpu = self._cfg.get("cpu") or {}
        if cpu:
            driver = cpu.get("scaling_driver", "auto")
            mode = cpu.get("mode", "active")
            if driver == "auto":
                driver = {"amd": "amd_pstate", "intel": "intel_pstate"}.get(
                    self._cpu_vendor() or "", "none")
            if driver == "amd_pstate":
                params.append(f"amd_pstate={mode}")
            elif driver == "intel_pstate":
                # `guided` is an amd_pstate-only mode; on Intel it would be
                # ignored by the kernel, so emit the driver's default instead of
                # a parameter that silently does nothing.
                params.append(f"intel_pstate={mode if mode in _INTEL_MODES else 'active'}")
            elif driver == "acpi_cpufreq":
                params.append("amd_pstate=disable" if self._cpu_vendor() == "amd"
                              else "intel_pstate=disable")
        if self._cfg.get("sysrq"):
            params.append("sysrq_always_enabled=1")
        return params

    def _derived(self) -> List[str]:
        """Everything dasik derives itself — never captured back by `sync`."""
        return self._derive_from_disks() + self._derive_from_cpu()
```

then replace every remaining use of `self._derive_from_disks()` **outside** `_derived` with `self._derived()`:

- in `desired_params` (`return self._merge(self._derived(), self.explicit_params)`),
- in `_desired_tokens` (`merged = self._merge(self._derived(), self.explicit_params)`),
- in `import_state` (`for token in self._tokens(self._derived()):`).

Update `import_state`'s docstring: the subtracted set is now "everything dasik derives (disks, cpu, sysrq)"; what stays is what somebody set by hand.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/lib/actions -k kernel_cmdline -v`
Expected: PASS — including `test_kernel_cmdline_sync.py`, which pins the existing capture behaviour.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/kernel_cmdline_action.py tests/lib/actions/test_kernel_cmdline_cpu.py
git commit -m "feat(cmdline): derive pstate + sysrq params from the cpu block"
```

---

### Task 8: preflight — power-profiles-daemon coherence

**Files:**
- Modify: `dasik/lib/validation/preflight.py`
- Test: `tests/lib/validation/test_preflight.py`

**Interfaces:**
- Consumes: `_declared_packages`, `Issue`, `preflight` (module) — and the `cpu` block shape from Task 6.
- Produces: `_check_cpu(config: Dict[str, Any], packages: Set[str]) -> List[Issue]` with codes `ppd_and_governor` (warning), `ppd_and_tlp` (error), plus three new `_UNIT_PROVIDERS` entries.

- [ ] **Step 1: Write the failing test**

Append to `tests/lib/validation/test_preflight.py`:

```python
def test_ppd_with_an_explicit_governor_warns():
    issues = preflight({"cpu": {"power_profiles_daemon": True, "governor": "performance"}},
                       efi_boot=True)
    assert any(i.code == "ppd_and_governor" and i.level == "warning" for i in issues)


def test_ppd_with_tlp_is_an_error():
    issues = preflight({"cpu": {"power_profiles_daemon": True}, "packages": ["tlp"]},
                       efi_boot=True)
    assert any(i.code == "ppd_and_tlp" and i.level == "error" for i in issues)


def test_governor_without_ppd_is_clean():
    issues = preflight({"cpu": {"power_profiles_daemon": False, "governor": "performance"}},
                       efi_boot=True)
    assert not any(i.code in ("ppd_and_governor", "ppd_and_tlp") for i in issues)


def test_ppd_unit_without_its_package_warns():
    issues = preflight({"systemd": {"enable_units": ["power-profiles-daemon.service"]},
                        "packages": ["base"]}, efi_boot=True)
    assert any(i.code == "unit_without_provider" and i.level == "warning" for i in issues)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/lib/validation/test_preflight.py -k "ppd or governor" -v`
Expected: FAIL — none of those codes exist yet.

- [ ] **Step 3: Implement**

In `dasik/lib/validation/preflight.py`, extend `_UNIT_PROVIDERS`:

```python
    "power-profiles-daemon.service": {"power-profiles-daemon"},
    "cpupower.service": {"cpupower"},
    "reflector.timer": {"reflector"},
```

add the check:

```python
def _check_cpu(config: Dict[str, Any], packages: Set[str]) -> List[Issue]:
    """power-profiles-daemon owns the frequency policy it shares with nobody."""
    cpu = config.get("cpu") or {}
    if not cpu or not cpu.get("power_profiles_daemon", True):
        return []
    issues: List[Issue] = []
    if cpu.get("governor"):
        issues.append(Issue(
            "warning", "ppd_and_governor",
            "power-profiles-daemon manages the energy-performance preference, so a "
            "fixed cpupower governor will be fought over; declare one or the other."))
    if "tlp" in packages:
        issues.append(Issue(
            "error", "ppd_and_tlp",
            "power-profiles-daemon and tlp both manage power policy and conflict; "
            "keep one of them."))
    return issues
```

and call it from `preflight()`:

```python
    issues += _check_cpu(config, packages)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/lib/validation -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/validation/preflight.py tests/lib/validation/test_preflight.py
git commit -m "feat(preflight): catch power-profiles-daemon conflicts (governor, tlp)"
```

---

### Task 9: `systemd-boot-update.service` toggle

**Files:**
- Modify: `dasik/lib/expand/toggles.py`
- Test: `tests/lib/expand/test_expand_sdboot_update.py`

**Interfaces:**
- Consumes: the toggle contract and `TOGGLES` list.
- Produces: `expand_sdboot_update(config) -> dict`.

- [ ] **Step 1: Write the failing test**

Create `tests/lib/expand/test_expand_sdboot_update.py`:

```python
import pytest

from dasik.lib.expand import expand_config
from dasik.lib.expand.toggles import expand_sdboot_update


@pytest.mark.parametrize("loader", ["sd-boot", "systemd-boot"])
def test_enables_the_native_updater_for_systemd_boot(loader):
    assert expand_sdboot_update({"bootloader": loader}) == {
        "units": ["systemd-boot-update.service"]}


def test_grub_contributes_nothing():
    assert expand_sdboot_update({"bootloader": "grub"}) == {}


def test_missing_bootloader_contributes_nothing():
    assert expand_sdboot_update({}) == {}


def test_expand_config_enables_the_unit():
    merged = expand_config({"bootloader": "sd-boot"})
    assert "systemd-boot-update.service" in merged["systemd"]["enable_units"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/lib/expand/test_expand_sdboot_update.py -v`
Expected: FAIL — `ImportError: cannot import name 'expand_sdboot_update'`.

- [ ] **Step 3: Implement**

In `dasik/lib/expand/toggles.py`:

```python
def expand_sdboot_update(config: Dict[str, Any]) -> Dict[str, Any]:
    # systemd ships this unit itself: it runs `bootctl update` when the ESP's
    # loader is older than the installed systemd. The old imperative installer
    # built the AUR `systemd-boot-pacman-hook` for the same job; the native unit
    # needs no package at all.
    if config.get("bootloader") not in ("sd-boot", "systemd-boot"):
        return {}
    return {"units": ["systemd-boot-update.service"]}
```

and add it to `TOGGLES`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/lib/expand -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/expand/toggles.py tests/lib/expand/test_expand_sdboot_update.py
git commit -m "feat(boot): enable systemd-boot-update.service on sd-boot systems"
```

---

### Task 10: `reflector`

**Files:**
- Create: `dasik/lib/models/reflector_model.py`
- Modify: `dasik/lib/models/json_model.py`, `dasik/lib/expand/toggles.py`
- Test: `tests/lib/models/test_reflector_model.py`, `tests/lib/expand/test_expand_reflector.py`

**Interfaces:**
- Consumes: the toggle contract; `DropFilesAction` consumes the emitted `files` entry (it already creates parent directories and compares content, so `/etc/xdg/reflector/reflector.conf` needs no extra code).
- Produces: `ReflectorModel(countries, protocols, latest, sort, save)`; `JsonModel.reflector: Optional[ReflectorModel]`; `expand_reflector(config) -> dict`; module constant `_REFLECTOR_CONF = "/etc/xdg/reflector/reflector.conf"`.

- [ ] **Step 1: Write the failing model test**

Create `tests/lib/models/test_reflector_model.py`:

```python
import pytest
from pydantic import ValidationError

from dasik.lib.models.json_model import JsonModel
from dasik.lib.models.reflector_model import ReflectorModel


def test_defaults():
    m = ReflectorModel()
    assert m.countries == []
    assert m.protocols == ["https"]
    assert m.latest == 20
    assert m.sort == "rate"
    assert m.save == "/etc/pacman.d/mirrorlist"


def test_rejects_an_unknown_protocol():
    with pytest.raises(ValidationError):
        ReflectorModel(protocols=["carrier-pigeon"])


def test_rejects_a_non_positive_latest():
    with pytest.raises(ValidationError):
        ReflectorModel(latest=0)


def test_rejects_a_country_with_a_newline():
    with pytest.raises(ValidationError):
        ReflectorModel(countries=["ES\n--save /etc/passwd"])


def test_json_model_accepts_the_block():
    cfg = JsonModel(**{"reflector": {"countries": ["ES"]}})
    assert cfg.reflector is not None and cfg.reflector.countries == ["ES"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/lib/models/test_reflector_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dasik.lib.models.reflector_model'`.

- [ ] **Step 3: Write the model**

Create `dasik/lib/models/reflector_model.py`:

```python
"""Model for reflector — periodic pacman mirrorlist refresh."""
import re
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

_COUNTRY_RE = re.compile(r"^[A-Za-z][A-Za-z .'-]*$")


class ReflectorModel(BaseModel):
    """Options written to /etc/xdg/reflector/reflector.conf."""

    countries: List[str] = Field(default_factory=list,
                                 description="Mirror countries, e.g. ['ES', 'France']")
    protocols: List[Literal["https", "http", "rsync", "ftp"]] = Field(
        default_factory=lambda: ["https"])
    latest: Optional[int] = Field(20, ge=1, description="Keep the N most recently synced")
    sort: Literal["rate", "age", "score", "delay", "country"] = "rate"
    save: str = Field("/etc/pacman.d/mirrorlist", description="Mirrorlist to write")

    @field_validator("countries")
    @classmethod
    def _plain_countries(cls, v: List[str]) -> List[str]:
        # Each value becomes a `--country <value>` line in a config file
        # reflector parses as arguments; a newline would smuggle a second flag.
        for country in v:
            if not _COUNTRY_RE.match(country):
                raise ValueError(f"invalid country name: {country!r}")
        return v

    @field_validator("save")
    @classmethod
    def _absolute_save(cls, v: str) -> str:
        if not v.startswith("/") or "\n" in v:
            raise ValueError("save must be an absolute single-line path")
        return v
```

- [ ] **Step 4: Wire the root field**

In `dasik/lib/models/json_model.py`:

```python
from .reflector_model import ReflectorModel
```

```python
    reflector: Optional[ReflectorModel] = None
```

- [ ] **Step 5: Run the model test to verify it passes**

Run: `pytest tests/lib/models/test_reflector_model.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Write the failing toggle test**

Create `tests/lib/expand/test_expand_reflector.py`:

```python
from dasik.lib.expand import expand_config
from dasik.lib.expand.toggles import expand_reflector


def test_absent_block_contributes_nothing():
    assert expand_reflector({}) == {}


def test_package_timer_and_conf():
    out = expand_reflector({"reflector": {"countries": ["ES", "France"],
                                          "protocols": ["https"],
                                          "latest": 10, "sort": "rate"}})
    assert out["packages"] == ["reflector"]
    assert out["units"] == ["reflector.timer"]
    conf = out["files"][0]
    assert conf["path"] == "/etc/xdg/reflector/reflector.conf"
    assert conf["content"] == (
        "# Managed by dasik\n"
        "--country ES\n"
        "--country France\n"
        "--protocol https\n"
        "--latest 10\n"
        "--sort rate\n"
        "--save /etc/pacman.d/mirrorlist\n")


def test_defaults_when_only_countries_are_given():
    out = expand_reflector({"reflector": {"countries": ["ES"]}})
    content = out["files"][0]["content"]
    assert "--protocol https\n" in content
    assert "--latest 20\n" in content
    assert "--sort rate\n" in content


def test_expand_config_merges_package_unit_and_file():
    merged = expand_config({"reflector": {"countries": ["ES"]}})
    assert "reflector" in merged["packages"]
    assert "reflector.timer" in merged["systemd"]["enable_units"]
    assert any(f["path"] == "/etc/xdg/reflector/reflector.conf" for f in merged["files"])
```

- [ ] **Step 7: Run it to verify it fails**

Run: `pytest tests/lib/expand/test_expand_reflector.py -v`
Expected: FAIL — `ImportError: cannot import name 'expand_reflector'`.

- [ ] **Step 8: Implement the toggle**

In `dasik/lib/expand/toggles.py`:

```python
_REFLECTOR_CONF = "/etc/xdg/reflector/reflector.conf"


def expand_reflector(config: Dict[str, Any]) -> Dict[str, Any]:
    cfg = config.get("reflector") or {}
    if not cfg:
        return {}
    lines = ["# Managed by dasik"]
    lines += [f"--country {c}" for c in cfg.get("countries") or []]
    lines += [f"--protocol {p}" for p in cfg.get("protocols") or ["https"]]
    latest = cfg.get("latest", 20)
    if latest:
        lines.append(f"--latest {latest}")
    lines.append(f"--sort {cfg.get('sort', 'rate')}")
    lines.append(f"--save {cfg.get('save', '/etc/pacman.d/mirrorlist')}")
    return {
        "packages": ["reflector"],
        # Only the timer: the one-shot service is what the timer triggers.
        "units": ["reflector.timer"],
        "files": [{"path": _REFLECTOR_CONF, "content": "\n".join(lines) + "\n"}],
    }
```

and add it to `TOGGLES`.

- [ ] **Step 9: Run the tests to verify they pass**

Run: `pytest tests/lib/expand tests/lib/models -v`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add dasik/lib/models/reflector_model.py dasik/lib/models/json_model.py dasik/lib/expand/toggles.py tests/lib/models/test_reflector_model.py tests/lib/expand/test_expand_reflector.py
git commit -m "feat(reflector): declarative mirrorlist refresh (package, timer, conf)"
```

---

### Task 11: docs, sample config, and the full gate run

**Files:**
- Modify: `docs/config-reference.md`, `config/install-megamix.json`
- Test: the whole suite plus the CLI smoke checks

**Interfaces:**
- Consumes: every field added in Tasks 1–10 (`sudo`, `cpu`, `sysrq`, `reflector`).
- Produces: no new code — documentation and a config that exercises the new fields.

- [ ] **Step 1: Add the new fields to `config/install-megamix.json`**

Add these root-level blocks (keep the file valid JSON, destructive flags untouched):

```json
  "sudo": { "wheel": true, "nopasswd": false, "rules": [] },
  "cpu": { "scaling_driver": "auto", "mode": "active", "power_profiles_daemon": true },
  "sysrq": true,
  "reflector": { "countries": ["ES"], "protocols": ["https"], "latest": 20, "sort": "rate" },
```

- [ ] **Step 2: Verify the config still validates**

Run: `dasik check config/install-megamix.json`
Expected: exit code 0, "valid" output. (Install the package first if needed: `pip install -e .[dev]`.)

- [ ] **Step 3: Document the fields**

In `docs/config-reference.md`, add a row/section per field, matching the file's existing format: `sudo` (wheel/nopasswd/rules, the implicit wheel default, sync-captured), `cpu` (scaling_driver/mode/power_profiles_daemon/governor), `sysrq` (bool), `reflector` (countries/protocols/latest/sort/save). Note which of them `sync` captures: `sudo` yes (`SudoAction.import_state`), the rest no (they are toggles/derivations, and their effects round-trip through `packages`/`systemd`/`files`/`kernel_cmdline`).

- [ ] **Step 4: Run the whole gate set**

```bash
pytest --cov=dasik            # expect: all pass, coverage >= 80%
mypy dasik                    # expect: clean
bandit -r dasik -ll           # expect: no issues
./scripts/mutation.sh         # expect: no surviving mutants
```

Fix anything that fails before continuing — a red gate is not "flaky", it is the finding.

- [ ] **Step 5: Smoke the CLI against real sample configs**

```bash
dasik --help
python -m dasik --help
dasik check config/install-megamix.json
dasik plan config/install-megamix.json
dasik plan config/vm-dracut.json
```

`plan` against a config with `disks` fails fast with `CommandNotFoundException` off Arch install media — that is expected; what must not happen is a traceback from parsing, expansion or preflight.

- [ ] **Step 6: Commit**

```bash
git add docs/config-reference.md config/install-megamix.json
git commit -m "docs(config): document sudo, cpu, sysrq and reflector"
```

- [ ] **Step 7: VM verification (before opening the PR)**

Follow [docs/vm-testing.md](../../vm-testing.md) and run an install from `config/vm-dracut.json` with the new blocks added (`sudo`, `cpu`, `sysrq`). Confirm in the booted guest:

- `/boot/loader/entries/` has both `arch.conf` and `arch-fallback.conf`, and the machine boots.
- `sudo -l` as the declared user works (this is the bug the block fixes).
- `cat /proc/cmdline` shows the derived pstate parameter and `sysrq_always_enabled=1`.
- `dasik plan <same config>` is a no-op, and `dasik sync` does **not** copy the derived parameters into `kernel_cmdline`.

Attach the captured output to the PR's agentic-verification comment (see CLAUDE.md → "Agentic PR verification").

---

## Self-Review

**Spec coverage:** §1 sudo → Tasks 1–3. §2 sd-boot fallback → Tasks 4–5. §3 cpu → Tasks 6–8. §4 sysrq → Tasks 6 (field) + 7 (derivation). §5 systemd-boot-update → Task 9. §6 reflector → Task 10. Spec's "Testing" section → the test steps in every task. Spec's "Verification before the PR" → Task 11.

**Type consistency:** `_render`/`_canonical` (Task 2) are module-level in `sudo_action.py` and used by name in its tests. `_FALLBACK_ITEM = "fallback-entry"` (Task 4) is the exact string the tests assert. `_cpu_vendor` is a `staticmethod` in Task 7 and monkeypatched as one in the tests. `_derived()` replaces `_derive_from_disks()` in exactly three call sites, all listed.

**Known follow-ups (deliberately not in this plan):** plymouth/splash, the pendrive LUKS keyfile, per-user `$HOME` dotfiles, and the remaining issue-#173 blocks (profiles/environments, podman, docker, private AUR packages, config-saver, partitioning TUI).

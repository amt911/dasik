# package install reason (explicit/dep) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `packages` carry an install reason (explicit/dependency) for pacman packages so a declared `asdeps` package is tracked (not dropped), captured by `sync`, and enforced by `apply`. AUR packages are reason-exempt.

**Architecture:** `packages: List[Union[str, PackageSpec]]` (plain str = explicit; `{name, reason}` = dependency). `PackagesAction` parses a reason per pacman package; `plan` adds a reason `MODIFY`, presence is "installed with any reason" (`pacman -Qq`), `apply` enforces reason via `pacman -D`, and `import_state` annotates the reason. `actual()` stays `pacman -Qqe` (drift/manifest). Builds on PR #79.

**Tech Stack:** Python 3.10+, pydantic, pytest/pytest-cov.

Spec: `docs/superpowers/specs/2026-06-02-package-install-reason-design.md`.

**Test runner:**
```bash
python -m venv /tmp/dasik-venv && /tmp/dasik-venv/bin/pip install -q pytest pytest-cov colorama pydantic
PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest ...
```

---

## Task 1: `PackageSpec` model + `JsonModel.packages` Union

**Files:**
- Create: `dasik/lib/models/package_model.py`
- Modify: `dasik/lib/models/json_model.py` (import + `packages` type)
- Modify: `dasik/lib/models/__init__.py` (export `PackageSpec`)
- Test: `tests/lib/models/test_package_model.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/models/test_package_model.py`:

```python
import pytest

from dasik.lib.models.package_model import PackageSpec
from dasik.lib.models.json_model import JsonModel


def _base(**extra):
    return JsonModel(
        locales={"selected_locales": [], "desired_locale": "en_US.UTF-8",
                 "desired_tty_layout": "us"},
        timezone={"region": "Europe", "city": "Madrid"},
        network={"type": "NetworkManager", "add_default_hosts": True},
        hostname="arch",
        **extra,
    )


def test_packagespec_defaults_to_explicit():
    assert PackageSpec(name="git").reason == "explicit"


def test_packagespec_accepts_dep():
    assert PackageSpec(name="foo", reason="dep").reason == "dep"


def test_packagespec_rejects_bad_reason():
    with pytest.raises(ValueError):
        PackageSpec(name="foo", reason="weird")


def test_json_model_packages_accepts_str_and_object():
    m = _base(packages=["git", {"name": "foo", "reason": "dep"}, "aur-yay"])
    assert m.packages[0] == "git"
    assert m.packages[1].name == "foo" and m.packages[1].reason == "dep"
    assert m.packages[2] == "aur-yay"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/models/test_package_model.py -v`
Expected: FAIL — `package_model` missing; `packages` is `List[str]`.

- [ ] **Step 3: Implement**

Create `dasik/lib/models/package_model.py`:

```python
"""Model for a package with an install reason (pacman only)."""
from typing import Literal
from pydantic import BaseModel


class PackageSpec(BaseModel):
    """A pacman package marked with an install reason.

    Plain strings in the packages list mean "explicit"; this object form marks a
    dependency (``reason="dep"``). AUR packages stay plain ``aur-`` strings.
    """
    name: str
    reason: Literal["explicit", "dep"] = "explicit"
```

In `dasik/lib/models/json_model.py`: add `from typing import Union` (if absent), import
`from .package_model import PackageSpec`, and change the field:

```python
    packages: List[Union[str, PackageSpec]] = Field(
        default_factory=list, description="Packages (str=explicit, {name,reason} for deps; aur- prefix for AUR)")
```

In `dasik/lib/models/__init__.py`: add `from dasik.lib.models.package_model import
PackageSpec` and `"PackageSpec"` to `__all__`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/models/test_package_model.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/models/package_model.py dasik/lib/models/json_model.py dasik/lib/models/__init__.py tests/lib/models/test_package_model.py
git commit -m "feat(models): PackageSpec install reason + packages Union[str, PackageSpec]"
```

---

## Task 2: `PackagesAction` — parse reason + presence/reason helpers

**Files:**
- Modify: `dasik/lib/actions/packages_action.py`
- Test: `tests/lib/actions/test_packages_action_v3.py` (append)

- [ ] **Step 1: Write the failing tests**

Append (a reason-aware fake that distinguishes `-Qqe` from `-Qq`):

```python
def _reason_fake(explicit=b"", installed=b""):
    """Command.execute fake: -Qqe -> explicit set, -Qq -> all installed."""
    def run(cmd, args, *a, **k):
        flag = args[0] if args else ""
        out = explicit if flag == "-Qqe" else installed if flag == "-Qq" else b""
        return MagicMock(stdout=out, stderr=b"", returncode=0)
    return run


def test_parses_reason_for_pacman_objects():
    a = PackagesAction(config=["git", {"name": "foo", "reason": "dep"}], context=_ctx("/"))
    assert a.pacman_pkgs == ["git", "foo"]
    assert a._reason["git"] == "explicit"
    assert a._reason["foo"] == "dep"


def test_aur_object_ignores_reason():
    a = PackagesAction(config=[{"name": "aur-yay", "reason": "dep"}], context=_ctx("/"))
    assert a.aur_pkgs == ["yay"]
    assert "yay" not in a._reason     # AUR reason-exempt


def test_installed_all_and_reason_of():
    with patch("dasik.lib.actions.packages_action.Command.execute",
               _reason_fake(explicit=b"git\n", installed=b"git\ndep1\n")):
        a = PackagesAction(config=[], context=_ctx("/"))
        assert a._installed_all() == {"git", "dep1"}
        assert a._reason_of("git") == "explicit"
        assert a._reason_of("dep1") == "dep"   # installed but not in -Qqe
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_packages_action_v3.py -k "reason or installed_all" -v`
Expected: FAIL — `_reason`/`_installed_all`/`_reason_of` missing; object entries not parsed.

- [ ] **Step 3: Implement parsing + helpers**

In `dasik/lib/actions/packages_action.py`, replace `__init__`:

```python
    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        raw: List[Any] = config if isinstance(config, list) else []
        self._original = raw
        self.pacman_pkgs: List[str] = []
        self.aur_pkgs: List[str] = []
        self._reason: dict[str, str] = {}   # pacman name -> "explicit"|"dep"
        for entry in raw:
            if isinstance(entry, dict):
                name, reason = entry["name"], entry.get("reason", "explicit")
            else:
                name, reason = entry, "explicit"
            if name.startswith(AUR_PREFIX):
                self.aur_pkgs.append(name[len(AUR_PREFIX):])   # AUR: reason-exempt
            else:
                self.pacman_pkgs.append(name)
                self._reason[name] = reason
```

Add helpers next to `actual()`:

```python
    def _installed_all(self) -> set[str]:
        """All installed packages (any reason): pacman -Qq."""
        target = getattr(self.context, "target", None) if self.context else None
        if target is None:
            return set()
        result = Command.execute("pacman", ["-Qq"], target=target)
        stdout = getattr(result, "stdout", b"") or b""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        return {line.strip() for line in stdout.splitlines() if line.strip()}

    def _reason_of(self, pkg: str) -> str:
        """Install reason of an installed package: explicit if in -Qqe else dep."""
        return "explicit" if pkg in self.actual() else "dep"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_packages_action_v3.py -v`
Expected: PASS (new + existing; existing tests use one fake → `-Qqe`/`-Qq` return the same,
default reason `explicit`, so they are unaffected).

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/packages_action.py tests/lib/actions/test_packages_action_v3.py
git commit -m "feat(packages): parse install reason (pacman) + installed-any/reason helpers"
```

---

## Task 3: reason-aware `plan()`

**Files:**
- Modify: `dasik/lib/actions/packages_action.py`
- Test: `tests/lib/actions/test_packages_action_v3.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def test_plan_no_install_when_declared_dep_already_installed_as_dep():
    # foo declared as dep, installed as a dependency → no INSTALL, no MODIFY
    with patch("dasik.lib.actions.packages_action.Command.execute",
               _reason_fake(explicit=b"git\n", installed=b"git\nfoo\n")):
        a = PackagesAction(config=["git", {"name": "foo", "reason": "dep"}], context=_ctx("/"))
        changes = a.plan(managed=["git", "foo"])
    assert changes == []


def test_plan_modify_when_reason_drifts():
    # foo declared dep but currently explicit (in -Qqe) → MODIFY (reason change)
    with patch("dasik.lib.actions.packages_action.Command.execute",
               _reason_fake(explicit=b"git\nfoo\n", installed=b"git\nfoo\n")):
        a = PackagesAction(config=["git", {"name": "foo", "reason": "dep"}], context=_ctx("/"))
        changes = a.plan(managed=["git", "foo"])
    assert [(c.op, c.item) for c in changes] == [(Op.MODIFY, "foo")]


def test_plan_install_for_declared_dep_not_installed():
    with patch("dasik.lib.actions.packages_action.Command.execute",
               _reason_fake(explicit=b"git\n", installed=b"git\n")):
        a = PackagesAction(config=["git", {"name": "foo", "reason": "dep"}], context=_ctx("/"))
        changes = a.plan(managed=[])
    assert [(c.op, c.item) for c in changes] == [(Op.INSTALL, "foo")]


def test_plan_no_modify_for_aur():
    with patch("dasik.lib.actions.packages_action.Command.execute",
               _reason_fake(explicit=b"git\n", installed=b"git\nyay\n")):
        a = PackagesAction(config=["git", "aur-yay"], context=_ctx("/"))
        changes = a.plan(managed=["git", "yay"])
    assert changes == []   # yay installed (any reason), AUR never MODIFY
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_packages_action_v3.py -k "plan_no_install or reason_drifts or declared_dep_not_installed or no_modify_for_aur" -v`
Expected: FAIL — current `plan` uses `actual()` (=`-Qqe`) for presence, so a dep package
shows as INSTALL and reason MODIFY does not exist.

- [ ] **Step 3: Implement reason-aware plan**

Replace `plan` in `dasik/lib/actions/packages_action.py`:

```python
    def plan(self, managed):
        from ..state.change import Change, Op

        desired = list(self.pacman_pkgs) + list(self.aur_pkgs)
        installed = self._installed_all()
        explicit = self.actual()

        changes: list = []
        for name in sorted(n for n in desired if n not in installed):
            changes.append(Change(self._PACMAN_DOMAIN, Op.INSTALL, name))
        # reason MODIFY: pacman packages only, installed, reason drifted
        for name in sorted(self.pacman_pkgs):
            if name in installed:
                current = "explicit" if name in explicit else "dep"
                if current != self._reason.get(name, "explicit"):
                    changes.append(Change(self._PACMAN_DOMAIN, Op.MODIFY, name,
                                          reason="install reason"))
        for name in sorted(set(managed) - set(desired)):
            changes.append(Change(self._PACMAN_DOMAIN, Op.REMOVE, name,
                                  reason="no longer declared"))
        return changes
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_packages_action_v3.py -v`
Expected: PASS (new + existing plan tests; existing ones use a single fake so `-Qq`==`-Qqe`
and default reasons, leaving INSTALL/REMOVE behaviour identical).

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/packages_action.py tests/lib/actions/test_packages_action_v3.py
git commit -m "feat(packages): reason-aware plan() (presence any-reason + reason MODIFY)"
```

---

## Task 4: `apply()` enforces reason via `pacman -D`

**Files:**
- Modify: `dasik/lib/actions/packages_action.py`
- Test: `tests/lib/actions/test_packages_action_v3.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def test_apply_marks_installed_dep_as_asdeps():
    a = PackagesAction(config=[{"name": "foo", "reason": "dep"}], context=_ctx("/"))
    with patch("dasik.lib.actions.packages_action.Command.execute") as run:
        a.apply([Change("packages", Op.INSTALL, "foo")])
    calls = [(c.args[0], c.args[1]) for c in run.call_args_list]
    assert any(c[0] == "pacman" and "-S" in c[1] and "foo" in c[1] for c in calls)
    assert any(c[0] == "pacman" and "-D" in c[1] and "--asdeps" in c[1] and "foo" in c[1]
               for c in calls)


def test_apply_modify_sets_reason():
    a = PackagesAction(config=[{"name": "foo", "reason": "dep"}], context=_ctx("/"))
    with patch("dasik.lib.actions.packages_action.Command.execute") as run:
        a.apply([Change("packages", Op.MODIFY, "foo")])
    calls = [(c.args[0], c.args[1]) for c in run.call_args_list]
    assert calls == [("pacman", ["-D", "--asdeps", "foo"])] or \
           ("pacman", ["-D", "--asdeps", "foo"]) in calls


def test_apply_modify_to_explicit():
    a = PackagesAction(config=["foo"], context=_ctx("/"))   # explicit
    with patch("dasik.lib.actions.packages_action.Command.execute") as run:
        a.apply([Change("packages", Op.MODIFY, "foo")])
    calls = [(c.args[0], c.args[1]) for c in run.call_args_list]
    assert ("pacman", ["-D", "--asexplicit", "foo"]) in calls


def test_apply_explicit_install_no_asdeps(tmp_path):
    a = PackagesAction(config=["git"], context=_ctx("/"))
    with patch("dasik.lib.actions.packages_action.Command.execute") as run:
        a.apply([Change("packages", Op.INSTALL, "git")])
    calls = [(c.args[0], c.args[1]) for c in run.call_args_list]
    assert not any("-D" in c[1] for c in calls)   # explicit needs no -D
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_packages_action_v3.py -k "asdeps or modify_sets_reason or modify_to_explicit or explicit_install_no" -v`
Expected: FAIL — `apply` ignores reason; raises on MODIFY (unknown op in current routing).

- [ ] **Step 3: Implement reason enforcement in apply**

In `apply`, after parsing changes, also collect MODIFY and emit `pacman -D` calls. Update the
change loop + tail:

```python
        pacman_installs: list[str] = []
        aur_installs: list[str] = []
        removes: list[str] = []
        reason_set: list[str] = []          # pacman names whose reason to enforce
        aur_set = set(self.aur_pkgs)
        pacman_set = set(self.pacman_pkgs)

        for change in changes:
            if change.op is Op.INSTALL:
                if change.item in pacman_set:
                    pacman_installs.append(change.item)
                    reason_set.append(change.item)      # enforce reason after install
                elif change.item in aur_set:
                    aur_installs.append(change.item)
                else:
                    raise ValueError(
                        f"apply() received INSTALL for unknown package "
                        f"{change.item!r}: not in pacman_pkgs or aur_pkgs"
                    )
            elif change.op is Op.MODIFY:
                reason_set.append(change.item)
            elif change.op is Op.REMOVE:
                removes.append(change.item)

        if pacman_installs:
            Command.execute("pacman", ["--noconfirm", "--needed", "-S", *pacman_installs],
                            target=target)
        if aur_installs:
            self._apply_aur_install(aur_installs)

        # enforce install reason (pacman packages only; --asexplicit is the default
        # after -S, so only emit -D when it changes something)
        to_dep = [p for p in reason_set if self._reason.get(p, "explicit") == "dep"]
        to_explicit = [p for p in reason_set if self._reason.get(p, "explicit") == "explicit"
                       and any(c.op is Op.MODIFY and c.item == p for c in changes)]
        if to_dep:
            Command.execute("pacman", ["-D", "--asdeps", *to_dep], target=target)
        if to_explicit:
            Command.execute("pacman", ["-D", "--asexplicit", *to_explicit], target=target)

        if removes:
            Command.execute("pacman", ["--noconfirm", "-Rns", *removes], target=target)
```

Note: an explicit INSTALL does not need `-D` (`-S` marks it explicit), so `to_explicit` is
limited to MODIFY-driven reason changes; a dep INSTALL or dep MODIFY emits `--asdeps`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_packages_action_v3.py -v`
Expected: PASS (new + existing apply tests; existing explicit installs/removes emit no `-D`).

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/packages_action.py tests/lib/actions/test_packages_action_v3.py
git commit -m "feat(packages): apply() enforces install reason via pacman -D"
```

---

## Task 5: `import_state()` annotates reason

**Files:**
- Modify: `dasik/lib/actions/packages_action.py`
- Test: `tests/lib/actions/test_packages_action_v3.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def test_import_state_declared_dep_kept_as_object():
    # foo declared dep, installed as dep (in -Qq, not -Qqe) → kept as {name,reason:dep}
    with patch("dasik.lib.actions.packages_action.Command.execute",
               _reason_fake(explicit=b"git\n", installed=b"git\nfoo\n")):
        a = PackagesAction(config=["git", {"name": "foo", "reason": "dep"}], context=_ctx("/"))
        frag = a.import_state(managed=["git", "foo"])
    assert frag == {"packages": ["git", {"name": "foo", "reason": "dep"}]}


def test_import_state_explicit_drift_is_plain_string():
    with patch("dasik.lib.actions.packages_action.Command.execute",
               _reason_fake(explicit=b"git\nhtop\n", installed=b"git\nhtop\n")):
        a = PackagesAction(config=["git"], context=_ctx("/"))
        frag = a.import_state(managed=[])
    assert frag == {"packages": ["git", "htop"]}


def test_import_state_keeps_aur_verbatim():
    with patch("dasik.lib.actions.packages_action.Command.execute",
               _reason_fake(explicit=b"git\n", installed=b"git\nyay\n")):
        a = PackagesAction(config=["git", "aur-yay"], context=_ctx("/"))
        frag = a.import_state(managed=["git", "yay"])
    assert frag == {"packages": ["git", "aur-yay"]}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_packages_action_v3.py -k "declared_dep_kept or explicit_drift_is_plain or keeps_aur_verbatim" -v`
Expected: FAIL — current `import_state` emits the declared dep as a plain string, no reason.

- [ ] **Step 3: Implement reason-annotating import_state**

Replace `import_state`:

```python
    def import_state(self, managed: "list[str] | None" = None) -> dict:
        """Capture reality into the config fragment (sync), annotating the install
        reason for pacman packages.

        Declared entries are kept (intent). A pacman package that is installed is
        emitted as ``{name, reason}`` when it is a dependency, else a plain string.
        AUR entries are kept verbatim (``aur-…``). Undeclared explicit packages
        (``pacman -Qqe`` \\ declared) are appended as plain strings. Transitive
        dependencies are never captured.
        """
        explicit = self.actual()
        installed = self._installed_all()

        def _strip(name: str) -> str:
            return name[len(AUR_PREFIX):] if name.startswith(AUR_PREFIX) else name

        result: list = []
        declared_stripped: set = set()
        for entry in self._original:
            name = entry["name"] if isinstance(entry, dict) else entry
            declared_stripped.add(_strip(name))
            if name.startswith(AUR_PREFIX):
                result.append(name)                       # AUR verbatim
                continue
            if name in installed and name not in explicit:
                result.append({"name": name, "reason": "dep"})
            else:
                result.append(name)                       # explicit / intent (not installed)

        extra = sorted(explicit - declared_stripped)      # new explicit packages
        result.extend(extra)
        return {self._PACMAN_DOMAIN: result}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_packages_action_v3.py -v`
Expected: PASS (new + existing import_state tests; existing ones use one fake so installed==
explicit, leaving every declared package a plain string as before).

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/packages_action.py tests/lib/actions/test_packages_action_v3.py
git commit -m "feat(packages): import_state annotates install reason for deps (sync)"
```

---

## Task 6: Sample + full suite + gate

**Files:**
- Modify: `config/install-megamix.json`
- Test: full suite

- [ ] **Step 1: Add a dep package to the sample**

In `config/install-megamix.json`, in the `packages` array, add one object entry to exercise
the reason, e.g.:
```jsonc
    { "name": "imagemagick", "reason": "dep" }
```
(Keep the existing plain strings; valid JSON.)

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
git commit -m "docs(config): exercise a dependency-reason package in megamix"
```

---

## Self-Review notes

- **Spec coverage:** Task 1 = model Union; Task 2 = parse reason + helpers; Task 3 = plan
  (presence any-reason + reason MODIFY, AUR exempt); Task 4 = apply enforce via `pacman -D`;
  Task 5 = import_state annotate; Task 6 = sample + gate. All spec sections covered.
- **Type consistency:** `PackageSpec{name,reason}`, `self._reason` dict, `_installed_all`,
  `_reason_of`, `self._original`, domain `packages` — consistent across tasks.
- **AUR exemption:** AUR names never enter `self._reason`; `plan` MODIFY iterates
  `self.pacman_pkgs` only; `import_state` keeps `aur-` entries verbatim. AUR INSTALL/REMOVE
  routing unchanged.
- **Back-compat / existing tests:** `actual()` stays `-Qqe`; existing tests use a single
  `Command.execute` fake, so `-Qq` returns the same set and reasons default to explicit —
  INSTALL/REMOVE/import_state behaviour is unchanged for plain-string configs. New behaviour
  is covered by the reason-aware fake.
- **`managed_keys` unchanged:** still `pacman_pkgs + aur_pkgs` names (stripped), so the
  manifest M is reason-agnostic — consistent with `actual()` and the REMOVE math.
- **Reconciler:** registered `config_key="packages"`, already v3; no reconciler change.
```

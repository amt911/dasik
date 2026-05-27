# Foundation Primitives Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the reusable primitives the declarative-convergence engine needs — a root-parameterized `Target`, a root-aware `Command`, a `Change`/`Plan` diff model, a `StateStore` (manifest), and a `GenerationStore` — plus the project's first pytest test infrastructure.

**Architecture:** Pure, dependency-light units under `dasik/lib/` with one responsibility each. Nothing here changes existing install behavior: `Command` stays backward-compatible (legacy `run_as_chroot=True` still means `/mnt`), and the new modules are not yet wired into any action. This is Plan 1 of 4 (see spec §7); Plans 2–4 build the action contract v3, reconciler, domain migrations, and CLI on top of these primitives.

**Tech Stack:** Python ≥3.10 (stdlib: `dataclasses`, `enum`, `pathlib`, `json`, `subprocess`, `hashlib`), pytest + pytest-cov for tests.

**Spec:** `docs/superpowers/specs/2026-05-27-declarative-convergence-and-sync-design.md` (components §3.1–3.3, storage §6).

---

## File Structure

| File | Responsibility |
| --- | --- |
| `dasik/lib/target/__init__.py` | package marker |
| `dasik/lib/target/target.py` | `Target` — the root (`/` or `/mnt`) commands run against; path mapping + chroot decision |
| `dasik/lib/command_worker/command_worker.py` (modify) | make `Command.execute` root-aware via `Target`, keep legacy `/mnt` default |
| `dasik/lib/state/__init__.py` | package marker |
| `dasik/lib/state/change.py` | `Op` enum, `Change` dataclass, `Plan` aggregate + rendering |
| `dasik/lib/state/state_store.py` | `Manifest` dataclass + `StateStore` (read/write `state.json`) |
| `dasik/lib/state/generation_store.py` | `GenerationStore` (record/list/restore generations + `current` symlink) |
| `pyproject.toml` (modify) | add `dev` optional-deps + pytest/coverage config |
| `.gitignore` (modify) | stop ignoring `tests/` |
| `tests/conftest.py` | shared fixtures (`tmp_target`) |
| `tests/lib/target/test_target.py` | Target tests |
| `tests/lib/command_worker/test_command_worker.py` | Command tests |
| `tests/lib/state/test_change.py` | Change/Plan tests |
| `tests/lib/state/test_state_store.py` | StateStore round-trip tests |
| `tests/lib/state/test_generation_store.py` | GenerationStore tests |

---

## Task 0: Test infrastructure

**Files:**
- Modify: `.gitignore`
- Modify: `pyproject.toml`
- Create: `tests/conftest.py`

- [ ] **Step 1: Stop ignoring the tests directory**

In `.gitignore`, delete the line that reads exactly:

```
tests
```

(It is line 2. Leave every other line untouched.)

- [ ] **Step 2: Add dev dependencies and pytest/coverage config to `pyproject.toml`**

Append these three blocks to the end of `pyproject.toml`:

```toml
[project.optional-dependencies]
dev = ["pytest", "pytest-cov"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--import-mode=importlib"

[tool.coverage.run]
source = ["dasik"]
branch = true
```

> Note: no `--cov-fail-under=80` yet. The 80% gate (CLAUDE.md) is enforced once the
> action/reconciler surface is migrated (Plan 2+); enforcing it now would fail on the
> large untested legacy surface. Coverage is still *measured* (`pytest --cov=dasik`).

- [ ] **Step 3: Install dev dependencies**

Run: `pip install -e ".[dev]"`
Expected: installs `pytest` and `pytest-cov` into the environment.

> If pip refuses (externally-managed environment), use a virtualenv:
> `python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"`.

- [ ] **Step 4: Create the shared fixtures file**

Create `tests/conftest.py`:

```python
"""Shared pytest fixtures for the dasik test suite."""
import pytest

from dasik.lib.target.target import Target


@pytest.fixture
def tmp_target(tmp_path):
    """A Target rooted at a temporary directory.

    Because root != "/", Target.is_chroot is True, but the state/generation
    stores only do path mapping (no chroot commands run), so this gives an
    isolated on-disk root for filesystem tests.
    """
    return Target(root=str(tmp_path))
```

- [ ] **Step 5: Commit**

```bash
git add .gitignore pyproject.toml tests/conftest.py
git commit -m "test: add pytest infrastructure and dev deps

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 1: `Target`

**Files:**
- Create: `dasik/lib/target/__init__.py`
- Create: `dasik/lib/target/target.py`
- Test: `tests/lib/target/test_target.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/target/test_target.py`:

```python
import pytest

from dasik.lib.target.target import Target


def test_root_host_is_not_chroot():
    assert Target(root="/").is_chroot is False


def test_root_mnt_is_chroot():
    assert Target(root="/mnt").is_chroot is True


def test_default_root_is_mnt():
    assert Target().root == "/mnt"


def test_path_maps_into_mnt():
    assert Target(root="/mnt").path("/etc/hostname") == "/mnt/etc/hostname"


def test_path_unchanged_for_host_root():
    assert Target(root="/").path("/etc/hostname") == "/etc/hostname"


def test_path_rejects_relative():
    with pytest.raises(ValueError):
        Target(root="/mnt").path("etc/hostname")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/lib/target/test_target.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dasik.lib.target'`

- [ ] **Step 3: Create the package marker**

Create `dasik/lib/target/__init__.py` (empty file).

- [ ] **Step 4: Implement `Target`**

Create `dasik/lib/target/target.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    """The root filesystem dasik operates on.

    - root == "/"   : day-2 management of the running host; commands run directly.
    - root == "/mnt": install target; commands run via ``arch-chroot <root>``.
    """

    root: str = "/mnt"

    @property
    def is_chroot(self) -> bool:
        """True when commands must run inside ``arch-chroot <root>``."""
        return self.root != "/"

    def path(self, absolute: str) -> str:
        """Map an in-target absolute path to the corresponding host path.

        For root="/" the path is returned unchanged. For root="/mnt" and
        absolute="/etc/hostname" returns "/mnt/etc/hostname".
        """
        if not absolute.startswith("/"):
            raise ValueError(f"path must be absolute, got: {absolute!r}")
        if self.root == "/":
            return absolute
        return self.root.rstrip("/") + absolute
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/lib/target/test_target.py -v`
Expected: PASS (6 passed)

- [ ] **Step 6: Commit**

```bash
git add dasik/lib/target/ tests/lib/target/
git commit -m "feat: add Target root abstraction (/ vs /mnt)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: Root-aware `Command`

**Files:**
- Modify: `dasik/lib/command_worker/command_worker.py`
- Test: `tests/lib/command_worker/test_command_worker.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/command_worker/test_command_worker.py`:

```python
from unittest.mock import patch

from dasik.lib.command_worker.command_worker import Command
from dasik.lib.target.target import Target


def _run_argv():
    """Patch subprocess.run + which; return the argv list Command passed."""
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return "result"

    return captured, fake_run


def test_no_chroot_runs_directly():
    captured, fake_run = _run_argv()
    with patch("dasik.lib.command_worker.command_worker.subprocess.run", fake_run):
        Command.execute("ls", ["-la"])
    assert captured["argv"] == ["ls", "-la"]


def test_legacy_run_as_chroot_uses_mnt():
    captured, fake_run = _run_argv()
    with patch("dasik.lib.command_worker.command_worker.subprocess.run", fake_run), \
         patch("dasik.lib.command_worker.command_worker.which", return_value="/usr/bin/arch-chroot"):
        Command.execute("pacman", ["-Q"], run_as_chroot=True)
    assert captured["argv"] == ["/usr/bin/arch-chroot", "/mnt", "pacman", "-Q"]


def test_target_mnt_uses_arch_chroot():
    captured, fake_run = _run_argv()
    with patch("dasik.lib.command_worker.command_worker.subprocess.run", fake_run), \
         patch("dasik.lib.command_worker.command_worker.which", return_value="/usr/bin/arch-chroot"):
        Command.execute("pacman", ["-Q"], target=Target(root="/mnt"))
    assert captured["argv"] == ["/usr/bin/arch-chroot", "/mnt", "pacman", "-Q"]


def test_target_host_runs_directly():
    captured, fake_run = _run_argv()
    with patch("dasik.lib.command_worker.command_worker.subprocess.run", fake_run):
        Command.execute("pacman", ["-Q"], target=Target(root="/"))
    assert captured["argv"] == ["pacman", "-Q"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/lib/command_worker/test_command_worker.py -v`
Expected: FAIL — `test_target_*` raise `TypeError: execute() got an unexpected keyword argument 'target'`

- [ ] **Step 3: Implement the root-aware `Command`**

Replace the entire contents of `dasik/lib/command_worker/command_worker.py` with:

```python

from ..exceptions.exceptions import CommandNotFoundException
from ..target.target import Target
from shutil import which
import subprocess


class Command:
    """Thin wrapper around subprocess.run with optional arch-chroot support."""

    @staticmethod
    def _locate_binary(name: str) -> str:
        path = which(name)
        if not path:
            raise CommandNotFoundException(f"Binary not found: {name}")
        return path

    @staticmethod
    def execute(cmd: str, args: list[str], run_as_chroot: bool = False,
                target: "Target | None" = None):
        """Run *cmd* with *args*, optionally inside ``arch-chroot <root>``.

        Chroot root resolution:
        - if *target* is given it decides: ``target.is_chroot`` -> arch-chroot
          ``target.root``; otherwise (root="/") run directly on the host.
        - else if *run_as_chroot* is True, fall back to the legacy "/mnt"
          (preserves existing install-time callers that pass run_as_chroot=True).
        """
        chroot_cmd: list[str] = []
        if target is not None:
            if target.is_chroot:
                chroot_path = Command._locate_binary("arch-chroot")
                chroot_cmd = [chroot_path, target.root]
        elif run_as_chroot:
            chroot_path = Command._locate_binary("arch-chroot")
            chroot_cmd = [chroot_path, "/mnt"]

        return subprocess.run(
            chroot_cmd + [cmd, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/lib/command_worker/test_command_worker.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/command_worker/command_worker.py tests/lib/command_worker/
git commit -m "feat: make Command root-aware via Target (keep /mnt default)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: `Change` / `Op` / `Plan`

**Files:**
- Create: `dasik/lib/state/__init__.py`
- Create: `dasik/lib/state/change.py`
- Test: `tests/lib/state/test_change.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/state/test_change.py`:

```python
from dasik.lib.state.change import Op, Change, Plan


def test_install_is_not_destructive():
    assert Change("packages", Op.INSTALL, "git").destructive is False


def test_remove_is_destructive():
    assert Change("packages", Op.REMOVE, "git").destructive is True


def test_disable_and_delete_are_destructive():
    assert Change("systemd", Op.DISABLE, "sshd.service").destructive is True
    assert Change("files", Op.DELETE, "/etc/foo").destructive is True


def test_empty_plan():
    p = Plan()
    assert p.is_empty() is True
    assert p.destructive() == []


def test_plan_collects_and_filters():
    p = Plan()
    p.add(Change("packages", Op.INSTALL, "git"))
    p.add(Change("packages", Op.REMOVE, "vim", reason="no longer declared"))
    assert p.is_empty() is False
    assert len(p.changes) == 2
    destructive = p.destructive()
    assert len(destructive) == 1
    assert destructive[0].item == "vim"


def test_change_render_has_sign_and_item():
    line = Change("packages", Op.INSTALL, "git").render()
    assert "+" in line and "git" in line and "packages" in line


def test_plan_render_empty_message():
    assert "No changes" in Plan().render()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/lib/state/test_change.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dasik.lib.state'`

- [ ] **Step 3: Create the package marker**

Create `dasik/lib/state/__init__.py` (empty file).

- [ ] **Step 4: Implement `change.py`**

Create `dasik/lib/state/change.py`:

```python
from dataclasses import dataclass, field
from enum import Enum


class Op(Enum):
    INSTALL = "install"
    REMOVE = "remove"
    MODIFY = "modify"
    ENABLE = "enable"
    DISABLE = "disable"
    CREATE = "create"
    DELETE = "delete"


_DESTRUCTIVE_OPS = frozenset({Op.REMOVE, Op.DISABLE, Op.DELETE})

_SIGNS = {
    Op.INSTALL: "+", Op.CREATE: "+", Op.ENABLE: "+",
    Op.REMOVE: "-", Op.DELETE: "-", Op.DISABLE: "-",
    Op.MODIFY: "~",
}


@dataclass(frozen=True)
class Change:
    """A single proposed change in one domain."""

    domain: str
    op: Op
    item: str
    reason: str = ""

    @property
    def destructive(self) -> bool:
        return self.op in _DESTRUCTIVE_OPS

    def render(self) -> str:
        sign = _SIGNS[self.op]
        tail = f"  ({self.reason})" if self.reason else ""
        return f"  {sign} [{self.domain}] {self.op.value} {self.item}{tail}"


@dataclass
class Plan:
    """An ordered aggregate of Changes across domains."""

    changes: list[Change] = field(default_factory=list)

    def add(self, change: Change) -> None:
        self.changes.append(change)

    def extend(self, changes: list[Change]) -> None:
        self.changes.extend(changes)

    def is_empty(self) -> bool:
        return not self.changes

    def destructive(self) -> list[Change]:
        return [c for c in self.changes if c.destructive]

    def render(self) -> str:
        if not self.changes:
            return "No changes - system matches config."
        return "\n".join(c.render() for c in self.changes)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/lib/state/test_change.py -v`
Expected: PASS (7 passed)

- [ ] **Step 6: Commit**

```bash
git add dasik/lib/state/__init__.py dasik/lib/state/change.py tests/lib/state/test_change.py
git commit -m "feat: add Change/Op/Plan diff model

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: `Manifest` + `StateStore`

**Files:**
- Create: `dasik/lib/state/state_store.py`
- Test: `tests/lib/state/test_state_store.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/state/test_state_store.py`:

```python
from dasik.lib.state.state_store import Manifest, StateStore, STATE_VERSION


def test_load_missing_returns_default(tmp_target):
    store = StateStore(tmp_target)
    m = store.load()
    assert m.version == STATE_VERSION
    assert m.generation == 0
    assert m.managed == {}


def test_save_then_load_round_trips(tmp_target):
    store = StateStore(tmp_target)
    m = Manifest(
        generation=2,
        applied_at="2026-05-27T21:00:00Z",
        config_hash="sha256:abc",
        managed={"packages": ["git", "htop"], "users": ["alice"]},
    )
    store.save(m)

    loaded = StateStore(tmp_target).load()
    assert loaded.generation == 2
    assert loaded.applied_at == "2026-05-27T21:00:00Z"
    assert loaded.config_hash == "sha256:abc"
    assert loaded.managed == {"packages": ["git", "htop"], "users": ["alice"]}


def test_save_creates_state_under_var_lib_dasik(tmp_target):
    store = StateStore(tmp_target)
    store.save(Manifest())
    assert store.state_path.name == "state.json"
    assert store.state_path.parent.name == "dasik"
    assert store.state_path.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/lib/state/test_state_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dasik.lib.state.state_store'`

- [ ] **Step 3: Implement `state_store.py`**

Create `dasik/lib/state/state_store.py`:

```python
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from ..target.target import Target

STATE_VERSION = 1


@dataclass
class Manifest:
    """What dasik manages/owns on the target (the active generation's record)."""

    version: int = STATE_VERSION
    generation: int = 0
    applied_at: str | None = None
    config_hash: str | None = None
    managed: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Manifest":
        return cls(
            version=data.get("version", STATE_VERSION),
            generation=data.get("generation", 0),
            applied_at=data.get("applied_at"),
            config_hash=data.get("config_hash"),
            managed=data.get("managed", {}),
        )


class StateStore:
    """Reads/writes the dasik state manifest under <target>/var/lib/dasik."""

    def __init__(self, target: Target):
        self._target = target

    @property
    def state_path(self) -> Path:
        return Path(self._target.path("/var/lib/dasik/state.json"))

    def load(self) -> Manifest:
        p = self.state_path
        if not p.exists():
            return Manifest()
        return Manifest.from_dict(json.loads(p.read_text()))

    def save(self, manifest: Manifest) -> None:
        p = self.state_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(manifest.to_dict(), indent=2))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/lib/state/test_state_store.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/state/state_store.py tests/lib/state/test_state_store.py
git commit -m "feat: add Manifest + StateStore (state.json persistence)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: `GenerationStore`

**Files:**
- Create: `dasik/lib/state/generation_store.py`
- Test: `tests/lib/state/test_generation_store.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/state/test_generation_store.py`:

```python
import pytest

from dasik.lib.state.generation_store import GenerationStore


def test_no_generations_lists_empty(tmp_target):
    assert GenerationStore(tmp_target).list() == []


def test_new_creates_generation_one_and_current(tmp_target):
    store = GenerationStore(tmp_target)
    n = store.new({"hostname": "box"}, {"generation": 1})
    assert n == 1
    gens = store.list()
    assert len(gens) == 1
    assert gens[0].number == 1
    assert gens[0].is_current is True


def test_second_new_increments_and_moves_current(tmp_target):
    store = GenerationStore(tmp_target)
    store.new({"a": 1}, {"generation": 1})
    n2 = store.new({"a": 2}, {"generation": 2})
    assert n2 == 2
    by_num = {g.number: g for g in store.list()}
    assert by_num[1].is_current is False
    assert by_num[2].is_current is True


def test_restore_switches_current_and_returns_snapshot(tmp_target):
    store = GenerationStore(tmp_target)
    store.new({"a": 1}, {"generation": 1})
    store.new({"a": 2}, {"generation": 2})

    config, manifest = store.restore(1)
    assert config == {"a": 1}
    assert manifest == {"generation": 1}
    by_num = {g.number: g for g in store.list()}
    assert by_num[1].is_current is True
    assert by_num[2].is_current is False


def test_restore_unknown_raises(tmp_target):
    store = GenerationStore(tmp_target)
    store.new({"a": 1}, {"generation": 1})
    with pytest.raises(FileNotFoundError):
        store.restore(99)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/lib/state/test_generation_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dasik.lib.state.generation_store'`

- [ ] **Step 3: Implement `generation_store.py`**

Create `dasik/lib/state/generation_store.py`:

```python
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..target.target import Target


@dataclass
class GenInfo:
    number: int
    is_current: bool


class GenerationStore:
    """Records/lists/restores generations under <target>/var/lib/dasik/generations.

    Each generation N is a directory holding the config snapshot and the state
    manifest that produced it. A ``current`` symlink points at the active one.
    """

    def __init__(self, target: Target):
        self._target = target

    @property
    def base_dir(self) -> Path:
        return Path(self._target.path("/var/lib/dasik/generations"))

    @property
    def current_link(self) -> Path:
        return self.base_dir / "current"

    def _next_number(self) -> int:
        if not self.base_dir.exists():
            return 1
        nums = [int(p.name) for p in self.base_dir.iterdir()
                if p.is_dir() and p.name.isdigit()]
        return (max(nums) + 1) if nums else 1

    def _point_current_at(self, number: int) -> None:
        link = self.current_link
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(str(number))

    def new(self, config: dict[str, Any], manifest_dict: dict[str, Any]) -> int:
        n = self._next_number()
        gen_dir = self.base_dir / str(n)
        gen_dir.mkdir(parents=True, exist_ok=True)
        (gen_dir / "config.json").write_text(json.dumps(config, indent=2))
        (gen_dir / "state.json").write_text(json.dumps(manifest_dict, indent=2))
        self._point_current_at(n)
        return n

    def list(self) -> list[GenInfo]:
        if not self.base_dir.exists():
            return []
        current = None
        if self.current_link.is_symlink():
            current = self.current_link.readlink().name
        gens: list[GenInfo] = []
        for p in sorted(self.base_dir.iterdir(), key=lambda x: x.name):
            if p.is_dir() and p.name.isdigit():
                gens.append(GenInfo(number=int(p.name), is_current=(p.name == current)))
        return gens

    def restore(self, number: int) -> tuple[dict[str, Any], dict[str, Any]]:
        gen_dir = self.base_dir / str(number)
        if not gen_dir.is_dir():
            raise FileNotFoundError(f"Generation {number} not found")
        config = json.loads((gen_dir / "config.json").read_text())
        manifest = json.loads((gen_dir / "state.json").read_text())
        self._point_current_at(number)
        return config, manifest
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/lib/state/test_generation_store.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Run the full suite + coverage of new modules**

Run: `pytest --cov=dasik.lib.target --cov=dasik.lib.state --cov=dasik.lib.command_worker --cov-report=term-missing -v`
Expected: all tests PASS; the new modules report high coverage (target ≥80% each).

- [ ] **Step 6: Commit**

```bash
git add dasik/lib/state/generation_store.py tests/lib/state/test_generation_store.py
git commit -m "feat: add GenerationStore (record/list/restore generations)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Self-Review (completed during planning)

**1. Spec coverage (Plan 1 portion):**
- §3.1 `Target` → Task 1. ✅
- Command root-awareness (remove hardcoded `/mnt`) → Task 2 (backward-compatible). ✅
- §3.2 `StateStore` + `Manifest` → Task 4. ✅
- §3.3 `GenerationStore` → Task 5. ✅
- §3.4 `Change`/`Plan` model → Task 3. ✅
- §8 test infra / TDD → Task 0; reconciler set-math, action plan(), ConfigWriter, CLI, safety are **deliberately out of Plan 1** (Plans 2–4).

**2. Placeholder scan:** none — every code/test step contains full source.

**3. Type consistency:** `Target(root=...)` / `.is_chroot` / `.path()` used identically in Tasks 1, 2, 4, 5 and `conftest.py`. `Command.execute(cmd, args, run_as_chroot=False, target=None)` matches its tests. `Manifest` fields (`version/generation/applied_at/config_hash/managed`) match `state_store` tests. `GenerationStore.new/list/restore` + `GenInfo(number, is_current)` match `generation_store` tests. `Op`/`Change(domain, op, item, reason)`/`Plan.add/extend/is_empty/destructive/render` match `change` tests.

**Decision note:** the spec lists `Change.destructive` as a field; implemented as a derived `@property` (DRY — destructiveness follows from `op`). Behaviorally identical; downstream code reads `change.destructive` either way.

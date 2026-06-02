# sync-reflects-reality + per-verb tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `sync` reflect reality for the set domains (capture present-but-undeclared items) and add deterministic in-process integration tests for every CLI verb.

**Architecture:** `import_state` for `packages`/`systemd`/`users` captures `A (present) + declared intent`, independent of the manifest `M` (M is still recorded by `Reconciler.sync`). New `tests/cli/test_verbs_integration.py` drives `main([...])` against a `tmp_path` fake root with `Command.execute`/`subprocess.run` mocked, exercising the real registry.

**Tech Stack:** Python 3.10+, pytest/pytest-cov.

Spec: `docs/superpowers/specs/2026-05-31-sync-reflects-reality-and-verb-tests-design.md`.

**Test runner:**
```bash
python -m venv /tmp/dasik-venv && /tmp/dasik-venv/bin/pip install -q pytest pytest-cov colorama pydantic
PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest ...
```

---

## Task 1: `PackagesAction.import_state` reflects reality

**Files:**
- Modify: `dasik/lib/actions/packages_action.py`
- Test: `tests/lib/actions/test_packages_action_v3.py` (append)

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_import_state_captures_owned_present_undeclared():
    """Present + owned (M) but NOT declared (D) must still be captured (reality)."""
    fake = _fake_command_run(stdout=b"git\nhtop\nvim\n")  # A = git,htop,vim
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=["git"], context=_ctx("/"))
        frag = a.import_state(managed=["htop"])   # htop owned, not declared
    # htop (owned+present) AND vim (drift) both captured; git declared kept
    assert frag == {"packages": ["git", "htop", "vim"]}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_packages_action_v3.py -k owned_present -v`
Expected: FAIL — `htop` dropped (currently `{"packages": ["git", "vim"]}`).

- [ ] **Step 3: Implement (drop M from capture)**

Replace the body of `import_state` in `dasik/lib/actions/packages_action.py` (keep the
docstring's intent but update it) so it no longer uses `managed`:

```python
    def import_state(self, managed: "list[str] | None" = None) -> dict:
        """Capture reality into the config fragment (sync): keep declared tokens
        (intent, ``aur-`` prefix preserved) and append everything present that is
        not declared. Independent of the manifest M — sync reflects reality.
        """
        actual = self.actual()
        original: List[str] = list(self.config) if isinstance(self.config, list) else []

        def _strip(token: str) -> str:
            return token[len(AUR_PREFIX):] if token.startswith(AUR_PREFIX) else token

        declared_stripped = {_strip(t) for t in original}
        extra = sorted(actual - declared_stripped)   # present, not declared
        return {self._PACMAN_DOMAIN: original + extra}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_packages_action_v3.py -v`
Expected: PASS. (Note: an existing test that asserted owned-vanished was dropped may need
updating — `import_state` now keeps declared intent regardless of M; update any such test to
the new "reality + intent" expectation.)

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/packages_action.py tests/lib/actions/test_packages_action_v3.py
git commit -m "fix(packages): sync import_state captures present-but-undeclared (reality)"
```

---

## Task 2: `SystemdAction.import_state` reflects reality

**Files:**
- Modify: `dasik/lib/actions/systemd_action.py`
- Test: `tests/lib/actions/test_systemd_action.py` (append)

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_import_state_captures_owned_present_undeclared_unit():
    a = _action({"enable_units": ["sshd.service"]},
                actual=["sshd.service", "docker.service"])
    frag = a.import_state(managed=["docker.service"])   # docker owned, not declared
    assert "docker.service" in frag["systemd"]["enable_units"]
    assert "sshd.service" in frag["systemd"]["enable_units"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_systemd_action.py -k owned_present -v`
Expected: FAIL — `docker.service` excluded (it is in `managed`, so dropped from drift).

- [ ] **Step 3: Implement (drop M)**

In `dasik/lib/actions/systemd_action.py`, change `import_state`:

```python
    def import_state(self, managed=None) -> dict:
        actual = self.actual()
        d_off = set(self._d_off())

        kept_units = list(self.units)
        kept_sockets = list(self.sockets)

        drift = sorted(actual - set(self._d_on()) - d_off)   # present, not declared (no M)
        socket_drift = [d for d in drift if d.endswith(".socket")]
        unit_drift = [d for d in drift if not d.endswith(".socket")]

        return {self._SYSTEMD_DOMAIN: {
            "enable_units": kept_units + unit_drift,
            "enable_sockets": kept_sockets + socket_drift,
            "disable_units": list(self.disable_units),
        }}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_systemd_action.py -v`
Expected: PASS. (Update the older `test_import_state_drops_owned_but_vanished` if present:
declared units are now kept as intent regardless of M; an undeclared owned-vanished unit is
not in A so it still does not appear — adjust the test's expectation/name accordingly.)

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/systemd_action.py tests/lib/actions/test_systemd_action.py
git commit -m "fix(systemd): sync import_state captures present-but-undeclared units (reality)"
```

---

## Task 3: `UsersAction.import_state` reflects reality

**Files:**
- Modify: `dasik/lib/actions/users_action.py`
- Test: `tests/lib/actions/test_users_action.py` (append)

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_import_state_captures_owned_present_undeclared_user():
    a = _v3([{"username": "alice", "hashed_password": "$6$a$h"}],
            actual=["alice", "carol"],
            shells={"alice": "/bin/bash", "carol": "/bin/bash"},
            groups={"alice": [], "carol": ["wheel"]},
            hashes={"alice": "$6$a$h", "carol": "$6$c$h"})
    frag = a.import_state(managed=["carol"])   # carol owned, not declared
    names = [u["username"] for u in frag["users"]]
    assert "carol" in names and "alice" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_users_action.py -k owned_present -v`
Expected: FAIL — `carol` excluded (in `managed`, so dropped from drift).

- [ ] **Step 3: Implement (drop M)**

In `dasik/lib/actions/users_action.py`, change `import_state`:

```python
    def import_state(self, managed=None) -> dict:
        actual = self.actual()

        result = []
        declared_names = set()
        for u in self.users:
            name = u["username"]
            declared_names.add(name)
            if name in actual and name != "root":
                result.append(self._capture(name))   # refresh from reality
            else:
                result.append(u)                     # intent / root kept as-is

        drift = sorted(actual - declared_names)       # present, not declared (no M)
        for name in drift:
            captured = self._capture(name)
            if captured["hashed_password"]:           # skip users without a readable hash
                result.append(captured)
        return {self._USERS_DOMAIN: result}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_users_action.py -v`
Expected: PASS. (Update the older `test_import_state_drops_owned_but_vanished` if present:
declared users are kept as intent; an undeclared owned-vanished user is not in A so it does
not appear — adjust accordingly.)

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/users_action.py tests/lib/actions/test_users_action.py
git commit -m "fix(users): sync import_state captures present-but-undeclared users (reality)"
```

---

## Task 4: Per-verb in-process integration tests

**Files:**
- Create: `tests/cli/test_verbs_integration.py`

- [ ] **Step 1: Write the harness + verb tests**

Create `tests/cli/test_verbs_integration.py`:

```python
"""In-process integration tests for every CLI verb.

Drives dasik.__main__.main([...]) against a tmp_path fake root with the system
commands mocked, exercising the REAL action registry (unlike tests/test_cli_*,
which mock the registry out). Deterministic; never touches the host.
"""
import json
from unittest.mock import MagicMock, patch

from dasik.__main__ import main


def _fake_exec(table=None):
    """Command.execute / subprocess.run replacement.

    `table` maps (cmd, args[0]) -> stdout bytes; default empty stdout, rc 0.
    """
    table = table or {}

    def run(cmd, args=None, *a, **k):
        if isinstance(cmd, (list, tuple)):          # subprocess.run(["arch-chroot", ...])
            key = (cmd[0], cmd[1] if len(cmd) > 1 else "")
        else:                                       # Command.execute("pacman", ["-Qqe"])
            key = (cmd, (args or [""])[0] if args else "")
        out = table.get(key, b"")
        return MagicMock(stdout=out, stderr=b"", returncode=0)

    return run


def _invoke(argv, table=None):
    with patch("dasik.lib.command_worker.command_worker.Command.execute",
               side_effect=_fake_exec(table)), \
         patch("subprocess.run", side_effect=_fake_exec(table)):
        return main(argv)


def _write(tmp_path, cfg):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg))
    return p


_MINIMAL = {
    "locales": {"selected_locales": [], "desired_locale": "en_US.UTF-8",
                "desired_tty_layout": "us"},
    "timezone": {"region": "Europe", "city": "Madrid"},
    "network": {"type": "NetworkManager", "add_default_hosts": True},
    "hostname": "arch",
}


def test_plan_runs_readonly_and_exits_zero(tmp_path, capsys):
    cfg = dict(_MINIMAL, packages=["git"])
    p = _write(tmp_path, cfg)
    code = _invoke(["plan", str(p), "--target", str(tmp_path)],
                   table={("pacman", "-Qqe"): b""})   # nothing installed
    assert code == 0
    out = capsys.readouterr().out
    assert "git" in out                         # plan shows the install
    assert not (tmp_path / "var/lib/dasik/state.json").exists()  # plan writes nothing


def test_apply_writes_state_and_generation(tmp_path):
    cfg = dict(_MINIMAL, packages=["git"])
    p = _write(tmp_path, cfg)
    code = _invoke(["apply", str(p), "--target", str(tmp_path), "--yes"],
                   table={("pacman", "-Qqe"): b""})
    assert code == 0
    assert (tmp_path / "var/lib/dasik/state.json").exists()
    assert (tmp_path / "var/lib/dasik/generations/1").is_dir()


def test_apply_is_idempotent_second_run_no_generation_2(tmp_path):
    cfg = dict(_MINIMAL, packages=["git"])
    p = _write(tmp_path, cfg)
    table = {("pacman", "-Qqe"): b"git\n"}      # git already installed
    _invoke(["apply", str(p), "--target", str(tmp_path), "--yes"], table=table)
    code = _invoke(["apply", str(p), "--target", str(tmp_path), "--yes"], table=table)
    assert code == 0
    # converged second run records no new generation
    assert not (tmp_path / "var/lib/dasik/generations/2").exists()


def test_sync_captures_reality_into_config(tmp_path):
    cfg = dict(_MINIMAL, packages=["git"])
    p = _write(tmp_path, cfg)
    # reality has more than declared: htop present, undeclared
    code = _invoke(["sync", str(p), "--target", str(tmp_path)],
                   table={("pacman", "-Qqe"): b"git\nhtop\n"})
    assert code == 0
    new = json.loads(p.read_text())
    assert "git" in new["packages"] and "htop" in new["packages"]
    assert (tmp_path / "config.json.bak").exists()


def test_generations_lists_after_apply(tmp_path, capsys):
    cfg = dict(_MINIMAL, packages=["git"])
    p = _write(tmp_path, cfg)
    _invoke(["apply", str(p), "--target", str(tmp_path), "--yes"],
            table={("pacman", "-Qqe"): b""})
    capsys.readouterr()
    code = _invoke(["generations", "--target", str(tmp_path)])
    assert code == 0
    assert "Generation 1" in capsys.readouterr().out


def test_rollback_restores_previous_generation(tmp_path):
    p = _write(tmp_path, dict(_MINIMAL, packages=["git"]))
    _invoke(["apply", str(p), "--target", str(tmp_path), "--yes"],
            table={("pacman", "-Qqe"): b""})            # gen 1 (git)
    _write(tmp_path, dict(_MINIMAL, packages=["git", "htop"]))
    _invoke(["apply", str(p), "--target", str(tmp_path), "--yes"],
            table={("pacman", "-Qqe"): b"git\n"})       # gen 2 (git, htop)
    code = _invoke(["rollback", "1", "--target", str(tmp_path), "--yes"],
                   table={("pacman", "-Qqe"): b"git\nhtop\n"})
    assert code == 0
```

- [ ] **Step 2: Run the verb tests**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/cli/test_verbs_integration.py -v`
Expected: PASS. If a test reveals a real verb bug, fix the product code (that is the point of
these tests) — do not weaken the assertion. If an assertion is too strict for incidental
behaviour (e.g. extra domains acting on the minimal config), narrow it to the verb's
contract (exit code + the targeted file/effect), not the exact full output.

- [ ] **Step 3: Commit**

```bash
git add tests/cli/test_verbs_integration.py
git commit -m "test(cli): in-process integration tests for every verb (real registry, fake root)"
```

---

## Task 5: Full suite + gate

- [ ] **Step 1: Full suite + coverage**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest --cov=dasik -q`
Expected: all pass; `Required test coverage of 80.0% reached`.

- [ ] **Step 2: Sanity — sample still parses**

Run:
```bash
PYTHONPATH=. /tmp/dasik-venv/bin/python -c "from dasik.lib.json_parser.json_parser import JsonParser; JsonParser('config/install-megamix.json').debug(); print('OK')"
```
Expected: `OK`.

- [ ] **Step 3: Commit (if any test tweaks were needed)**

```bash
git add -A && git commit -m "test: adjust import_state expectations for reality-reflecting sync"
```

---

## Self-Review notes

- **Spec coverage:** Tasks 1-3 = sync-reflects-reality for packages/systemd/users; Task 4 =
  per-verb integration harness + tests; Task 5 = gate. All spec sections covered.
- **Type consistency:** `import_state(managed=None)` signature kept (param now ignored);
  domains `packages`/`systemd`/`users`; harness `_fake_exec`/`_invoke`/`_write`. Consistent.
- **M still recorded:** only `import_state`'s captured fragment changes; `Reconciler.sync`
  still sets `new_managed[domain] = sorted(action.actual())`, so the manifest keeps tracking
  ownership for `apply`'s REMOVE math. (apply/plan unchanged.)
- **Existing tests:** the older `*_drops_owned_but_vanished` import_state tests encode the old
  M-aware behaviour; update them to the reality-reflecting expectation (declared kept as
  intent; undeclared-vanished simply absent from A). Tasks 1-3 Step 4 call this out.
- **Harness tolerance:** verb tests assert each verb's contract (exit code, state/generation,
  config rewrite) rather than exact full output, so incidental domains acting on the minimal
  config do not make them brittle.
```

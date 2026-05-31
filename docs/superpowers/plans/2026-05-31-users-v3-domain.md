# users v3-domain Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the `users` domain to the v3 `plan`/`apply`/`sync` contract with attribute-aware reconciliation (shell, groups, hashed password) on top of name-set CREATE/DELETE.

**Architecture:** `set_math.compute_changes` computes CREATE/DELETE over the username set; `UsersAction` adds an action-local MODIFY layer comparing `shell`/`groups`/`hashed_password` against the target's `/etc/passwd`, `/etc/group`, `/etc/shadow`. Passwords are stored hashed in the config and compared directly. `actual()` is scoped to uid≥1000; `root` is special-cased (password-only). A root-level `remove_home_on_delete` flag (read because the action is registered `__root__`) decides `userdel -r`.

**Tech Stack:** Python 3.10+, pydantic, pytest/pytest-cov, `useradd`/`usermod`/`userdel` via `Command.execute`.

Spec: `docs/superpowers/specs/2026-05-31-users-v3-domain-design.md`.

**Test runner:**
```bash
python -m venv /tmp/dasik-venv && /tmp/dasik-venv/bin/pip install -q pytest pytest-cov colorama pydantic
PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest ...
```

---

## Task 1: Model — `hashed_password` + `remove_home_on_delete`

**Files:**
- Modify: `dasik/lib/models/user_model.py`
- Modify: `dasik/lib/models/json_model.py:36` (add root field)
- Test: `tests/lib/models/test_user_model.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/models/test_user_model.py`:

```python
import pytest

from dasik.lib.models.user_model import UserModel
from dasik.lib.models.json_model import JsonModel


_HASH = "$6$abc$" + "x" * 86


def test_accepts_hashed_password():
    m = UserModel(username="alice", hashed_password=_HASH)
    assert m.hashed_password == _HASH
    assert m.shell == "/bin/bash"
    assert m.groups == []


def test_rejects_plaintext_password():
    with pytest.raises(ValueError):
        UserModel(username="alice", hashed_password="hunter2")


def test_json_model_remove_home_on_delete_defaults_false():
    m = JsonModel(
        locales={"selected_locales": ["en_US.UTF-8 UTF-8"],
                 "desired_locale": "en_US.UTF-8", "desired_tty_layout": "us"},
        timezone={"region": "Europe", "city": "Madrid"},
        network={"type": "NetworkManager", "add_default_hosts": True},
        hostname="arch",
    )
    assert m.remove_home_on_delete is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/models/test_user_model.py -v`
Expected: FAIL — `hashed_password` not a field; `remove_home_on_delete` missing.

- [ ] **Step 3: Implement the model changes**

Replace `dasik/lib/models/user_model.py`:

```python
"""Models for user configuration."""
from typing import List
from pydantic import BaseModel, Field, field_validator


class UserModel(BaseModel):
    """A system user to create. Password is stored already hashed."""
    username: str = Field(..., description="Login name")
    hashed_password: str = Field(..., description="Crypt hash ($6$salt$hash), e.g. openssl passwd -6")
    shell: str = Field(default="/bin/bash", description="Login shell path")
    groups: List[str] = Field(default_factory=list, description="Supplementary groups")

    @field_validator("hashed_password")
    @classmethod
    def _must_be_hash(cls, v: str) -> str:
        if not v.startswith("$"):
            raise ValueError(
                "hashed_password must be a crypt hash (e.g. $6$...); "
                "plaintext passwords are not accepted"
            )
        return v
```

In `dasik/lib/models/json_model.py`, add under the "Toggles" block (after `enable_trim`):

```python
    remove_home_on_delete: bool = False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/models/test_user_model.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/models/user_model.py dasik/lib/models/json_model.py tests/lib/models/test_user_model.py
git commit -m "feat(models): hashed_password on UserModel + remove_home_on_delete root flag"
```

---

## Task 2: `UsersAction` — constructor (list|dict), `actual()`, attr readers; register `__root__`

**Files:**
- Modify: `dasik/lib/actions/users_action.py`
- Modify: `dasik/lib/actions/actions_handler_v2.py:100-104`
- Test: `tests/lib/actions/test_users_action.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/lib/actions/test_users_action.py`:

```python
from unittest.mock import patch as _patch

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target

_PASSWD_UIDS = (
    "root:x:0:0::/root:/bin/bash\n"
    "bin:x:1:1::/:/usr/bin/nologin\n"
    "alice:x:1000:1000::/home/alice:/usr/bin/zsh\n"
    "bob:x:1001:1001::/home/bob:/bin/bash\n"
)
_SHADOW = "root:$6$r$roothash:::::::\nalice:$6$a$alicehash:::::::\n"


def _ctx(root="/"):
    return ActionContext(target=Target(root=root))


def _open_tree(passwd=_PASSWD_UIDS, group="wheel:x:998:alice\n", shadow=_SHADOW):
    def opener(path, *a, **k):
        from unittest.mock import mock_open
        p = str(path)
        data = passwd if "passwd" in p else group if "group" in p else shadow
        return mock_open(read_data=data)()
    return _patch("builtins.open", side_effect=opener)


def test_actual_includes_only_uid_ge_1000():
    a = UsersAction([], _ctx("/"))
    with _open_tree():
        assert a.actual() == {"alice", "bob"}   # root/bin excluded


def test_actual_empty_without_target():
    a = UsersAction([], None)
    assert a.actual() == set()


def test_reads_shell_groups_hash_from_target():
    a = UsersAction([], _ctx("/"))
    with _open_tree():
        assert a._shell("alice") == "/usr/bin/zsh"
        assert a._groups("alice") == {"wheel"}
        assert a._hash("alice") == "$6$a$alicehash"


def test_constructor_accepts_root_dict_and_flag():
    a = UsersAction(
        {"users": [{"username": "alice", "hashed_password": "$6$x$h"}],
         "remove_home_on_delete": True}
    )
    assert [u["username"] for u in a.users] == ["alice"]
    assert a.remove_home_on_delete is True


def test_constructor_accepts_bare_list_legacy():
    a = UsersAction([{"username": "alice", "hashed_password": "$6$x$h"}])
    assert a.users[0]["username"] == "alice"
    assert a.remove_home_on_delete is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_users_action.py -k "actual or shell_groups or constructor" -v`
Expected: FAIL — `actual`/`_shell`/`_hash` not defined; constructor ignores dict/flag.

- [ ] **Step 3: Implement constructor, target-aware readers, `actual()`**

In `dasik/lib/actions/users_action.py`, replace the imports + `__init__` and add readers.
New top + constructor:

```python
"""Action: create/modify/delete users declaratively.

v3 domain "users": CREATE/DELETE by username (set-math) + MODIFY for
shell/groups/hashed_password drift. Passwords are stored hashed and compared
against /etc/shadow. actual() is scoped to uid>=1000; root is special-cased.
"""
from typing import Any, Dict, List
from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command
from ..state.change import Change, Op


class UsersAction(AbstractAction):
    """Create system users from the declarative config."""

    _USERS_DOMAIN = "users"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        if isinstance(config, list):
            self.users: List[Dict[str, Any]] = config
            self.remove_home_on_delete: bool = False
        elif isinstance(config, dict):
            self.users = config.get("users", [])
            self.remove_home_on_delete = config.get("remove_home_on_delete", False)
        else:
            self.users = []
            self.remove_home_on_delete = False
        self._by_name: Dict[str, Dict[str, Any]] = {u["username"]: u for u in self.users}
```

Keep the `name`/`is_optional` properties. Add target-aware path + readers (these
supersede the hardcoded `/mnt` statics; keep `_user_exists` for the legacy path but
re-point it through `_passwd_path`):

```python
    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _passwd_path(self) -> str:
        t = self._target()
        return t.path("/etc/passwd") if t is not None else "/mnt/etc/passwd"

    def _group_path(self) -> str:
        t = self._target()
        return t.path("/etc/group") if t is not None else "/mnt/etc/group"

    def _shadow_path(self) -> str:
        t = self._target()
        return t.path("/etc/shadow") if t is not None else "/mnt/etc/shadow"

    def actual(self) -> set:
        """Usernames with uid >= 1000 on the target (excludes system accounts/root)."""
        if self._target() is None:
            return set()
        names = set()
        try:
            with open(self._passwd_path(), "r") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) >= 3 and parts[2].isdigit() and int(parts[2]) >= 1000:
                        names.add(parts[0])
        except FileNotFoundError:
            pass
        return names

    def _shell(self, username: str) -> str:
        try:
            with open(self._passwd_path(), "r") as f:
                for line in f:
                    if line.startswith(f"{username}:"):
                        return line.rstrip("\n").split(":")[-1]
        except FileNotFoundError:
            pass
        return ""

    def _groups(self, username: str) -> set:
        groups: set = set()
        try:
            with open(self._group_path(), "r") as f:
                for line in f:
                    fields = line.strip().split(":")
                    members = fields[3].split(",") if len(fields) > 3 and fields[3] else []
                    if username in members:
                        groups.add(fields[0])
        except FileNotFoundError:
            pass
        return groups

    def _hash(self, username: str) -> str:
        try:
            with open(self._shadow_path(), "r") as f:
                for line in f:
                    if line.startswith(f"{username}:"):
                        return line.split(":")[1]
        except (FileNotFoundError, IndexError):
            pass
        return ""
```

Note: delete the old `@staticmethod _get_user_shell` / `_get_user_groups` (replaced by
`_shell`/`_groups`); keep `_user_exists` but make it use `_passwd_path()`:

```python
    def _user_exists(self, username: str) -> bool:
        try:
            with open(self._passwd_path(), "r") as f:
                for line in f:
                    if line.startswith(f"{username}:"):
                        return True
        except FileNotFoundError:
            pass
        return False
```

In `dasik/lib/actions/actions_handler_v2.py`, change the registration:

```python
    register_action(
        action_class=UsersAction,
        config_key='__root__',   # needs root-level remove_home_on_delete + users list
        is_optional=True,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_users_action.py -v`
Expected: PASS (new + existing legacy tests — the legacy tests construct `UsersAction([...])`, still valid).

> If a legacy test referenced the removed `_get_user_shell`/`_get_user_groups`, update it
> to `_shell`/`_groups`. Re-run until green.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/users_action.py dasik/lib/actions/actions_handler_v2.py tests/lib/actions/test_users_action.py
git commit -m "feat(users): target-aware actual()/readers + __root__ registration"
```

---

## Task 3: `UsersAction.plan()` + `managed_keys()`

**Files:**
- Modify: `dasik/lib/actions/users_action.py`
- Test: `tests/lib/actions/test_users_action.py` (append)

- [ ] **Step 1: Write the failing tests**

Append:

```python
def _v3(cfg, actual, shells=None, groups=None, hashes=None):
    a = UsersAction(cfg, _ctx("/"))
    a.actual = lambda: set(actual)
    a._shell = lambda u: (shells or {}).get(u, "/bin/bash")
    a._groups = lambda u: set((groups or {}).get(u, []))
    a._hash = lambda u: (hashes or {}).get(u, "")
    return a


def test_plan_creates_missing_user():
    a = _v3([{"username": "alice", "hashed_password": "$6$a$h"}], actual=[])
    changes = a.plan(managed=[])
    assert [(c.op, c.item) for c in changes] == [(Op.CREATE, "alice")]


def test_plan_deletes_owned_no_longer_declared():
    a = _v3([], actual=["old"])
    changes = a.plan(managed=["old"])
    assert [(c.op, c.item) for c in changes] == [(Op.DELETE, "old")]


def test_plan_modifies_on_shell_drift():
    a = _v3(
        [{"username": "alice", "hashed_password": "$6$a$h", "shell": "/bin/bash"}],
        actual=["alice"], shells={"alice": "/usr/bin/zsh"},
        groups={"alice": []}, hashes={"alice": "$6$a$h"},
    )
    changes = a.plan(managed=["alice"])
    assert [(c.op, c.item) for c in changes] == [(Op.MODIFY, "alice")]
    assert "shell" in changes[0].reason


def test_plan_modifies_on_groups_drift():
    a = _v3(
        [{"username": "alice", "hashed_password": "$6$a$h", "groups": ["wheel"]}],
        actual=["alice"], shells={"alice": "/bin/bash"},
        groups={"alice": []}, hashes={"alice": "$6$a$h"},
    )
    changes = a.plan(managed=["alice"])
    assert changes[0].op is Op.MODIFY and "groups" in changes[0].reason


def test_plan_modifies_on_password_drift():
    a = _v3(
        [{"username": "alice", "hashed_password": "$6$NEW$h"}],
        actual=["alice"], shells={"alice": "/bin/bash"},
        groups={"alice": []}, hashes={"alice": "$6$OLD$h"},
    )
    changes = a.plan(managed=["alice"])
    assert changes[0].op is Op.MODIFY and "password" in changes[0].reason


def test_plan_root_password_modify_only():
    a = _v3(
        [{"username": "root", "hashed_password": "$6$NEW$h"}],
        actual=[], hashes={"root": "$6$OLD$h"},
    )
    changes = a.plan(managed=[])
    assert [(c.op, c.item) for c in changes] == [(Op.MODIFY, "root")]


def test_plan_empty_when_converged():
    a = _v3(
        [{"username": "alice", "hashed_password": "$6$a$h",
          "shell": "/bin/bash", "groups": ["wheel"]}],
        actual=["alice"], shells={"alice": "/bin/bash"},
        groups={"alice": ["wheel"]}, hashes={"alice": "$6$a$h"},
    )
    assert a.plan(managed=["alice"]) == []


def test_managed_keys_excludes_root():
    a = UsersAction([
        {"username": "alice", "hashed_password": "$6$a$h"},
        {"username": "root", "hashed_password": "$6$r$h"},
    ])
    assert a.managed_keys() == {"users": ["alice"]}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_users_action.py -k "plan or managed_keys" -v`
Expected: FAIL — base `plan` returns `[]`, `managed_keys` returns `{}`.

- [ ] **Step 3: Implement `plan` and `managed_keys`**

Add to `UsersAction`:

```python
    def _declared_non_root(self) -> List[str]:
        return [u["username"] for u in self.users if u["username"] != "root"]

    def _modify_reason(self, username: str) -> str:
        u = self._by_name[username]
        changed = []
        if u.get("shell", "/bin/bash") != self._shell(username):
            changed.append("shell")
        if set(u.get("groups", [])) != self._groups(username):
            changed.append("groups")
        if u["hashed_password"] != self._hash(username):
            changed.append("password")
        return ",".join(changed)

    def plan(self, managed):
        from ..state.set_math import compute_changes
        actual = self.actual()
        changes, _drift = compute_changes(
            self._USERS_DOMAIN,
            desired=self._declared_non_root(),
            managed=managed,
            actual=actual,
            op_install=Op.CREATE,
            op_remove=Op.DELETE,
        )
        # MODIFY layer for declared∩actual (non-root)
        for name in sorted(set(self._declared_non_root()) & actual):
            reason = self._modify_reason(name)
            if reason:
                changes.append(Change(self._USERS_DOMAIN, Op.MODIFY, name, reason=reason))
        # root: password-only MODIFY
        if "root" in self._by_name:
            if self._by_name["root"]["hashed_password"] != self._hash("root"):
                changes.append(Change(self._USERS_DOMAIN, Op.MODIFY, "root", reason="password"))
        return changes

    def managed_keys(self) -> dict:
        return {self._USERS_DOMAIN: self._declared_non_root()}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_users_action.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/users_action.py tests/lib/actions/test_users_action.py
git commit -m "feat(users): v3 plan() (CREATE/DELETE + MODIFY layer) + managed_keys()"
```

---

## Task 4: `UsersAction.apply()`

**Files:**
- Modify: `dasik/lib/actions/users_action.py`
- Test: `tests/lib/actions/test_users_action.py` (append)

- [ ] **Step 1: Write the failing tests**

Append:

```python
def test_apply_creates_user_with_shell_groups_and_hash():
    a = UsersAction(
        [{"username": "alice", "hashed_password": "$6$a$h",
          "shell": "/usr/bin/zsh", "groups": ["wheel", "audio"]}],
        _ctx("/"),
    )
    changes = [Change("users", Op.CREATE, "alice")]
    with _patch("dasik.lib.actions.users_action.Command.execute") as run:
        a.apply(changes)
    cmds = [(c.args[0], c.args[1]) for c in run.call_args_list]
    assert ("useradd", ["-m", "-s", "/usr/bin/zsh", "-G", "wheel,audio", "alice"]) in cmds
    assert ("usermod", ["-p", "$6$a$h", "alice"]) in cmds


def test_apply_modify_sets_shell_groups_hash():
    a = UsersAction(
        [{"username": "alice", "hashed_password": "$6$a$h",
          "shell": "/bin/bash", "groups": ["wheel"]}],
        _ctx("/"),
    )
    with _patch("dasik.lib.actions.users_action.Command.execute") as run:
        a.apply([Change("users", Op.MODIFY, "alice")])
    cmds = [(c.args[0], c.args[1]) for c in run.call_args_list]
    assert ("usermod", ["-s", "/bin/bash", "alice"]) in cmds
    assert ("usermod", ["-G", "wheel", "alice"]) in cmds
    assert ("usermod", ["-p", "$6$a$h", "alice"]) in cmds


def test_apply_modify_root_only_sets_password():
    a = UsersAction([{"username": "root", "hashed_password": "$6$r$h"}], _ctx("/"))
    with _patch("dasik.lib.actions.users_action.Command.execute") as run:
        a.apply([Change("users", Op.MODIFY, "root")])
    cmds = [(c.args[0], c.args[1]) for c in run.call_args_list]
    assert cmds == [("usermod", ["-p", "$6$r$h", "root"])]


def test_apply_delete_keeps_home_by_default():
    a = UsersAction([], _ctx("/"))
    with _patch("dasik.lib.actions.users_action.Command.execute") as run:
        a.apply([Change("users", Op.DELETE, "old")])
    assert run.call_args_list[0].args[:2] == ("userdel", ["old"])


def test_apply_delete_removes_home_when_flag_set():
    a = UsersAction({"users": [], "remove_home_on_delete": True}, _ctx("/"))
    with _patch("dasik.lib.actions.users_action.Command.execute") as run:
        a.apply([Change("users", Op.DELETE, "old")])
    assert run.call_args_list[0].args[:2] == ("userdel", ["-r", "old"])


def test_apply_create_before_delete():
    a = UsersAction([{"username": "new", "hashed_password": "$6$n$h"}], _ctx("/"))
    changes = [Change("users", Op.DELETE, "old"), Change("users", Op.CREATE, "new")]
    with _patch("dasik.lib.actions.users_action.Command.execute") as run:
        a.apply(changes)
    ops = [c.args[0] for c in run.call_args_list]
    assert ops.index("useradd") < ops.index("userdel")


def test_apply_noop_without_target():
    a = UsersAction([{"username": "x", "hashed_password": "$6$x$h"}], None)
    with _patch("dasik.lib.actions.users_action.Command.execute") as run:
        a.apply([Change("users", Op.CREATE, "x")])
    run.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_users_action.py -k apply_ -v`
Expected: FAIL — base `apply` is a no-op.

- [ ] **Step 3: Implement `apply`**

Add to `UsersAction`:

```python
    def apply(self, changes) -> None:
        target = self._target()
        if target is None:
            return
        creates = [c.item for c in changes if c.op is Op.CREATE]
        modifies = [c.item for c in changes if c.op is Op.MODIFY]
        deletes = [c.item for c in changes if c.op is Op.DELETE]

        for name in creates:
            u = self._by_name[name]
            argv = ["-m", "-s", u.get("shell", "/bin/bash")]
            groups = u.get("groups", [])
            if groups:
                argv += ["-G", ",".join(groups)]
            argv.append(name)
            Command.execute("useradd", argv, target=target)
            Command.execute("usermod", ["-p", u["hashed_password"], name], target=target)

        for name in modifies:
            u = self._by_name[name]
            if name != "root":
                Command.execute("usermod", ["-s", u.get("shell", "/bin/bash"), name], target=target)
                Command.execute("usermod", ["-G", ",".join(u.get("groups", [])), name], target=target)
            Command.execute("usermod", ["-p", u["hashed_password"], name], target=target)

        for name in deletes:
            argv = ["-r", name] if self.remove_home_on_delete else [name]
            Command.execute("userdel", argv, target=target)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_users_action.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/users_action.py tests/lib/actions/test_users_action.py
git commit -m "feat(users): v3 apply() routes useradd/usermod/userdel"
```

---

## Task 5: `UsersAction.import_state()` (sync)

**Files:**
- Modify: `dasik/lib/actions/users_action.py`
- Test: `tests/lib/actions/test_users_action.py` (append)

- [ ] **Step 1: Write the failing tests**

Append:

```python
def test_import_state_captures_drift_user_with_attrs():
    a = _v3(
        [{"username": "alice", "hashed_password": "$6$a$h",
          "shell": "/bin/bash", "groups": []}],
        actual=["alice", "carol"],
        shells={"alice": "/bin/bash", "carol": "/bin/bash"},
        groups={"alice": [], "carol": ["wheel"]},
        hashes={"alice": "$6$a$h", "carol": "$6$c$h"},
    )
    frag = a.import_state(managed=["alice"])
    users = {u["username"]: u for u in frag["users"]}
    assert "carol" in users
    assert users["carol"]["hashed_password"] == "$6$c$h"
    assert users["carol"]["groups"] == ["wheel"]


def test_import_state_drops_owned_but_vanished():
    a = _v3(
        [{"username": "alice", "hashed_password": "$6$a$h"},
         {"username": "gone", "hashed_password": "$6$g$h"}],
        actual=["alice"],
        shells={"alice": "/bin/bash"}, groups={"alice": []},
        hashes={"alice": "$6$a$h"},
    )
    frag = a.import_state(managed=["alice", "gone"])
    names = [u["username"] for u in frag["users"]]
    assert names == ["alice"]


def test_import_state_keeps_declared_intent_not_present():
    a = _v3(
        [{"username": "alice", "hashed_password": "$6$a$h"},
         {"username": "future", "hashed_password": "$6$f$h"}],
        actual=["alice"],
        shells={"alice": "/bin/bash"}, groups={"alice": []},
        hashes={"alice": "$6$a$h"},
    )
    frag = a.import_state(managed=[])
    names = [u["username"] for u in frag["users"]]
    assert "future" in names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_users_action.py -k import_state -v`
Expected: FAIL — base `import_state` returns `{}`.

- [ ] **Step 3: Implement `import_state`**

Add to `UsersAction`:

```python
    def _capture(self, username: str) -> dict:
        return {
            "username": username,
            "hashed_password": self._hash(username),
            "shell": self._shell(username),
            "groups": sorted(self._groups(username)),
        }

    def import_state(self, managed=None) -> dict:
        managed_set = set(managed or [])
        actual = self.actual()
        vanished = managed_set - actual                       # M \ A

        result = []
        declared_names = set()
        for u in self.users:
            name = u["username"]
            declared_names.add(name)
            if name in vanished:
                continue                                       # owned + gone → drop
            if name in actual and name != "root":
                result.append(self._capture(name))             # refresh from reality
            else:
                result.append(u)                               # intent / root kept as-is

        drift = sorted(actual - declared_names - managed_set)  # A \ D \ M
        result.extend(self._capture(name) for name in drift)
        return {self._USERS_DOMAIN: result}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_users_action.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/users_action.py tests/lib/actions/test_users_action.py
git commit -m "feat(users): v3 import_state() captures drift users with attrs (sync)"
```

---

## Task 6: Legacy `is_needed`/`execute`/`verify` use hash + remove_home_on_delete

**Files:**
- Modify: `dasik/lib/actions/users_action.py`
- Test: `tests/lib/actions/test_users_action.py` (append + fix any legacy refs)

The old executor path must keep working with the new field names.

- [ ] **Step 1: Write the failing tests**

Append:

```python
def test_legacy_is_needed_true_when_user_missing():
    a = UsersAction([{"username": "alice", "hashed_password": "$6$a$h"}], _ctx("/"))
    with _open_tree(passwd="root:x:0:0::/root:/bin/bash\n"):  # alice absent
        assert a.is_needed() is True


def test_legacy_verify_true_when_users_present():
    a = UsersAction([{"username": "alice", "hashed_password": "$6$a$h"}], _ctx("/"))
    with _open_tree():  # alice present (uid 1000)
        assert a.verify() is True
```

- [ ] **Step 2: Run tests to verify they fail (or legacy tests break)**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_users_action.py -v`
Expected: any legacy test still referencing `password` (plaintext) or `_get_user_*` fails;
the new legacy tests fail if `is_needed`/`execute` still read `u["password"]`.

- [ ] **Step 3: Update `is_needed`/`execute`/`verify`**

Replace the idempotency/execute block. `is_needed` now compares via the v3 readers;
`execute` sets the hash with `usermod -p` and honors the delete flag (for the legacy path,
deletions are not computed — it only ensures declared users exist/are correct, matching the
old behaviour, but uses the hash):

```python
    def is_needed(self) -> bool:
        for u in self.users:
            name = u["username"]
            if name == "root":
                if u["hashed_password"] != self._hash("root"):
                    return True
                continue
            if not self._user_exists(name):
                return True
            if u.get("shell", "/bin/bash") != self._shell(name):
                return True
            if set(u.get("groups", [])) - self._groups(name):
                return True
            if u["hashed_password"] != self._hash(name):
                return True
        return False

    def execute(self) -> None:
        target = self._target()
        for u in self.users:
            name = u["username"]
            if name == "root":
                Command.execute("usermod", ["-p", u["hashed_password"], "root"], target=target)
                continue
            shell = u.get("shell", "/bin/bash")
            groups = u.get("groups", [])
            if self._user_exists(name):
                Command.execute("usermod", ["-s", shell, name], target=target)
                if groups:
                    Command.execute("usermod", ["-G", ",".join(groups), name], target=target)
            else:
                argv = ["-m", "-s", shell]
                if groups:
                    argv += ["-G", ",".join(groups)]
                argv.append(name)
                Command.execute("useradd", argv, target=target)
            Command.execute("usermod", ["-p", u["hashed_password"], name], target=target)

    def verify(self) -> bool:
        for u in self.users:
            if u["username"] == "root":
                continue
            if not self._user_exists(u["username"]):
                return False
        return True
```

Delete the now-unused `_set_password` helper and the `import subprocess` if no longer
referenced.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_users_action.py -v`
Expected: PASS. If an older legacy test still passes `password=`, update it to
`hashed_password="$6$..."`.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/users_action.py tests/lib/actions/test_users_action.py
git commit -m "refactor(users): legacy path uses hashed_password + v3 readers"
```

---

## Task 7: Sample migration + full suite + gate

**Files:**
- Modify: `config/install-megamix.json`
- Test: full suite

- [ ] **Step 1: Generate real hashes and migrate the sample**

Generate two SHA-512 hashes:
```bash
/tmp/dasik-venv/bin/python -c "import crypt; print(crypt.crypt('alicepw', crypt.mksalt(crypt.METHOD_SHA512)))"
/tmp/dasik-venv/bin/python -c "import crypt; print(crypt.crypt('bobpw', crypt.mksalt(crypt.METHOD_SHA512)))"
/tmp/dasik-venv/bin/python -c "import crypt; print(crypt.crypt('svcpw', crypt.mksalt(crypt.METHOD_SHA512)))"
```
(If `crypt` is unavailable on Python 3.13+, use `openssl passwd -6 alicepw`.)

In `config/install-megamix.json`, replace each user's `"password": "..."` with
`"hashed_password": "<generated hash>"`. Example shape:
```jsonc
{ "username": "alice", "hashed_password": "$6$....", "shell": "/bin/zsh", "groups": ["wheel", "libvirt", "docker"] }
```
Optionally add a root-level `"remove_home_on_delete": false` near the other toggles.

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
git commit -m "docs(config): migrate megamix users to hashed_password"
```

---

## Self-Review notes

- **Spec coverage:** Task 1 = model (hash + root flag); Task 2 = constructor/actual/readers + `__root__`; Task 3 = plan/managed_keys (CREATE/DELETE/MODIFY/root); Task 4 = apply; Task 5 = import_state; Task 6 = legacy consistency; Task 7 = sample + gate. All spec sections covered.
- **Type consistency:** domain `"users"` (`_USERS_DOMAIN`), ops `Op.CREATE`/`Op.DELETE`/`Op.MODIFY`, helpers `_shell`/`_groups`/`_hash`/`_declared_non_root`/`_by_name`/`_target`, field `hashed_password`, flag `remove_home_on_delete` — consistent across tasks.
- **Reconciler integration:** registered `__root__`; `build_plan`/`sync` pass the full config dict; constructor reads `config["users"]`. `is_v3()` flips True once `plan` is overridden (Task 3). `_domain_for` sees one key (`users`).
- **Known sharp edge:** `crypt` module is removed in Python 3.13. Task 7 Step 1 notes the `openssl passwd -6` fallback for hash generation; tests use literal `$6$...` strings so they don't depend on `crypt`.

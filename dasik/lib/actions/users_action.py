"""Action: create/modify/delete users declaratively.

v3 domain "users": CREATE/DELETE by username (set-math) + MODIFY for
shell/groups/hashed_password drift. Passwords are stored hashed and compared
against /etc/shadow. actual() is scoped to uid>=1000; root is special-cased.

Registered with config_key="__root__" so it can read the root-level
``remove_home_on_delete`` flag alongside the ``users`` list.
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

    @property
    def name(self) -> str:
        return "User Creation"

    @property
    def is_optional(self) -> bool:
        return True

    # ------------------------------------------------------------------ #
    #  target-aware filesystem readers
    # ------------------------------------------------------------------ #

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
        names: set = set()
        try:
            with open(self._passwd_path(), "r") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) >= 3 and parts[2].isdigit() and int(parts[2]) >= 1000:
                        names.add(parts[0])
        except FileNotFoundError:
            pass
        return names

    def _user_exists(self, username: str) -> bool:
        try:
            with open(self._passwd_path(), "r") as f:
                for line in f:
                    if line.startswith(f"{username}:"):
                        return True
        except FileNotFoundError:
            pass
        return False

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

    # ------------------------------------------------------------------ #
    #  v3 contract
    # ------------------------------------------------------------------ #

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
        for name in sorted(set(self._declared_non_root()) & actual):
            reason = self._modify_reason(name)
            if reason:
                changes.append(Change(self._USERS_DOMAIN, Op.MODIFY, name, reason=reason))
        if "root" in self._by_name:
            if self._by_name["root"]["hashed_password"] != self._hash("root"):
                changes.append(Change(self._USERS_DOMAIN, Op.MODIFY, "root", reason="password"))
        return changes

    def managed_keys(self) -> dict:
        return {self._USERS_DOMAIN: self._declared_non_root()}

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

    # ------------------------------------------------------------------ #
    #  legacy is_needed / execute / verify (old ActionExecutor path)
    # ------------------------------------------------------------------ #

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

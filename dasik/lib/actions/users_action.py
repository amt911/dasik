"""Action: create/modify/delete users declaratively.

v3 domain "users": CREATE/DELETE by username (set-math) + MODIFY for
shell/groups/hashed_password drift. Passwords are stored hashed and compared
against /etc/shadow. actual() is scoped to uid>=1000; root is special-cased.

Registered with config_key="__root__" so it can read the root-level
``remove_home_on_delete`` flag alongside the ``users`` list.
"""
import re
from typing import Any, Dict, List
from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command
from ..exceptions.exceptions import CommandExecutionError, ConfigValidationError
from ..state.change import Change, Op

# Linux useradd NAME_REGEX: lowercase/underscore start, then [a-z0-9_-], optional
# trailing $. The name reaches `useradd <name>` argv (a leading '-' is a flag) and
# /etc/passwd/-shadow line parsing (':' is the field separator), so validate it.
_USERNAME_RE = re.compile(r"[a-z_][a-z0-9_-]*\$?")


def _validate_username(name: Any) -> None:
    if not isinstance(name, str) or not _USERNAME_RE.fullmatch(name):
        raise ConfigValidationError(
            f"Invalid username {name!r}: must match [a-z_][a-z0-9_-]*$? "
            f"(no leading digit/'-', no ':' or whitespace)."
        )


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
        for u in self.users:
            _validate_username(u.get("username") if isinstance(u, dict) else None)
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

    # Regular login users are 1000 <= uid < NOBODY; `nobody` (65534) and other
    # high pseudo-accounts are not real users.
    _UID_MIN = 1000
    _NOBODY_UID = 65534

    def actual(self) -> set:
        """Regular login usernames on the target (1000 <= uid < 65534)."""
        if self._target() is None:
            return set()
        names: set = set()
        try:
            with open(self._passwd_path(), "r") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) >= 3 and parts[2].isdigit():
                        uid = int(parts[2])
                        if self._UID_MIN <= uid < self._NOBODY_UID:
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
        except (FileNotFoundError, IndexError, PermissionError):
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
        # Capture reality: keep all declared users (intent; refresh attrs for
        # present ones) + every real user not declared. Independent of M.
        actual = self.actual()

        result = []
        declared_names = set()
        for u in self.users:
            name = u["username"]
            declared_names.add(name)
            if name in actual and name != "root":
                result.append(self._capture(name))             # refresh from reality
            else:
                result.append(u)                               # intent / root kept as-is

        drift = sorted(actual - declared_names)                # present, not declared (no M)
        for name in drift:
            captured = self._capture(name)
            # A user we cannot read a hash for (e.g. /etc/shadow unreadable)
            # cannot be represented portably — skip rather than emit an invalid
            # entry with an empty hashed_password.
            if captured["hashed_password"]:
                result.append(captured)
        return {self._USERS_DOMAIN: result}

    def apply(self, changes) -> None:
        target = self._target()
        if target is None:
            return
        creates = [c.item for c in changes if c.op is Op.CREATE]
        modifies = [c.item for c in changes if c.op is Op.MODIFY]
        deletes = [c.item for c in changes if c.op is Op.DELETE]

        # check=True on every mutation: a failed useradd/usermod/userdel must
        # abort loudly, never masquerade as success. In particular a failed
        # `useradd` must stop before the follow-up `usermod -p` sets a password
        # on a user that was never created.
        # …but a failure on ONE user must not hide the next: aborting on the
        # first one meant discovering a single broken user per apply, and an
        # apply is a whole install. Each user's own sequence stays atomic (a
        # failed useradd still skips its usermod); the failures are collected
        # and raised together at the end.
        failures: List[str] = []

        def attempt(user: str, run) -> None:
            try:
                run()
            except CommandExecutionError as exc:
                failures.append(f"{user}: {exc}")

        for name in creates:
            u = self._by_name[name]
            argv = ["-m", "-s", u.get("shell", "/bin/bash")]
            groups = u.get("groups", [])
            if groups:
                argv += ["-G", ",".join(groups)]
            argv.append(name)

            def create(u=u, argv=argv, name=name):
                Command.execute("useradd", argv, target=target, check=True)
                Command.execute("usermod", ["-p", u["hashed_password"], name],
                                target=target, check=True)
            attempt(name, create)

        for name in modifies:
            u = self._by_name[name]

            def modify(u=u, name=name):
                if name != "root":
                    Command.execute("usermod", ["-s", u.get("shell", "/bin/bash"), name], target=target, check=True)
                    Command.execute("usermod", ["-G", ",".join(u.get("groups", [])), name], target=target, check=True)
                Command.execute("usermod", ["-p", u["hashed_password"], name], target=target, check=True)
            attempt(name, modify)

        for name in deletes:
            argv = ["-r", name] if self.remove_home_on_delete else [name]

            def delete(argv=argv):
                Command.execute("userdel", argv, target=target, check=True)
            attempt(name, delete)

        if failures:
            raise CommandExecutionError(
                f"user setup failed for {len(failures)} account(s):\n"
                + "\n".join(f"  - {f}" for f in failures)
            )

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

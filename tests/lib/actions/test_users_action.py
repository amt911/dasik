from unittest.mock import mock_open, patch
from unittest.mock import patch as _patch

from dasik.lib.actions.users_action import UsersAction
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Change, Op


_PASSWD_UIDS = (
    "root:x:0:0::/root:/bin/bash\n"
    "bin:x:1:1::/:/usr/bin/nologin\n"
    "alice:x:1000:1000::/home/alice:/usr/bin/zsh\n"
    "bob:x:1001:1001::/home/bob:/bin/bash\n"
)
_GROUP = "wheel:x:998:alice\naudio:x:995:alice\nusers:x:100:\n"
_SHADOW = (
    "root:$6$r$roothash:::::::\n"
    "alice:$6$a$alicehash:::::::\n"
)


def _ctx(root="/"):
    return ActionContext(target=Target(root=root))


def _open_tree(passwd=_PASSWD_UIDS, group=_GROUP, shadow=_SHADOW):
    def opener(path, *a, **k):
        p = str(path)
        data = passwd if "passwd" in p else group if "group" in p else shadow
        return mock_open(read_data=data)()
    return patch("builtins.open", side_effect=opener)


# ---------------------------------------------------------------------- #
#  Task 2: constructor, actual(), readers                                #
# ---------------------------------------------------------------------- #


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
        assert a._groups("alice") == {"wheel", "audio"}
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


def test_name_and_optional():
    a = UsersAction([])
    assert a.name == "User Creation"
    assert a.is_optional is True


# ---------------------------------------------------------------------- #
#  Legacy is_needed / verify (updated to hashed_password + readers)      #
# ---------------------------------------------------------------------- #


def test_legacy_needed_when_user_absent():
    a = UsersAction([{"username": "carol", "hashed_password": "$6$c$h"}], _ctx("/"))
    with _open_tree():  # carol absent
        assert a.is_needed() is True


def test_legacy_needed_when_shell_differs():
    a = UsersAction(
        [{"username": "alice", "hashed_password": "$6$a$alicehash",
          "shell": "/bin/bash"}], _ctx("/"))
    with _open_tree():  # alice shell is /usr/bin/zsh
        assert a.is_needed() is True


def test_legacy_needed_when_group_missing():
    a = UsersAction(
        [{"username": "alice", "hashed_password": "$6$a$alicehash",
          "shell": "/usr/bin/zsh", "groups": ["docker"]}], _ctx("/"))
    with _open_tree():
        assert a.is_needed() is True


def test_legacy_needed_when_hash_differs():
    a = UsersAction(
        [{"username": "alice", "hashed_password": "$6$NEW$h",
          "shell": "/usr/bin/zsh", "groups": ["wheel"]}], _ctx("/"))
    with _open_tree():
        assert a.is_needed() is True


def test_legacy_not_needed_when_user_matches():
    a = UsersAction(
        [{"username": "alice", "hashed_password": "$6$a$alicehash",
          "shell": "/usr/bin/zsh", "groups": ["wheel"]}], _ctx("/"))
    with _open_tree():
        assert a.is_needed() is False
        assert a.verify() is True


def test_legacy_root_only_checks_hash():
    a = UsersAction([{"username": "root", "hashed_password": "$6$r$roothash"}], _ctx("/"))
    with _open_tree():
        assert a.is_needed() is False
        assert a.verify() is True


# ---------------------------------------------------------------------- #
#  Task 3: plan() + managed_keys()                                        #
# ---------------------------------------------------------------------- #


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


# ---------------------------------------------------------------------- #
#  Task 4: apply()                                                        #
# ---------------------------------------------------------------------- #


def test_apply_creates_user_with_shell_groups_and_hash():
    a = UsersAction(
        [{"username": "alice", "hashed_password": "$6$a$h",
          "shell": "/usr/bin/zsh", "groups": ["wheel", "audio"]}],
        _ctx("/"),
    )
    with patch("dasik.lib.actions.users_action.Command.execute") as run:
        a.apply([Change("users", Op.CREATE, "alice")])
    cmds = [(c.args[0], c.args[1]) for c in run.call_args_list]
    assert ("useradd", ["-m", "-s", "/usr/bin/zsh", "-G", "wheel,audio", "alice"]) in cmds
    assert ("usermod", ["-p", "$6$a$h", "alice"]) in cmds


def test_apply_modify_sets_shell_groups_hash():
    a = UsersAction(
        [{"username": "alice", "hashed_password": "$6$a$h",
          "shell": "/bin/bash", "groups": ["wheel"]}],
        _ctx("/"),
    )
    with patch("dasik.lib.actions.users_action.Command.execute") as run:
        a.apply([Change("users", Op.MODIFY, "alice")])
    cmds = [(c.args[0], c.args[1]) for c in run.call_args_list]
    assert ("usermod", ["-s", "/bin/bash", "alice"]) in cmds
    assert ("usermod", ["-G", "wheel", "alice"]) in cmds
    assert ("usermod", ["-p", "$6$a$h", "alice"]) in cmds


def test_apply_modify_root_only_sets_password():
    a = UsersAction([{"username": "root", "hashed_password": "$6$r$h"}], _ctx("/"))
    with patch("dasik.lib.actions.users_action.Command.execute") as run:
        a.apply([Change("users", Op.MODIFY, "root")])
    cmds = [(c.args[0], c.args[1]) for c in run.call_args_list]
    assert cmds == [("usermod", ["-p", "$6$r$h", "root"])]


def test_apply_delete_keeps_home_by_default():
    a = UsersAction([], _ctx("/"))
    with patch("dasik.lib.actions.users_action.Command.execute") as run:
        a.apply([Change("users", Op.DELETE, "old")])
    assert run.call_args_list[0].args[:2] == ("userdel", ["old"])


def test_apply_delete_removes_home_when_flag_set():
    a = UsersAction({"users": [], "remove_home_on_delete": True}, _ctx("/"))
    with patch("dasik.lib.actions.users_action.Command.execute") as run:
        a.apply([Change("users", Op.DELETE, "old")])
    assert run.call_args_list[0].args[:2] == ("userdel", ["-r", "old"])


def test_apply_create_before_delete():
    a = UsersAction([{"username": "new", "hashed_password": "$6$n$h"}], _ctx("/"))
    changes = [Change("users", Op.DELETE, "old"), Change("users", Op.CREATE, "new")]
    with patch("dasik.lib.actions.users_action.Command.execute") as run:
        a.apply(changes)
    ops = [c.args[0] for c in run.call_args_list]
    assert ops.index("useradd") < ops.index("userdel")


def test_apply_noop_without_target():
    a = UsersAction([{"username": "x", "hashed_password": "$6$x$h"}], None)
    with patch("dasik.lib.actions.users_action.Command.execute") as run:
        a.apply([Change("users", Op.CREATE, "x")])
    run.assert_not_called()


# ---------------------------------------------------------------------- #
#  Task 5: import_state() (sync)                                          #
# ---------------------------------------------------------------------- #


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

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

from unittest.mock import mock_open, patch

from dasik.lib.actions.users_action import UsersAction


_PASSWD = "root:x:0:0::/root:/bin/bash\nalice:x:1000:1000::/home/alice:/usr/bin/zsh\n"
_GROUP = "wheel:x:998:alice\naudio:x:995:alice\nusers:x:100:\n"


def _patch_files(passwd=_PASSWD, group=_GROUP):
    def opener(path, *a, **k):
        data = passwd if "passwd" in str(path) else group
        return mock_open(read_data=data)()
    return patch("builtins.open", side_effect=opener)


def test_user_exists_reads_passwd():
    a = UsersAction([])
    with _patch_files():
        assert a._user_exists("alice") is True
        assert a._user_exists("bob") is False


def test_user_exists_false_when_passwd_missing():
    a = UsersAction([])
    with patch("builtins.open", side_effect=FileNotFoundError):
        assert a._user_exists("alice") is False


def test_get_user_shell():
    a = UsersAction([])
    with _patch_files():
        assert a._get_user_shell("alice") == "/usr/bin/zsh"
        assert a._get_user_shell("ghost") == ""


def test_get_user_groups():
    a = UsersAction([])
    with _patch_files():
        assert a._get_user_groups("alice") == {"wheel", "audio"}


def test_needed_when_user_absent():
    a = UsersAction([{"username": "bob", "password": "x"}])
    with _patch_files():
        assert a.is_needed() is True


def test_needed_when_shell_differs():
    a = UsersAction([{"username": "alice", "password": "x", "shell": "/bin/bash"}])
    with _patch_files():
        assert a.is_needed() is True


def test_needed_when_group_missing():
    a = UsersAction([{"username": "alice", "password": "x",
                      "shell": "/usr/bin/zsh", "groups": ["docker"]}])
    with _patch_files():
        assert a.is_needed() is True


def test_not_needed_when_user_matches():
    a = UsersAction([{"username": "alice", "password": "x",
                      "shell": "/usr/bin/zsh", "groups": ["wheel"]}])
    with _patch_files():
        assert a.is_needed() is False
        assert a.verify() is True


def test_root_user_is_skipped_in_idempotency():
    a = UsersAction([{"username": "root", "password": "x"}])
    with _patch_files():
        assert a.is_needed() is False
        assert a.verify() is True


def test_name_and_optional():
    a = UsersAction([])
    assert a.name == "User Creation"
    assert a.is_optional is True

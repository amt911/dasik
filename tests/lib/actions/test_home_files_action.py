"""HomeFilesAction — the files dasik owns inside a user's $HOME.

Everything /etc gets from DropFilesAction, plus the two things a home file has
and an /etc file does not: the path depends on where the *machine* says the home
is, and a file root writes into $HOME is useless to the desktop unless it is
chowned to the user.
"""
import os
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.home_files_action import HomeFilesAction
from dasik.lib.exceptions.exceptions import CommandExecutionError
from dasik.lib.target.target import Target


_UID, _GID = os.getuid(), os.getgid()
_ENTRY = "[Desktop Entry]\nType=Application\n"


def _passwd(root, entries=(("andres", 1000, 1000, "/home/andres"),)):
    (root / "etc").mkdir(parents=True, exist_ok=True)
    (root / "etc/passwd").write_text(
        "root:x:0:0::/root:/bin/bash\n"
        + "".join(f"{n}:x:{u}:{g}::{h}:/bin/bash\n" for n, u, g, h in entries))


def _action(root, files, managed=None):
    return HomeFilesAction({"home_files": list(files)},
                           ActionContext(target=Target(root=str(root))))


_FILE = {"user": "andres", "path": ".config/autostart/apparmor-notify.desktop",
         "content": _ENTRY}


def _plan(root, files, managed=()):
    return [(c.op.name, c.item, c.reason) for c in
            _action(root, files).plan(managed=list(managed))]


# --- where the file goes --------------------------------------------------- #

def test_the_path_comes_from_the_machines_passwd(tmp_path):
    _passwd(tmp_path, [("andres", 1000, 1000, "/var/lib/andres")])

    assert _plan(tmp_path, [_FILE])[0][1] == \
        "/var/lib/andres/.config/autostart/apparmor-notify.desktop"


def test_an_unknown_user_falls_back_to_the_conventional_home(tmp_path):
    """The whole plan is computed before anything is applied, so on a fresh
    install the user does not exist yet. /home/<user> is what useradd will
    choose; the plan must still be able to name the file."""
    _passwd(tmp_path, [])

    assert _plan(tmp_path, [_FILE])[0][1] == \
        "/home/andres/.config/autostart/apparmor-notify.desktop"


# --- plan ------------------------------------------------------------------ #

def _existing(tmp_path, content=_ENTRY, uid=_UID, gid=_GID, mode=0o644):
    _passwd(tmp_path, [("andres", uid, gid, "/home/andres")])
    p = tmp_path / "home/andres/.config/autostart"
    p.mkdir(parents=True, exist_ok=True)
    f = p / "apparmor-notify.desktop"
    f.write_text(content)
    f.chmod(mode)
    return f


def test_a_missing_home_file_is_planned(tmp_path):
    _passwd(tmp_path)
    assert _plan(tmp_path, [_FILE]) == [
        ("CREATE", "/home/andres/.config/autostart/apparmor-notify.desktop", "")]


def test_a_home_file_already_in_place_plans_nothing(tmp_path):
    _existing(tmp_path)
    assert _plan(tmp_path, [_FILE]) == []


def test_content_drift_is_planned(tmp_path):
    _existing(tmp_path, content="something else\n")
    assert [c[0] for c in _plan(tmp_path, [_FILE])] == ["MODIFY"]


def test_a_file_root_still_owns_is_planned(tmp_path):
    """The failure this primitive exists to avoid: the content is right, the
    file is root's, and the desktop that has to rewrite it cannot."""
    _existing(tmp_path, uid=_UID + 1, gid=_GID + 1)

    changes = _plan(tmp_path, [_FILE])

    assert [c[0] for c in changes] == ["MODIFY"]
    assert "owner" in changes[0][2]


def test_mode_drift_is_planned(tmp_path):
    _existing(tmp_path, mode=0o600)
    assert [c[0] for c in _plan(tmp_path, [dict(_FILE, mode="0644")])] == ["MODIFY"]


def test_the_declared_mode_already_set_plans_nothing(tmp_path):
    _existing(tmp_path, mode=0o600)
    assert _plan(tmp_path, [dict(_FILE, mode="0600")]) == []


def test_dropping_the_declaration_deletes_the_file_dasik_owns(tmp_path):
    _existing(tmp_path)
    owned = "/home/andres/.config/autostart/apparmor-notify.desktop"

    assert _plan(tmp_path, [], managed=[owned]) == [
        ("DELETE", owned, "no longer declared")]


def test_an_unowned_home_file_is_left_alone(tmp_path):
    _existing(tmp_path)
    assert _plan(tmp_path, []) == []


# --- apply ----------------------------------------------------------------- #

def test_apply_writes_the_file_and_gives_it_to_the_user(tmp_path):
    _passwd(tmp_path, [("andres", 4242, 4243, "/home/andres")])
    action = _action(tmp_path, [_FILE])
    with patch("dasik.lib.actions.home_files_action.os.chown") as chown:
        action.apply(action.plan(managed=[]))

    written = tmp_path / "home/andres/.config/autostart/apparmor-notify.desktop"
    assert written.read_text() == _ENTRY
    assert (4242, 4243) in {tuple(c.args[1:]) for c in chown.call_args_list}


def test_apply_chowns_every_directory_it_had_to_create(tmp_path):
    """`.config` and `.config/autostart` created as root would leave the user
    unable to add anything beside the file."""
    _passwd(tmp_path, [("andres", 4242, 4243, "/home/andres")])
    (tmp_path / "home/andres").mkdir(parents=True)
    action = _action(tmp_path, [_FILE])
    with patch("dasik.lib.actions.home_files_action.os.chown") as chown:
        action.apply(action.plan(managed=[]))

    chowned = {c.args[0] for c in chown.call_args_list}
    assert str(tmp_path / "home/andres/.config") in chowned
    assert str(tmp_path / "home/andres/.config/autostart") in chowned
    assert str(tmp_path / "home/andres") not in chowned      # pre-existing


def test_apply_sets_the_declared_mode(tmp_path):
    _passwd(tmp_path)
    action = _action(tmp_path, [dict(_FILE, mode="0600")])
    with patch("dasik.lib.actions.home_files_action.os.chown"):
        action.apply(action.plan(managed=[]))

    written = tmp_path / "home/andres/.config/autostart/apparmor-notify.desktop"
    assert oct(written.stat().st_mode & 0o777) == "0o600"


def test_apply_deletes_what_the_plan_removed(tmp_path):
    f = _existing(tmp_path)
    owned = "/home/andres/.config/autostart/apparmor-notify.desktop"
    action = _action(tmp_path, [])
    with patch("dasik.lib.actions.home_files_action.os.chown"):
        action.apply(action.plan(managed=[owned]))

    assert not f.exists()


def test_apply_refuses_to_write_for_a_user_the_machine_does_not_have(tmp_path):
    """Silently writing to /home/<name> for a user that was never created leaves
    a root-owned directory nobody can use."""
    _passwd(tmp_path, [])
    action = _action(tmp_path, [_FILE])
    with patch("dasik.lib.actions.home_files_action.os.chown"), \
         pytest.raises(CommandExecutionError, match="andres"):
        action.apply(action.plan(managed=[]))


# --- sync ------------------------------------------------------------------ #

def test_sync_reads_a_declared_file_back_from_the_machine(tmp_path):
    _existing(tmp_path, content="edited by hand\n")

    captured = _action(tmp_path, [_FILE]).import_state([])["home_files"]

    assert captured == [{"user": "andres",
                         "path": ".config/autostart/apparmor-notify.desktop",
                         "content": "edited by hand\n"}]


def test_sync_captures_a_file_the_manifest_owns_from_an_empty_seed(tmp_path):
    _existing(tmp_path)
    owned = "/home/andres/.config/autostart/apparmor-notify.desktop"

    captured = _action(tmp_path, []).import_state([owned])["home_files"]

    assert captured == [{"user": "andres",
                         "path": ".config/autostart/apparmor-notify.desktop",
                         "content": _ENTRY}]


def test_sync_does_not_scan_the_home_directory(tmp_path):
    """A $HOME scan would capture gigabytes and every secret in it. Only what
    dasik declared or owns is reported."""
    _existing(tmp_path)
    (tmp_path / "home/andres/.ssh").mkdir(parents=True)
    (tmp_path / "home/andres/.ssh/id_ed25519").write_text("PRIVATE KEY")

    captured = _action(tmp_path, []).import_state([])

    assert captured["home_files"] == []


def test_sync_drops_an_owned_file_that_is_gone(tmp_path):
    _passwd(tmp_path)
    owned = "/home/andres/.config/autostart/apparmor-notify.desktop"

    assert _action(tmp_path, []).import_state([owned])["home_files"] == []


def test_sync_keeps_the_declared_mode(tmp_path):
    _existing(tmp_path, mode=0o600)

    captured = _action(tmp_path, [dict(_FILE, mode="0600")]).import_state([])

    assert captured["home_files"][0]["mode"] == "0600"


# --- manifest -------------------------------------------------------------- #

def test_the_domain_is_the_absolute_path(tmp_path):
    _passwd(tmp_path)
    assert _action(tmp_path, [_FILE]).managed_keys() == {
        "home_files": ["/home/andres/.config/autostart/apparmor-notify.desktop"]}


def test_no_target_means_no_work():
    action = HomeFilesAction({"home_files": [_FILE]}, ActionContext(target=None))
    assert action.plan(managed=[]) == []

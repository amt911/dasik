"""The `config_saver` block: backup policy, its timer, and restoring dotfiles.

config-saver is the user's own tool and it is **not in the AUR** — its PKGBUILD
lives in a plain Git repository — so the block also carries the source that can
build it. The restore side is what closes "dotfiles de $HOME" in #173: an
archive produced on the old machine, unpacked into the new one during the
install.
"""
import hashlib
import json
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.config_saver_action import ConfigSaverAction
from dasik.lib.expand import expand_config, subtract_contributions
from dasik.lib.models.config_saver_model import ConfigSaverModel
from dasik.lib.target.target import Target


_SHA = "a520605367e13ec25db4c3c7e1c4bf46175ba8cd"
_SOURCE = {"url": "https://github.com/amt911/config-saver-aur.git", "ref": _SHA}
_DOC = {"normalize_content": True,
        "directories": [{"source": "$HOME", "files": [".zshrc"]}]}


# --- the model ------------------------------------------------------------- #

def test_an_empty_block_is_valid():
    """Declaring it at all means "install config-saver"."""
    assert ConfigSaverModel().configs == {}


def test_a_config_name_must_be_a_filename():
    with pytest.raises(ValidationError):
        ConfigSaverModel(configs={"../../etc/passwd": _DOC})


def test_the_source_ref_must_be_a_full_sha():
    with pytest.raises(ValidationError):
        ConfigSaverModel(source={"url": _SOURCE["url"], "ref": "a520605"})


def test_a_restore_archive_must_be_an_absolute_path():
    with pytest.raises(ValidationError):
        ConfigSaverModel(restore=[{"user": "andres", "archive": "dotfiles.tar.gz"}])


def test_a_restore_entry_is_kept_verbatim():
    m = ConfigSaverModel(restore=[{"user": "andres",
                                   "archive": "/run/media/usb/dotfiles.tar.gz"}])
    assert m.restore[0].user == "andres"


# --- expansion ------------------------------------------------------------- #

def _config(**over):
    block = {"source": _SOURCE, "configs": {"dotfiles": _DOC},
             "timer_users": ["andres"]}
    block.update(over)
    return {"config_saver": block,
            "users": [{"username": "andres", "hashed_password": "$6$a$b"}]}


def test_the_package_and_its_source_are_derived():
    expanded = expand_config(_config())

    assert "config-saver" in expanded["packages"]
    assert expanded["package_sources"]["config-saver"] == {
        "type": "pkgbuild-git", **_SOURCE}


def test_no_source_is_invented_when_none_is_declared():
    expanded = expand_config(_config(source=None))

    assert "config-saver" in expanded["packages"]
    assert "package_sources" not in expanded


def test_a_declared_source_wins_over_the_derived_one():
    config = _config()
    config["package_sources"] = {"config-saver": {
        "type": "pkgbuild-git", "url": _SOURCE["url"], "ref": "b" * 40}}

    assert expand_config(config)["package_sources"]["config-saver"]["ref"] == "b" * 40


def test_each_config_becomes_a_json_file():
    files = expand_config(_config())["files"]
    entry = [f for f in files
             if f["path"] == "/etc/config-saver/configs/dotfiles.json"]

    assert entry and json.loads(entry[0]["content"]) == _DOC


def test_the_timer_is_enabled_per_user():
    units = expand_config(_config())["systemd"]["enable_units"]
    assert units == ["config-saver@andres.timer"]


def test_the_derived_pieces_are_not_captured_as_hand_written_ones():
    config = _config()
    captured = subtract_contributions(expand_config(config), config)

    assert captured["files"] == []
    assert "config-saver" not in captured["packages"]
    assert captured.get("package_sources", {}) == {}


# --- restore --------------------------------------------------------------- #

def _machine(tmp_path, archive=b"tarball", installed=True, home=True):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/passwd").write_text(
        "root:x:0:0::/root:/bin/bash\n"
        "andres:x:1000:1000::/home/andres:/bin/bash\n")
    if installed:
        (tmp_path / "usr/bin").mkdir(parents=True, exist_ok=True)
        (tmp_path / "usr/bin/config-saver").write_text("")
    if home:
        (tmp_path / "home/andres").mkdir(parents=True, exist_ok=True)
    if archive is not None:
        (tmp_path / "run/media/usb").mkdir(parents=True, exist_ok=True)
        (tmp_path / "run/media/usb/dotfiles.tar.gz").write_bytes(archive)
    return tmp_path


_ARCHIVE = "/run/media/usb/dotfiles.tar.gz"
_RESTORE = {"config_saver": {"restore": [{"user": "andres", "archive": _ARCHIVE}]}}


def _action(root, config=None):
    return ConfigSaverAction(config if config is not None else _RESTORE,
                             ActionContext(target=Target(root=str(root))))


def _mark(root, payload=b"tarball", user="andres"):
    digest = hashlib.sha256(payload).hexdigest()
    d = root / f"home/{user}/.local/state/dasik/config-saver"
    d.mkdir(parents=True, exist_ok=True)
    (d / digest).write_text(_ARCHIVE + "\n")


def test_an_archive_never_restored_is_planned(tmp_path):
    action = _action(_machine(tmp_path))
    assert [(c.op.name, c.item) for c in action.plan(managed=[])] == [
        ("CREATE", f"andres:{_ARCHIVE}")]


def test_an_archive_already_restored_plans_nothing(tmp_path):
    root = _machine(tmp_path)
    _mark(root)
    assert _action(root).plan(managed=[]) == []


def test_a_new_archive_at_the_same_path_is_restored_again(tmp_path):
    """The marker records the archive's CONTENT, so replacing dotfiles.tar.gz
    with a newer capture is a change, not a no-op."""
    root = _machine(tmp_path, archive=b"newer tarball")
    _mark(root, payload=b"tarball")

    assert [c.op.name for c in _action(root).plan(managed=[])] == ["CREATE"]


def test_a_missing_archive_is_still_planned(tmp_path):
    """Silence would be indistinguishable from "already restored"; apply then
    says exactly which path it could not find."""
    root = _machine(tmp_path, archive=None)
    assert [c.op.name for c in _action(root).plan(managed=[])] == ["CREATE"]


def test_apply_runs_config_saver_as_the_user_and_marks_it(tmp_path):
    root = _machine(tmp_path)
    action = _action(root)
    with patch("dasik.lib.actions.config_saver_action.Command.execute",
               MagicMock(return_value=MagicMock(returncode=0))) as run, \
         patch("dasik.lib.actions.config_saver_action.os.chown"):
        action.apply(action.plan(managed=[]))

    argv = run.call_args.args[1]
    assert run.call_args.args[0] == "su"
    assert "andres" in argv and _ARCHIVE in argv
    assert action.plan(managed=[]) == []          # converged


def test_apply_refuses_an_archive_that_is_not_there(tmp_path):
    from dasik.lib.exceptions.exceptions import CommandExecutionError

    root = _machine(tmp_path, archive=None)
    action = _action(root)
    with pytest.raises(CommandExecutionError, match="dotfiles.tar.gz"):
        action.apply(action.plan(managed=[]))


def test_dropping_a_restore_entry_never_deletes_anything(tmp_path):
    """Un-declaring a restore cannot un-restore a home directory. The manifest
    stops owning it and the files stay — said out loud rather than implied."""
    root = _machine(tmp_path)
    _mark(root)
    action = _action(root, {"config_saver": {}})

    assert action.plan(managed=[f"andres:{_ARCHIVE}"]) == []


# --- capture ---------------------------------------------------------------- #

def _captured(root, config=None, enabled=False):
    action = _action(root, config if config is not None else {})
    res = MagicMock(stdout=b"enabled\n" if enabled else b"disabled\n",
                    returncode=0 if enabled else 1)
    with patch("dasik.lib.actions.config_saver_action.Command.execute",
               MagicMock(return_value=res)):
        return action.import_state([]).get("config_saver")


def test_sync_invents_nothing_when_config_saver_is_not_installed(tmp_path):
    assert _captured(_machine(tmp_path, installed=False)) is None


def test_sync_captures_the_configs_it_finds(tmp_path):
    root = _machine(tmp_path)
    (root / "etc/config-saver/configs").mkdir(parents=True)
    (root / "etc/config-saver/configs/dotfiles.json").write_text(json.dumps(_DOC))

    with patch("dasik.lib.actions.config_saver_action.ConfigSaverAction._pkg_owned",
               return_value=False):
        captured = _captured(root)

    assert captured["configs"] == {"dotfiles": _DOC}


def test_sync_skips_the_configs_the_package_ships(tmp_path):
    root = _machine(tmp_path)
    (root / "etc/config-saver/configs").mkdir(parents=True)
    (root / "etc/config-saver/configs/default-config.json").write_text("{}")

    with patch("dasik.lib.actions.config_saver_action.ConfigSaverAction._pkg_owned",
               return_value=True):
        assert _captured(root)["configs"] == {}


def test_sync_captures_the_enabled_timers(tmp_path):
    captured = _captured(_machine(tmp_path), enabled=True)
    assert captured["timer_users"] == ["andres"]


def test_sync_keeps_the_restore_declaration_as_intent(tmp_path):
    """A marker names a content hash, not a path — the archive cannot be
    reconstructed from the machine, so the declaration is what survives."""
    root = _machine(tmp_path)
    captured = _captured(root, _RESTORE)

    assert captured["restore"] == [{"user": "andres", "archive": _ARCHIVE}]

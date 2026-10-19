"""`config_saver.restic_stores`: config-saver's deduplicated restic store, declared.

config-saver 3.5.0 can keep its backups as snapshots in one encrypted restic
repository instead of a `.tar.gz.age` per day. Creating that store is a
one-time, per-user step (`config-saver --restic-init`); declaring it makes a
fresh install and the machines already running converge on it.

Three rules shape the domain:
- the store holds the backups, so un-declaring it **never deletes it**;
- the public key is derived from the identity (`age-keygen -y`), so the two can
  never disagree — config-saver refuses a mismatch anyway;
- a missing prerequisite (restic, age, the key, a config-saver older than
  3.5.0) is a **warning**, not a failed apply: restic is an optional package,
  and on a fresh install the key arrives after the first boot. The plan keeps
  naming the store until it exists.
"""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.config_saver_action import ConfigSaverAction, ConfigSaverResticAction
from dasik.lib.exceptions.exceptions import CommandExecutionError
from dasik.lib.login_shell import MARKED
from dasik.lib.models.config_saver_model import ConfigSaverModel
from dasik.lib.state.change import Op
from dasik.lib.target.target import Target

_PUB = "age1qyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqs3290gq"
_EXEC = "dasik.lib.actions.config_saver_action.Command.execute"


# --- the model ------------------------------------------------------------- #

def test_the_identity_defaults_to_the_usual_age_key():
    m = ConfigSaverModel(restic_stores=[{"user": "andres"}])
    assert m.restic_stores[0].identity == "~/.config/age/key.txt"


@pytest.mark.parametrize("identity", [
    "key.txt",                      # relative: relative to what?
    "~/../root/key.txt",            # escapes the home
    "/home/andres/o'brien/key.txt", # restic's password command cannot carry it
    '/k"ey.txt',
    "/k\\ey.txt",
])
def test_an_identity_must_be_a_plain_absolute_or_home_path(identity):
    with pytest.raises(ValidationError):
        ConfigSaverModel(restic_stores=[{"user": "andres", "identity": identity}])


def test_a_store_user_must_be_a_valid_username():
    with pytest.raises(ValidationError):
        ConfigSaverModel(restic_stores=[{"user": "../root"}])


def test_one_store_per_user():
    with pytest.raises(ValidationError):
        ConfigSaverModel(restic_stores=[{"user": "andres"}, {"user": "andres"}])


# --- a machine --------------------------------------------------------------- #

def _machine(root, *, restic=True, age=True, key=True, store=None):
    (root / "etc").mkdir(parents=True, exist_ok=True)
    (root / "etc/passwd").write_text(
        "root:x:0:0::/root:/bin/bash\n"
        "andres:x:1000:1000::/home/andres:/bin/bash\n")
    (root / "usr/bin").mkdir(parents=True, exist_ok=True)
    (root / "usr/bin/config-saver").write_text("")
    for name, present in (("restic", restic), ("age", age)):
        if present:
            (root / f"usr/bin/{name}").write_text("")
    home = root / "home/andres"
    home.mkdir(parents=True, exist_ok=True)
    if key:
        (home / ".config/age").mkdir(parents=True, exist_ok=True)
        (home / ".config/age/key.txt").write_text("AGE-SECRET-KEY-1...\n")
    saves = home / ".config/config-saver"
    if store in ("complete", "repo-only", "password-only"):
        saves.mkdir(parents=True, exist_ok=True)
        if store != "password-only":
            (saves / "restic").mkdir()
        if store != "repo-only":
            (saves / "restic-password.age").write_text("x")
    return root


_DECLARED = {"config_saver": {"restic_stores": [{"user": "andres"}]}}


def _action(root, config=None):
    return ConfigSaverResticAction(config if config is not None else _DECLARED,
                                   ActionContext(target=Target(root=str(root))))


def _block_owner(root, config):
    return ConfigSaverAction(config, ActionContext(target=Target(root=str(root))))


def _restic_changes(changes):
    return [c for c in changes if c.domain == "config_saver_restic"]


# --- plan ------------------------------------------------------------------ #

def test_a_declared_store_that_does_not_exist_is_planned(tmp_path):
    changes = _restic_changes(_action(_machine(tmp_path)).plan(managed=[]))
    assert [(c.op, c.item) for c in changes] == [(Op.CREATE, "andres")]


def test_an_existing_store_plans_nothing(tmp_path):
    action = _action(_machine(tmp_path, store="complete"))
    assert _restic_changes(action.plan(managed=[])) == []
    assert action.actual() == {"andres"}


@pytest.mark.parametrize("half", ["repo-only", "password-only"])
def test_a_half_made_store_is_not_a_store(tmp_path, half):
    action = _action(_machine(tmp_path, store=half))
    assert [c.item for c in _restic_changes(action.plan(managed=[]))] == ["andres"]


def test_undeclaring_a_store_never_plans_its_removal(tmp_path):
    """The store holds the backups. Dropping the block stops managing it; it
    does not delete months of history."""
    root = _machine(tmp_path, store="complete")
    action = _action(root, {"config_saver": {}})
    assert _restic_changes(action.plan(managed=["andres"])) == []


def test_the_domain_is_in_managed_keys(tmp_path):
    keys = _action(_machine(tmp_path)).managed_keys()
    assert keys["config_saver_restic"] == ["andres"]


# --- apply ------------------------------------------------------------------- #

def _fake(version=b"config-saver 3.5.0\n", keygen=_PUB.encode(), keygen_rc=0,
          init_rc=0):
    calls = []

    def execute(cmd, args, **kwargs):
        calls.append([cmd, *args])
        script = args[3] if cmd == "su" and len(args) > 3 else ""
        if "--version" in args:
            return SimpleNamespace(returncode=0, stdout=b"\x1edasik\x1e" + version, stderr=b"")
        if "age-keygen" in script:
            return SimpleNamespace(returncode=keygen_rc,
                                   stdout=b"hi from profile\n\x1edasik\x1e" + keygen + b"\n",
                                   stderr=b"no such key")
        if "--restic-init" in args:
            if init_rc and kwargs.get("check"):
                raise CommandExecutionError("init failed")
            return SimpleNamespace(returncode=init_rc, stdout=b"", stderr=b"")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    return calls, execute


def _apply(root, execute, config=None):
    action = _action(root, config)
    with patch(_EXEC, side_effect=execute), \
         patch("dasik.lib.actions.config_saver_action.run_logger") as log:
        action.apply(action.plan(managed=[]))
    return log.get.return_value.warning.call_args_list


def test_apply_initialises_the_store_as_the_user(tmp_path):
    calls, execute = _fake()
    warnings = _apply(_machine(tmp_path), execute)

    init = [c for c in calls if "--restic-init" in c]
    assert init == [["su", "-", "andres", "-c", 'config-saver "$@"', "--",
                     "config-saver", "--restic-init", "--encrypt-to", _PUB,
                     "--identity", "/home/andres/.config/age/key.txt"]]
    keygen = [c for c in calls if c[0] == "su" and "age-keygen" in c[4]]
    assert keygen == [["su", "-", "andres", "-c", MARKED('age-keygen -y "$1"'), "--",
                       "age-keygen", "/home/andres/.config/age/key.txt"]]
    assert warnings == []


def test_an_absolute_identity_is_used_as_is(tmp_path):
    root = _machine(tmp_path)
    (root / "srv").mkdir()
    (root / "srv/key.txt").write_text("k")
    calls, execute = _fake()
    _apply(root, execute, {"config_saver": {"restic_stores": [
        {"user": "andres", "identity": "/srv/key.txt"}]}})

    assert [c for c in calls if "--restic-init" in c][0][-1] == "/srv/key.txt"


@pytest.mark.parametrize("missing, says", [
    ({"restic": False}, "restic is not installed"),
    ({"age": False}, "age is not installed"),
    ({"key": False}, "/home/andres/.config/age/key.txt"),
])
def test_a_missing_prerequisite_warns_and_initialises_nothing(tmp_path, missing, says):
    calls, execute = _fake()
    warnings = _apply(_machine(tmp_path, **missing), execute)

    assert not [c for c in calls if "--restic-init" in c]
    assert len(warnings) == 1 and says in warnings[0].args[0]


@pytest.mark.parametrize("version", [b"config-saver 3.4.0\n", b"config-saver 2.9.12\n",
                                     b"garbage\n"])
def test_a_config_saver_older_than_3_5_warns_and_initialises_nothing(tmp_path, version):
    calls, execute = _fake(version=version)
    warnings = _apply(_machine(tmp_path), execute)

    assert not [c for c in calls if "--restic-init" in c]
    assert len(warnings) == 1 and "3.5.0" in warnings[0].args[0]


def test_newer_config_saver_versions_are_accepted(tmp_path):
    calls, execute = _fake(version=b"config-saver 3.10.1\n")
    _apply(_machine(tmp_path), execute)
    assert [c for c in calls if "--restic-init" in c]


def test_a_half_made_store_is_an_error_not_a_guess(tmp_path):
    """Exactly one of repo / password file: the history may be unreadable.
    dasik will not re-init over it, and will not hide it either."""
    _calls, execute = _fake()
    with pytest.raises(CommandExecutionError, match="restic-password.age"):
        _apply(_machine(tmp_path, store="repo-only"), execute)


def test_an_unreadable_key_is_an_error(tmp_path):
    _calls, execute = _fake(keygen=b"", keygen_rc=1)
    with pytest.raises(CommandExecutionError, match="age-keygen"):
        _apply(_machine(tmp_path), execute)


def test_a_declared_user_missing_on_the_target_is_an_error(tmp_path):
    _calls, execute = _fake()
    with pytest.raises(CommandExecutionError, match="no user 'ghost'"):
        _apply(_machine(tmp_path), execute,
               {"config_saver": {"restic_stores": [{"user": "ghost"}]}})


# --- sync ---------------------------------------------------------------------- #

def _captured(root, config=None):
    with patch(_EXEC, return_value=SimpleNamespace(returncode=1, stdout=b"", stderr=b"")):
        return _block_owner(root, config if config is not None else {}).import_state()


def test_sync_captures_an_existing_store_with_its_identity(tmp_path):
    root = _machine(tmp_path, store="complete")
    (root / "home/andres/.config/config-saver/restic-store.json").write_text(
        json.dumps({"identity": "/home/andres/keys/age.txt"}))

    block = _captured(root)["config_saver"]

    assert block["restic_stores"] == [{"user": "andres", "identity": "~/keys/age.txt"}]


def test_sync_keeps_an_identity_outside_the_home_absolute(tmp_path):
    root = _machine(tmp_path, store="complete")
    (root / "home/andres/.config/config-saver/restic-store.json").write_text(
        json.dumps({"identity": "/srv/key.txt"}))

    assert _captured(root)["config_saver"]["restic_stores"] == [
        {"user": "andres", "identity": "/srv/key.txt"}]


def test_sync_without_a_store_captures_no_restic_stores(tmp_path):
    assert "restic_stores" not in _captured(_machine(tmp_path))["config_saver"]


def test_sync_keeps_a_declared_store_that_does_not_exist_yet(tmp_path):
    """The key arrives after the first boot: until then the store is intent,
    like a restore, and losing it would make the next apply create nothing."""
    block = _captured(_machine(tmp_path, key=False), _DECLARED)["config_saver"]
    assert block["restic_stores"] == [{"user": "andres",
                                       "identity": "~/.config/age/key.txt"}]


def test_sync_then_plan_is_silent(tmp_path):
    root = _machine(tmp_path, store="complete")
    captured = _captured(root)
    action = _action(root, captured)
    assert _restic_changes(action.plan(managed=[])) == []


def test_the_store_action_captures_nothing_itself(tmp_path):
    """One writer per top-level key: ConfigSaverAction captures the block."""
    assert _action(_machine(tmp_path, store="complete")).import_state() == {}


def test_the_block_action_plans_no_store(tmp_path):
    owner = _block_owner(_machine(tmp_path), _DECLARED)
    assert owner.plan(managed=[]) == []
    assert "config_saver_restic" not in owner.managed_keys()


def test_config_saver_itself_missing_warns_and_runs_nothing(tmp_path):
    """Its build can fail under `build_failure: warn-and-continue`: no binary
    is a warning like the others, not a crash on `config-saver --version`."""
    root = _machine(tmp_path)
    (root / "usr/bin/config-saver").unlink()
    calls, execute = _fake()
    warnings = _apply(root, execute)

    assert calls == []
    assert len(warnings) == 1 and "config-saver is not installed" in warnings[0].args[0]


def test_sync_ignores_system_accounts_with_a_store(tmp_path):
    root = _machine(tmp_path, store="complete")
    (root / "etc/passwd").write_text(
        "root:x:0:0::/root:/bin/bash\n"
        "andres:x:999:999::/home/andres:/bin/bash\n")
    assert "restic_stores" not in _captured(root)["config_saver"]


@pytest.mark.parametrize("settings", ["{not json", "[]", '{"identity": "relative/key"}'])
def test_a_store_with_unreadable_settings_falls_back_to_the_default_identity(tmp_path, settings):
    root = _machine(tmp_path, store="complete")
    (root / "home/andres/.config/config-saver/restic-store.json").write_text(settings)
    assert _captured(root)["config_saver"]["restic_stores"] == [
        {"user": "andres", "identity": "~/.config/age/key.txt"}]


def test_a_symlinked_store_settings_file_is_not_followed(tmp_path):
    """sync reads the user's home as root: a planted link must not make it
    read (and copy into the config) a file the user cannot."""
    root = _machine(tmp_path, store="complete")
    (root / "secret.json").write_text('{"identity": "/root/leaked"}')
    (root / "home/andres/.config/config-saver/restic-store.json").symlink_to(root / "secret.json")
    assert _captured(root)["config_saver"]["restic_stores"] == [
        {"user": "andres", "identity": "~/.config/age/key.txt"}]

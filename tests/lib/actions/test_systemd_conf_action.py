"""The pacman-owned /etc/systemd/*.conf files (oomd, system, user).

`sync` could not see these at all: they are pacman **backup files**, so
DropFilesAction's discovery skips them (it only captures files no package
owns), and /etc/systemd is not one of its sections either. A machine with
`DefaultMemoryPressureDurationSec=20s` in /etc/systemd/oomd.conf captured a
config that silently dropped it.

dasik writes its own values as a drop-in (`<conf>.d/10-dasik.conf`) rather than
editing the package file — that is the supported systemd mechanism and it keeps
pacman's .pacnew handling out of the picture — but it READS the effective
configuration: package file first, then drop-ins in lexicographic order.
"""
import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.systemd_conf_action import (
    OomdAction, SystemdSystemConfAction, SystemdUserConfAction,
)
from dasik.lib.state.change import Op
from dasik.lib.target.target import Target


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


# The stock Arch file: every setting present but commented out.
_STOCK_OOMD = """# This file is part of systemd.
[OOM]
#SwapUsedLimit=90%
#DefaultMemoryPressureLimit=60%
#DefaultMemoryPressureDurationSec=30s
"""


def _write(tmp_path, canonical, text):
    path = tmp_path / canonical.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# --- reading the effective configuration ---------------------------------- #

def test_a_stock_machine_has_no_configuration(tmp_path):
    """Commented-out defaults are not configuration — capturing them would
    invent settings nobody chose."""
    _write(tmp_path, "/etc/systemd/oomd.conf", _STOCK_OOMD)
    assert OomdAction({}, _ctx(tmp_path))._actual_value() is None


def test_no_file_at_all_is_no_configuration(tmp_path):
    assert OomdAction({}, _ctx(tmp_path))._actual_value() is None


def test_reads_a_value_set_in_the_pacman_owned_file(tmp_path):
    """The exact case that motivated this: an edited backup file."""
    _write(tmp_path, "/etc/systemd/oomd.conf",
           "[OOM]\nDefaultMemoryPressureDurationSec=20s\n")
    action = OomdAction({}, _ctx(tmp_path))
    assert action.import_state(managed=[]) == {
        "oomd": {"DefaultMemoryPressureDurationSec": "20s"}}


def test_reads_a_value_set_in_a_drop_in(tmp_path):
    _write(tmp_path, "/etc/systemd/oomd.conf", _STOCK_OOMD)
    _write(tmp_path, "/etc/systemd/oomd.conf.d/10-dasik.conf",
           "[OOM]\nSwapUsedLimit=80%\n")
    action = OomdAction({}, _ctx(tmp_path))
    assert action.import_state(managed=[]) == {"oomd": {"SwapUsedLimit": "80%"}}


def test_a_drop_in_overrides_the_pacman_owned_file(tmp_path):
    _write(tmp_path, "/etc/systemd/oomd.conf", "[OOM]\nSwapUsedLimit=90%\n")
    _write(tmp_path, "/etc/systemd/oomd.conf.d/10-dasik.conf",
           "[OOM]\nSwapUsedLimit=50%\n")
    action = OomdAction({}, _ctx(tmp_path))
    assert action.import_state(managed=[]) == {"oomd": {"SwapUsedLimit": "50%"}}


def test_the_last_drop_in_wins(tmp_path):
    """systemd applies drop-ins in lexicographic order."""
    _write(tmp_path, "/etc/systemd/oomd.conf.d/10-dasik.conf",
           "[OOM]\nSwapUsedLimit=50%\n")
    _write(tmp_path, "/etc/systemd/oomd.conf.d/99-local.conf",
           "[OOM]\nSwapUsedLimit=70%\n")
    action = OomdAction({}, _ctx(tmp_path))
    assert action.import_state(managed=[]) == {"oomd": {"SwapUsedLimit": "70%"}}


def test_a_section_with_no_settings_is_no_configuration(tmp_path):
    """Arch's system.conf ships a bare `[Manager]` header."""
    _write(tmp_path, "/etc/systemd/system.conf", "[Manager]\n")
    assert SystemdSystemConfAction({}, _ctx(tmp_path)).import_state(managed=[]) == {}


def test_an_unparseable_file_is_tolerated(tmp_path):
    """A hand-broken conf must not crash a `plan` — it reads as unset."""
    _write(tmp_path, "/etc/systemd/oomd.conf", "this is not ini\n")
    assert OomdAction({}, _ctx(tmp_path))._actual_value() is None


# --- planning -------------------------------------------------------------- #

def test_a_declared_value_the_machine_lacks_is_planned(tmp_path):
    _write(tmp_path, "/etc/systemd/oomd.conf", _STOCK_OOMD)
    action = OomdAction({"oomd": {"SwapUsedLimit": "80%"}}, _ctx(tmp_path))
    assert [c.op for c in action.plan(managed=[])] == [Op.MODIFY]


def test_a_declared_value_the_machine_already_has_plans_nothing(tmp_path):
    """Set in the PACMAN file, declared in the config: the effective state
    already matches, so re-applying must not rewrite anything."""
    _write(tmp_path, "/etc/systemd/oomd.conf",
           "[OOM]\nDefaultMemoryPressureDurationSec=20s\n")
    action = OomdAction({"oomd": {"DefaultMemoryPressureDurationSec": "20s"}},
                        _ctx(tmp_path))
    assert action.plan(managed=[]) == []


def test_key_order_and_whitespace_do_not_make_a_change(tmp_path):
    _write(tmp_path, "/etc/systemd/oomd.conf",
           "[OOM]\nSwapUsedLimit =  90%\nDefaultMemoryPressureLimit=60%\n")
    action = OomdAction(
        {"oomd": {"DefaultMemoryPressureLimit": "60%", "SwapUsedLimit": "90%"}},
        _ctx(tmp_path))
    assert action.plan(managed=[]) == []


# --- applying -------------------------------------------------------------- #

def test_apply_writes_a_drop_in_and_never_touches_the_package_file(tmp_path):
    original = _write(tmp_path, "/etc/systemd/oomd.conf", _STOCK_OOMD)
    action = OomdAction({"oomd": {"SwapUsedLimit": "80%"}}, _ctx(tmp_path))

    action.apply(action.plan(managed=[]))

    dropin = tmp_path / "etc/systemd/oomd.conf.d/10-dasik.conf"
    assert "[OOM]" in dropin.read_text()
    assert "SwapUsedLimit = 80%" in dropin.read_text()
    assert original.read_text() == _STOCK_OOMD


def test_applying_twice_is_a_no_op(tmp_path):
    action = OomdAction({"oomd": {"SwapUsedLimit": "80%"}}, _ctx(tmp_path))
    action.apply(action.plan(managed=[]))

    assert action.plan(managed=[]) == []
    assert action.verify() is True


# --- the three domains are distinct ---------------------------------------- #

@pytest.mark.parametrize("cls,key,conf,section", [
    (OomdAction, "oomd", "oomd.conf", "OOM"),
    (SystemdSystemConfAction, "systemd_system_conf", "system.conf", "Manager"),
    (SystemdUserConfAction, "systemd_user_conf", "user.conf", "Manager"),
])
def test_each_action_owns_its_own_file_and_section(tmp_path, cls, key, conf, section):
    action = cls({key: {"LogLevel": "debug"}}, _ctx(tmp_path))

    action.apply(action.plan(managed=[]))

    dropin = tmp_path / f"etc/systemd/{conf}.d/10-dasik.conf"
    assert dropin.read_text() == f"[{section}]\nLogLevel = debug\n"
    assert action.import_state(managed=[]) == {key: {"LogLevel": "debug"}}


def test_the_system_and_user_managers_do_not_share_state(tmp_path):
    _write(tmp_path, "/etc/systemd/system.conf", "[Manager]\nLogLevel=debug\n")

    assert SystemdUserConfAction({}, _ctx(tmp_path)).import_state(managed=[]) == {}


# --- the disable direction ------------------------------------------------- #

def test_dropping_the_block_removes_the_drop_in_dasik_owns(tmp_path):
    """Otherwise `oomd` deleted from the config is a declaration the tool
    ignores: the drop-in stays and the machine keeps the setting forever."""
    _write(tmp_path, "/etc/systemd/oomd.conf.d/10-dasik.conf",
           "[OOM]\nSwapUsedLimit = 80%\n")
    action = OomdAction({}, _ctx(tmp_path))

    changes = action.plan(managed=["[OOM]\nSwapUsedLimit = 80%\n"])
    assert [c.op for c in changes] == [Op.REMOVE]

    action.apply(changes)
    assert not (tmp_path / "etc/systemd/oomd.conf.d/10-dasik.conf").exists()


def test_an_unowned_drop_in_is_left_alone(tmp_path):
    """Nothing in the manifest means dasik never wrote it — somebody else's
    file is not dasik's to delete."""
    _write(tmp_path, "/etc/systemd/oomd.conf.d/10-dasik.conf",
           "[OOM]\nSwapUsedLimit = 80%\n")
    action = OomdAction({}, _ctx(tmp_path))

    assert action.plan(managed=[]) == []


def test_removal_never_touches_the_package_file(tmp_path):
    original = _write(tmp_path, "/etc/systemd/oomd.conf", _STOCK_OOMD)
    _write(tmp_path, "/etc/systemd/oomd.conf.d/10-dasik.conf",
           "[OOM]\nSwapUsedLimit = 80%\n")
    action = OomdAction({}, _ctx(tmp_path))

    action.apply(action.plan(managed=["[OOM]\nSwapUsedLimit = 80%\n"]))

    assert original.read_text() == _STOCK_OOMD


def test_nothing_to_remove_when_there_is_no_drop_in(tmp_path):
    action = OomdAction({}, _ctx(tmp_path))
    assert action.plan(managed=["[OOM]\nSwapUsedLimit = 80%\n"]) == []

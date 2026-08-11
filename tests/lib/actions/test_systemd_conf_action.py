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
from dasik.lib.exceptions.exceptions import ConfigValidationError
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


# --- a drop-in that outranks ours ------------------------------------------ #
#
# dasik always writes 10-dasik.conf, and systemd applies drop-ins in
# lexicographic order. A foreign 99-user.conf setting the same key therefore
# wins forever: apply writes our file, the effective value stays theirs, and the
# next plan proposes the very same change again. Convergence is impossible, so
# the honest move is to refuse before mutating anything.

def test_a_later_drop_in_holding_the_same_key_is_refused(tmp_path):
    _write(tmp_path, "/etc/systemd/oomd.conf.d/99-user.conf",
           "[OOM]\nSwapUsedLimit=99%\n")
    action = OomdAction({"oomd": {"SwapUsedLimit": "90%"}}, _ctx(tmp_path))

    with pytest.raises(ConfigValidationError) as excinfo:
        action.plan(managed=[])

    message = str(excinfo.value)
    assert "99-user.conf" in message      # names the file to fix
    assert "SwapUsedLimit" in message     # names the key in conflict
    assert "10-dasik.conf" in message     # explains why it loses


def test_an_earlier_drop_in_is_not_a_conflict(tmp_path):
    """05-other.conf loses to 10-dasik.conf, so the declared value applies."""
    _write(tmp_path, "/etc/systemd/oomd.conf.d/05-other.conf",
           "[OOM]\nSwapUsedLimit=99%\n")
    action = OomdAction({"oomd": {"SwapUsedLimit": "90%"}}, _ctx(tmp_path))

    assert [c.op for c in action.plan(managed=[])] == [Op.MODIFY]


def test_a_later_drop_in_holding_other_keys_is_not_a_conflict(tmp_path):
    """Only a key dasik declares can be stolen from it."""
    _write(tmp_path, "/etc/systemd/oomd.conf.d/99-user.conf",
           "[OOM]\nDefaultMemoryPressureLimit=60%\n")
    action = OomdAction({"oomd": {"SwapUsedLimit": "90%"}}, _ctx(tmp_path))

    assert [c.op for c in action.plan(managed=[])] == [Op.MODIFY]


def test_a_later_drop_in_agreeing_with_the_config_is_not_a_conflict(tmp_path):
    """It already holds the declared value — the machine is where it should be."""
    _write(tmp_path, "/etc/systemd/oomd.conf.d/99-user.conf",
           "[OOM]\nSwapUsedLimit=90%\n")
    action = OomdAction({"oomd": {"SwapUsedLimit": "90%"}}, _ctx(tmp_path))

    assert action.plan(managed=[]) == []


def test_a_later_drop_in_is_not_a_conflict_when_nothing_is_declared(tmp_path):
    """No declared block, nothing to lose — and the file is not dasik's."""
    _write(tmp_path, "/etc/systemd/oomd.conf.d/99-user.conf",
           "[OOM]\nSwapUsedLimit=99%\n")

    assert OomdAction({}, _ctx(tmp_path)).plan(managed=[]) == []


def test_the_conflict_is_reported_per_file(tmp_path):
    """The system and user managers share a section name but not their files."""
    _write(tmp_path, "/etc/systemd/system.conf.d/99-user.conf",
           "[Manager]\nDefaultTimeoutStopSec=99s\n")
    config = {"systemd_system_conf": {"DefaultTimeoutStopSec": "10s"},
              "systemd_user_conf": {"DefaultTimeoutStopSec": "10s"}}

    with pytest.raises(ConfigValidationError):
        SystemdSystemConfAction(config, _ctx(tmp_path)).plan(managed=[])

    assert [c.op for c in SystemdUserConfAction(config, _ctx(tmp_path))
            .plan(managed=[])] == [Op.MODIFY]


# --- sync reports reality, never the config -------------------------------- #

def test_sync_does_not_report_a_declared_setting_the_machine_lacks(tmp_path):
    """ScalarV3Action falls back to the desired value when the target reads as
    nothing. That is right where "unreadable" is not a state (a machine always
    has a timezone) and wrong here: a stock oomd.conf IS the unset state, so the
    fallback would report a setting nobody applied."""
    _write(tmp_path, "/etc/systemd/oomd.conf", _STOCK_OOMD)
    action = OomdAction({"oomd": {"SwapUsedLimit": "90%"}}, _ctx(tmp_path))

    # The block is CLEARED, not omitted: ConfigWriter.merge overwrites keys and
    # never deletes them, so an omitted block leaves the stale declaration.
    assert action.import_state(managed=[]) == {"oomd": {}}


def test_sync_reports_what_the_machine_has_over_what_the_config_asks_for(tmp_path):
    _write(tmp_path, "/etc/systemd/oomd.conf", "[OOM]\nSwapUsedLimit=70%\n")
    action = OomdAction({"oomd": {"SwapUsedLimit": "90%"}}, _ctx(tmp_path))

    assert action.import_state(managed=[]) == {"oomd": {"SwapUsedLimit": "70%"}}

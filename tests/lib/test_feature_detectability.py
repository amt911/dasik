"""Every declared feature must be VISIBLE in `dasik plan`.

A feature that converges but never shows up in a plan is undebuggable: you
cannot tell "already applied" from "dasik ignores this block", and `apply` then
changes the machine in ways the dry run never announced. This suite pins the
whole of issue #173 block A (PR #174) to that rule — for each feature, a target
missing it produces a change, and a target already carrying it produces none.

`sysrq` is the one that prompted this: on a machine whose boot entry already had
`sysrq_always_enabled=1` the plan is (correctly) silent, which is
indistinguishable from a feature nothing looks at unless the empty case is
asserted too.
"""
from unittest.mock import MagicMock, patch

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.drop_files_action import DropFilesAction
from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction
from dasik.lib.actions.sudo_action import SudoAction
from dasik.lib.actions.systemd_action import SystemdAction
from dasik.lib.expand import expand_config
from dasik.lib.target.target import Target


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


def _entry(tmp_path, options):
    entries = tmp_path / "boot/loader/entries"
    entries.mkdir(parents=True, exist_ok=True)
    (entries / "arch.conf").write_text(f"title Arch\noptions {options}\n")
    (tmp_path / "boot/loader/loader.conf").write_text("default arch\n")


def _cmdline_plan(tmp_path, config, options, managed=()):
    _entry(tmp_path, options)
    action = KernelCmdlineAction({"bootloader": "sd-boot", **config}, _ctx(tmp_path))
    return [(c.op.name, c.item) for c in action.plan(managed=list(managed))]


# --- sysrq (REISUB) -------------------------------------------------------- #

def test_sysrq_missing_from_the_entry_is_planned(tmp_path):
    assert _cmdline_plan(tmp_path, {"sysrq": True}, "root=LABEL=root rw quiet") == [
        ("INSTALL", "sysrq_always_enabled=1")]


def test_sysrq_already_on_the_entry_plans_nothing(tmp_path):
    assert _cmdline_plan(tmp_path, {"sysrq": True},
                         "root=LABEL=root rw sysrq_always_enabled=1") == []


def test_dropping_the_sysrq_flag_removes_a_parameter_dasik_owns(tmp_path):
    """The disable direction is a change too — otherwise `sysrq: false` is a
    declaration the tool silently ignores."""
    assert _cmdline_plan(tmp_path, {}, "root=LABEL=root rw sysrq_always_enabled=1",
                         managed=["sysrq_always_enabled=1"]) == [
        ("REMOVE", "sysrq_always_enabled=1")]


def test_an_unowned_sysrq_parameter_is_left_alone(tmp_path):
    """Somebody else's parameter is not dasik's to delete."""
    assert _cmdline_plan(tmp_path, {}, "root=LABEL=root rw sysrq_always_enabled=1") == []


# --- cpu ------------------------------------------------------------------- #

def test_the_cpu_driver_parameter_is_planned(tmp_path):
    assert _cmdline_plan(tmp_path, {"cpu": {"scaling_driver": "amd_pstate"}},
                         "root=LABEL=root rw") == [("INSTALL", "amd_pstate=active")]


def test_the_cpu_driver_parameter_already_set_plans_nothing(tmp_path):
    assert _cmdline_plan(tmp_path, {"cpu": {"scaling_driver": "amd_pstate"}},
                         "root=LABEL=root rw amd_pstate=active") == []


def test_the_cpu_governor_file_is_planned(tmp_path):
    config = expand_config({"cpu": {"scaling_driver": "none",
                                    "governor": "performance"}})
    action = DropFilesAction(config, _ctx(tmp_path))

    planned = [c.item for c in action.plan(managed=[])]

    assert "/etc/default/cpupower" in planned


def test_power_profiles_daemon_is_planned_as_a_unit(tmp_path):
    config = expand_config({"cpu": {"scaling_driver": "none"}})
    assert "power-profiles-daemon.service" in config["systemd"]["enable_units"]
    assert _units_planned(config) == ["power-profiles-daemon.service"]


# --- reflector ------------------------------------------------------------- #

def test_the_reflector_conf_is_planned(tmp_path):
    config = expand_config({"reflector": {"countries": ["ES"]}})
    action = DropFilesAction(config, _ctx(tmp_path))

    planned = [c.item for c in action.plan(managed=[])]

    assert "/etc/xdg/reflector/reflector.conf" in planned


def test_the_reflector_timer_is_planned_as_a_unit():
    config = expand_config({"reflector": {"countries": ["ES"]}})
    assert _units_planned(config) == ["reflector.timer"]


# --- systemd-boot-update --------------------------------------------------- #

def test_systemd_boot_update_is_planned_on_sd_boot():
    config = expand_config({"bootloader": "sd-boot"})
    assert _units_planned(config) == ["systemd-boot-update.service"]


def test_systemd_boot_update_is_not_planned_on_grub():
    config = expand_config({"bootloader": "grub"})
    assert _units_planned(config) == []


# --- sudo ------------------------------------------------------------------ #

def test_a_missing_sudoers_fragment_is_planned(tmp_path):
    action = SudoAction({"sudo": {"wheel": True}}, _ctx(tmp_path))

    assert [c.op.name for c in action.plan(managed=[])] == ["MODIFY"]


def test_an_applied_sudoers_fragment_plans_nothing(tmp_path):
    (tmp_path / "etc/sudoers.d").mkdir(parents=True)
    (tmp_path / "etc/sudoers.d/10-dasik").write_text(
        "# Managed by dasik\n%wheel ALL=(ALL:ALL) ALL\n")
    action = SudoAction({"sudo": {"wheel": True}}, _ctx(tmp_path))

    assert action.plan(managed=[]) == []


# --- helper ---------------------------------------------------------------- #

def _units_planned(config):
    """Units SystemdAction would enable on a target where nothing is enabled."""
    nothing_enabled = MagicMock(stdout=b"", returncode=0)
    with patch("dasik.lib.actions.systemd_action.Command.execute",
               return_value=nothing_enabled):
        action = SystemdAction(config.get("systemd", {}), _ctx("/mnt"))
        return [c.item for c in action.plan(managed=[])]

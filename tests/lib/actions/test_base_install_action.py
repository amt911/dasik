from unittest.mock import MagicMock, mock_open, patch

import pytest

from dasik.lib.actions.base_install_action import BaseInstallAction
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.command_worker.command_worker import Command
from dasik.lib.exceptions.exceptions import CommandExecutionError
from dasik.lib.target.target import Target
from dasik.lib.state.change import Change, Op


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


def _marker(tmp_path):
    d = tmp_path / "usr" / "bin"
    d.mkdir(parents=True, exist_ok=True)
    (d / "pacman").write_text("")


# --- microcode detection (unchanged behavior) ----------------------------- #

def test_base_packages_without_microcode():
    a = BaseInstallAction({"enable_microcode": False})
    assert a.packages == ["base", "linux", "linux-firmware"]


def test_adds_amd_ucode_on_amd():
    with patch("builtins.open", mock_open(read_data="vendor_id : AuthenticAMD")):
        a = BaseInstallAction({"enable_microcode": True})
    assert "amd-ucode" in a.packages


def test_adds_intel_ucode_on_intel():
    with patch("builtins.open", mock_open(read_data="vendor_id : GenuineIntel")):
        a = BaseInstallAction({"enable_microcode": True})
    assert "intel-ucode" in a.packages


def test_unknown_vendor_exits():
    with patch("builtins.open", mock_open(read_data="vendor_id : Cyrix")):
        with pytest.raises(SystemExit):
            BaseInstallAction({"enable_microcode": True})


# --- v3 contract ---------------------------------------------------------- #

def test_is_v3_true():
    assert BaseInstallAction.is_v3() is True


def test_actual_empty_when_not_installed(tmp_path):
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    assert a.actual() == set()


def test_actual_present_when_marker(tmp_path):
    _marker(tmp_path)
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    assert a.actual() == {"base"}


def test_plan_install_when_absent(tmp_path):
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    changes = a.plan(managed=[])
    assert changes and changes[0].op is Op.INSTALL and changes[0].item == "base"


def test_plan_empty_when_present(tmp_path):
    _marker(tmp_path)
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    assert a.plan(managed=[]) == []


def test_apply_runs_install_when_changes(tmp_path):
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    with patch.object(BaseInstallAction, "_install") as inst:
        a.apply(a.plan(managed=[]))
        inst.assert_called_once()


def test_apply_noop_when_no_changes(tmp_path):
    _marker(tmp_path)
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    with patch.object(BaseInstallAction, "_install") as inst:
        a.apply(a.plan(managed=[]))
        inst.assert_not_called()


def test_apply_skips_reinstall_when_installed_despite_stale_change(tmp_path):
    # re-run idempotency: plan may say INSTALL (computed before /mnt was mounted),
    # but apply re-checks reality and must NOT re-pacstrap an installed base
    _marker(tmp_path)
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    with patch.object(BaseInstallAction, "_install") as inst:
        a.apply([Change("base", Op.INSTALL, "base")])
    inst.assert_not_called()


def test_is_needed_and_verify_track_marker(tmp_path):
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    assert a.is_needed() is True and a.verify() is False
    _marker(tmp_path)
    assert a.is_needed() is False and a.verify() is True


def test_managed_keys(tmp_path):
    _marker(tmp_path)
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    assert a.managed_keys() == {"base": ["base"]}


def test_import_state_empty(tmp_path):
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    assert a.import_state(managed=[]) == {}


def test_install_pacstraps_and_writes_fstab(tmp_path):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    with patch.object(Command, "execute_checked",
                      return_value=MagicMock(stdout=b"UUID=1 / ext4 defaults 0 1\n")) as ck:
        a._install()
    assert (tmp_path / "etc" / "fstab").read_text() == "UUID=1 / ext4 defaults 0 1\n"
    assert any(c.args[0] == "pacstrap" for c in ck.call_args_list)


def test_install_propagates_command_failure(tmp_path):
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    with patch.object(Command, "execute_checked",
                      side_effect=CommandExecutionError("pacstrap ... failed (rc=1): not enough free disk space")):
        with pytest.raises(CommandExecutionError):
            a._install()


def test_cache_to_ram_mounts_tmpfs_before_pacstrap(tmp_path):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))  # chroot target
    with patch.object(Command, "execute_checked",
                      return_value=MagicMock(stdout=b"")) as ck, \
         patch("dasik.lib.actions.base_install_action.os.path.ismount", return_value=False), \
         patch.object(BaseInstallAction, "_available_ram_kb", return_value=2_000_000):
        a._install()
    cmds = [c.args for c in ck.call_args_list]
    names = [c[0] for c in cmds]
    assert "mount" in names and "pacstrap" in names
    assert names.index("mount") < names.index("pacstrap")
    mount_args = cmds[names.index("mount")][1]
    assert "tmpfs" in mount_args
    assert "/var/cache/pacman/pkg" in mount_args          # host cache, NOT under the disk
    assert any(a_.startswith("size=") for a_ in mount_args)


def test_cache_to_ram_skips_on_live_host():
    a = BaseInstallAction({"enable_microcode": False}, ActionContext(target=Target(root="/")))
    with patch.object(Command, "execute_checked", return_value=MagicMock(stdout=b"")) as ck, \
         patch("builtins.open", mock_open()):
        a._install()
    assert "mount" not in [c.args[0] for c in ck.call_args_list]


def test_cache_to_ram_skips_if_already_mounted(tmp_path):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    with patch.object(Command, "execute_checked", return_value=MagicMock(stdout=b"")) as ck, \
         patch("dasik.lib.actions.base_install_action.os.path.ismount", return_value=True), \
         patch.object(BaseInstallAction, "_available_ram_kb", return_value=2_000_000):
        a._install()
    assert "mount" not in [c.args[0] for c in ck.call_args_list]


def test_available_ram_kb_parses_meminfo():
    data = "MemTotal:       4000000 kB\nMemAvailable:    3000000 kB\n"
    with patch("builtins.open", mock_open(read_data=data)):
        assert BaseInstallAction._available_ram_kb() == 3_000_000


def test_name_and_optional():
    a = BaseInstallAction({"enable_microcode": False})
    assert a.name == "Base Installation"
    assert a.is_optional is False

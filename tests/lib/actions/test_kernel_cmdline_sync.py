"""`sync` must bring back the boot entry's own parameters.

import_state used to echo the DECLARED params, so capturing a real machine from
an empty seed dropped everything that was set by hand — `resume=`,
`amd_pstate=`, `nvidia_drm.modeset=1`. On a hibernating machine that silently
removed hibernation from the captured config.

What dasik derives from `disks` is NOT captured: those tokens carry resolved
UUIDs and would pin the config to one machine. They are re-derived on apply.
"""
from unittest.mock import patch

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction
from dasik.lib.actions.luks_uuid import luks_uuid
from dasik.lib.target.target import Target


ROOT_UUID = luks_uuid("cryptroot")


def _cfg(explicit=None):
    cfg = {
        "bootloader": "sd-boot",
        "disks": {"disks": [{"partitions": [
            {"label": "root", "filesystem": "btrfs", "mountpoint": None,
             "encrypt": True, "luks_name": "cryptroot",
             "mount_options": ["compress-force=zstd:3"],
             "btrfs_subvolumes": [{"name": "@", "mountpoint": "/"}]},
        ]}]},
    }
    if explicit is not None:
        cfg["kernel_cmdline"] = explicit
    return cfg


def _captured(live, explicit=None):
    action = KernelCmdlineAction(_cfg(explicit), ActionContext(target=Target(root="/mnt")))
    with patch.object(KernelCmdlineAction, "_current_cmdline", return_value=live):
        return action.import_state(managed=[])["kernel_cmdline"]


def test_captures_a_parameter_nobody_declared():
    live = f"root=/dev/mapper/cryptroot rw resume=/dev/mapper/cryptswap quiet"
    assert _captured(live) == ["resume=/dev/mapper/cryptswap", "quiet"]


def test_does_not_capture_what_it_derives_from_disks():
    live = (f"root=/dev/mapper/cryptroot rw "
            f"rd.luks.name={ROOT_UUID}=cryptroot "
            f"rootflags=compress-force=zstd:3,subvol=@")
    assert _captured(live) == []


def test_captures_an_unlock_for_a_device_the_config_does_not_declare():
    """A second encrypted device that lives outside `disks` must survive sync."""
    other = "rd.luks.name=11111111-1111-1111-1111-111111111111=cryptdata"
    live = f"root=/dev/mapper/cryptroot rw {other}"
    assert _captured(live) == [other]


def test_declared_params_survive_even_when_the_entry_is_unreadable():
    assert _captured("", explicit=["quiet"]) == ["quiet"]


def test_keeps_the_live_order_and_deduplicates():
    live = "quiet splash quiet"
    assert _captured(live) == ["quiet", "splash"]

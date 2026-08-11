from unittest.mock import mock_open, patch

from dasik.lib.actions.initramfs.mkinitcpio import MkinitcpioBackend
from dasik.lib.target.target import Target


_DEFAULT = ("HOOKS=(base udev autodetect modconf kms keyboard keymap "
            "consolefont block filesystems fsck)\n")


def _enc_cfg(fs="ext4"):
    return {"disks": {"disks": [{"partitions": [
        {"encrypt": True, "luks_name": "cryptroot", "mountpoint": "/", "filesystem": fs}]}]}}


def _b(cfg, root="/"):
    return MkinitcpioBackend(cfg, Target(root=root))


def test_desired_moves_keyboard_before_autodetect():
    with patch("builtins.open", mock_open(read_data=_DEFAULT)):
        hooks = _b({}).desired_value().split()
    assert hooks.index("keyboard") < hooks.index("autodetect")


def test_desired_encryption_substitutions():
    with patch("builtins.open", mock_open(read_data=_DEFAULT)):
        hooks = _b(_enc_cfg()).desired_value().split()
    assert "systemd" in hooks and "udev" not in hooks
    assert "sd-vconsole" in hooks and "keymap" not in hooks
    assert "sd-encrypt" in hooks and hooks.index("sd-encrypt") == hooks.index("block") + 1
    assert "consolefont" not in hooks


def test_desired_btrfs_hook_encrypted():
    with patch("builtins.open", mock_open(read_data=_DEFAULT)):
        hooks = _b(_enc_cfg(fs="btrfs")).desired_value().split()
    assert "btrfs" in hooks and hooks.index("btrfs") == hooks.index("systemd") + 1


def test_actual_value_parses_hooks_line():
    """The managed value spans every directive dasik owns (HOOKS, and
    MODULES/FILES once a key device or an embedded keyfile needs them), so it is
    rendered as the conf lines themselves."""
    with patch("builtins.open", mock_open(read_data=_DEFAULT)):
        assert _b({}).actual_value() == (
            "HOOKS=(base udev autodetect modconf kms keyboard keymap "
            "consolefont block filesystems fsck)")


def test_actual_value_none_when_file_absent():
    with patch("builtins.open", side_effect=FileNotFoundError):
        assert _b({}).actual_value() is None


def test_apply_rewrites_hooks_and_runs_mkinitcpio():
    a = _b(_enc_cfg(), root="/")
    m = mock_open(read_data=_DEFAULT)
    with patch("builtins.open", m), \
         patch("dasik.lib.actions.initramfs.mkinitcpio.Command.execute") as run:
        a.apply()
    body = "".join(c.args[0] for c in m().write.call_args_list)
    assert "HOOKS=(" in body and "sd-encrypt" in body
    assert ("mkinitcpio", ["-P"]) == (run.call_args.args[0], run.call_args.args[1])
    assert run.call_args.kwargs["target"].root == "/"

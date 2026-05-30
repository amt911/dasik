from unittest.mock import mock_open, patch

from dasik.lib.actions.mkinitcpio_action import MkinitcpioAction


_DEFAULT_HOOKS = ("HOOKS=(base udev autodetect modconf kms keyboard keymap "
                  "consolefont block filesystems fsck)\n")


def _enc_cfg(luks="cryptroot", fs="ext4"):
    return {"disks": {"disks": [{"partitions": [
        {"encrypt": True, "luks_name": luks, "mountpoint": "/", "filesystem": fs},
    ]}]}}


def test_detect_encryption_true():
    a = MkinitcpioAction(_enc_cfg())
    assert a.has_encryption is True


def test_detect_encryption_false_and_root_fs():
    cfg = {"disks": {"disks": [{"partitions": [
        {"mountpoint": "/", "filesystem": "btrfs"}]}]}}
    a = MkinitcpioAction(cfg)
    assert a.has_encryption is False
    assert a.root_fs == "btrfs"


def test_read_current_hooks_parses_file():
    a = MkinitcpioAction({})
    with patch("builtins.open", mock_open(read_data=_DEFAULT_HOOKS)):
        hooks = a._read_current_hooks()
    assert hooks[0] == "base"
    assert "keymap" in hooks


def test_read_current_hooks_default_when_missing():
    a = MkinitcpioAction({})
    with patch("builtins.open", side_effect=FileNotFoundError):
        assert "base" in a._read_current_hooks()


def test_compute_moves_keyboard_before_autodetect():
    a = MkinitcpioAction({})
    with patch("builtins.open", mock_open(read_data=_DEFAULT_HOOKS)):
        hooks = a._compute_desired_hooks()
    assert hooks.index("keyboard") < hooks.index("autodetect")


def test_compute_encryption_substitutions():
    a = MkinitcpioAction(_enc_cfg())
    with patch("builtins.open", mock_open(read_data=_DEFAULT_HOOKS)):
        hooks = a._compute_desired_hooks()
    assert "systemd" in hooks and "udev" not in hooks
    assert "sd-vconsole" in hooks and "keymap" not in hooks
    assert "sd-encrypt" in hooks
    assert hooks.index("sd-encrypt") == hooks.index("block") + 1
    assert "consolefont" not in hooks


def test_compute_adds_btrfs_hook_encrypted():
    a = MkinitcpioAction(_enc_cfg(fs="btrfs"))
    with patch("builtins.open", mock_open(read_data=_DEFAULT_HOOKS)):
        hooks = a._compute_desired_hooks()
    assert "btrfs" in hooks
    assert hooks.index("btrfs") == hooks.index("systemd") + 1


def test_compute_adds_btrfs_hook_unencrypted():
    cfg = {"disks": {"disks": [{"partitions": [
        {"mountpoint": "/", "filesystem": "btrfs"}]}]}}
    a = MkinitcpioAction(cfg)
    with patch("builtins.open", mock_open(read_data=_DEFAULT_HOOKS)):
        hooks = a._compute_desired_hooks()
    assert "btrfs" in hooks
    assert hooks.index("btrfs") == hooks.index("udev") + 1


def test_is_needed_true_when_hooks_differ():
    a = MkinitcpioAction(_enc_cfg())
    with patch("builtins.open", mock_open(read_data=_DEFAULT_HOOKS)):
        assert a.is_needed() is True


def test_is_needed_false_when_already_desired():
    # No encryption, ext4 → only the keyboard move; feed a file already moved.
    moved = "HOOKS=(base udev autodetect modconf kms keyboard block filesystems fsck)\n"
    cfg = {"disks": {"disks": [{"partitions": [
        {"mountpoint": "/", "filesystem": "ext4"}]}]}}
    a = MkinitcpioAction(cfg)
    # keyboard already after autodetect → recompute equals input
    reordered = "HOOKS=(base udev keyboard autodetect modconf kms block filesystems fsck)\n"
    with patch("builtins.open", mock_open(read_data=reordered)):
        assert a.is_needed() is False
        assert a.verify() is True


def test_name_and_optional():
    a = MkinitcpioAction({})
    assert a.name == "Mkinitcpio Configuration"
    assert a.is_optional is True

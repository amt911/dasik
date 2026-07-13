"""BootloaderAction._root_param: the base entry's root= must match kernel_cmdline.

For an encrypted root, both must be root=/dev/mapper/<luks_name>; otherwise a
stale root=LABEL=… lingers as a duplicate that kernel-cmdline can't remove,
breaking idempotency. Regression: found by running the encrypted VM install.
"""
from types import SimpleNamespace

from dasik.lib.actions.bootloader_action import BootloaderAction


def _bl(disks):
    return BootloaderAction({"bootloader": "sd-boot", "disks": {"disks": disks}},
                            context=SimpleNamespace(target=object()))


def test_encrypted_root_uses_mapper_path():
    a = _bl([{"partitions": [
        {"mountpoint": "/", "label": "ROOT", "encrypt": True, "luks_name": "cryptroot"}]}])
    assert a._root_param() == "root=/dev/mapper/cryptroot"


def test_plain_root_uses_label():
    a = _bl([{"partitions": [{"mountpoint": "/", "label": "ROOT"}]}])
    assert a._root_param() == "root=LABEL=ROOT"


def test_default_when_no_root_partition():
    a = _bl([{"partitions": [{"mountpoint": "/boot", "label": "ESP"}]}])
    assert a._root_param() == "root=LABEL=root"

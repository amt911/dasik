"""A BT keyboard at the LUKS/FIDO2 prompt needs `uhid` in the image, not only
dracut's `bluetooth` module.

bluez creates the input device through /dev/uhid (`UserspaceHID=true` is its
default, for classic and BLE keyboards alike), and dracut's 70bluetooth only
installs `hidp`. Without uhid, bluetoothd logs `input-hog profile accept failed`
at the prompt and the keyboard connects but never types — measured on a real
host, not reasoned about: the image carried bluetoothd, the pairings and
hidp.ko, and no uhid.ko.
"""
import re

from dasik.lib.actions.initramfs.dracut import DracutBackend
from dasik.lib.target.target import Target


def _cfg(bt_initramfs: bool, encrypt: bool = True):
    part = {"mountpoint": "/", "filesystem": "btrfs"}
    if encrypt:
        part.update(encrypt=True, luks_name="cryptroot")
    cfg = {"disks": {"disks": [{"partitions": [part]}]}}
    if bt_initramfs:
        cfg["bluetooth"] = {"enable": True, "in_initramfs": True}
    return cfg


def _drivers(conf: str) -> list:
    found = []
    for m in re.finditer(r'^add_drivers\+="\s*(.*?)\s*"$', conf, re.M):
        found += m.group(1).split()
    return found


def test_bluetooth_in_initramfs_adds_the_uhid_driver():
    conf = DracutBackend(_cfg(True), Target(root="/")).desired_value()
    assert _drivers(conf) == ["uhid"]


def test_uhid_comes_with_bluetooth_even_without_encryption():
    conf = DracutBackend(_cfg(True, encrypt=False), Target(root="/")).desired_value()
    assert _drivers(conf) == ["uhid"]


def test_no_uhid_without_bluetooth_in_initramfs():
    conf = DracutBackend(_cfg(False), Target(root="/")).desired_value()
    assert "add_drivers" not in conf
    assert "uhid" not in conf

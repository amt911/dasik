"""dracut must ship the `resume` module when the config declares hibernation.

VM-proven on 2026-08-08 (config/vm-laptop-hibernate.json): an install with an
encrypted swap partition and `resume=/dev/mapper/cryptswap` on the cmdline came
up with `/sys/power/resume` = `0:0` and `lsinitrd | grep -c resume` = 0 —
`systemctl hibernate` wrote the image, and the next boot was a COLD one.

dracut ships 74resume, but its check() only passes in hostonly mode when a swap
shows up in host_fs_types[]. dasik runs dracut inside `arch-chroot /mnt`, where
that detection is exactly as unreliable as it is for the LUKS root — which is
why the crypt modules are already FORCED. Same fix, same reason.
"""
import re

from dasik.lib.actions.initramfs.base import detect_hibernation
from dasik.lib.actions.initramfs.dracut import DracutBackend
from dasik.lib.target.target import Target


def _cfg(*, swap=False, resume_param=False, encrypt=True, fs="btrfs"):
    parts = [{"mountpoint": "/", "filesystem": fs}]
    if encrypt:
        parts[0].update({"encrypt": True, "luks_name": "cryptroot"})
    if swap:
        parts.insert(0, {"label": "swap", "filesystem": "swap",
                         "encrypt": True, "luks_name": "cryptswap"})
    cfg = {"disks": {"disks": [{"partitions": parts}]}}
    if resume_param:
        cfg["kernel_cmdline"] = ["quiet", "resume=/dev/mapper/cryptswap"]
    return cfg


def _forced(cfg):
    conf = DracutBackend(cfg, Target(root="/")).desired_value()
    m = re.search(r'force_add_dracutmodules\+="\s*(.*?)\s*"', conf)
    return m.group(1).split() if m else []


# --- the detector ---------------------------------------------------------- #

def test_detects_hibernation_from_a_declared_swap_partition():
    assert detect_hibernation(_cfg(swap=True)) is True


def test_detects_hibernation_from_a_resume_kernel_param():
    """A synced config may carry `resume=` with the swap declared elsewhere."""
    assert detect_hibernation(_cfg(swap=False, resume_param=True)) is True


def test_no_hibernation_without_swap_or_resume():
    assert detect_hibernation(_cfg()) is False


def test_resume_param_must_be_a_real_token():
    """`resume_offset=` or a stray substring is not a resume device."""
    assert detect_hibernation({"kernel_cmdline": ["noresume=/dev/x"]}) is False


# --- what lands in dasik.conf ---------------------------------------------- #

def test_swap_partition_forces_the_resume_module():
    assert "resume" in _forced(_cfg(swap=True))


def test_resume_param_alone_forces_the_resume_module():
    assert "resume" in _forced(_cfg(swap=False, resume_param=True))


def test_no_resume_module_without_hibernation():
    assert "resume" not in _forced(_cfg())


def test_resume_joins_the_crypt_modules_without_duplicates():
    mods = _forced(_cfg(swap=True, resume_param=True))
    assert mods == ["crypt", "systemd", "systemd-cryptsetup", "btrfs", "resume"]


def test_unencrypted_swap_still_forces_resume():
    """Hibernation needs the module whether or not the swap is encrypted."""
    cfg = {"disks": {"disks": [{"partitions": [
        {"label": "swap", "filesystem": "swap"},
        {"mountpoint": "/", "filesystem": "ext4"},
    ]}]}}
    assert "resume" in _forced(cfg)

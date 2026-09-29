"""dracut builds a real fallback image next to the host-only one.

dracut has no preset: nothing produced /boot/initramfs-<pkgbase>-fallback.img,
so the rescue entry either loaded the same host-only image as arch.conf or —
on a machine migrated from mkinitcpio — a months-old mkinitcpio image built for
a kernel that was no longer installed. The fallback is a generic
(`--no-hostonly`) image, and a stale one is not converged: it must be at least
as new as the generator's inputs AND as the kernel it boots.
"""
import os
from unittest.mock import mock_open, patch

from dasik.lib.actions.initramfs.dracut import DracutBackend
from dasik.lib.target.target import Target


def _cfg():
    part = {"mountpoint": "/", "filesystem": "btrfs",
            "encrypt": True, "luks_name": "cryptroot"}
    return {"disks": {"disks": [{"partitions": [part]}]}}


def _apply_calls(kernels):
    b = DracutBackend(_cfg(), Target(root="/"))
    with patch("builtins.open", mock_open()), \
         patch("dasik.lib.actions.initramfs.dracut.os.makedirs"), \
         patch("dasik.lib.actions.initramfs.dracut.os.path.exists", return_value=True), \
         patch.object(DracutBackend, "_target_kernels", return_value=kernels), \
         patch("dasik.lib.actions.initramfs.dracut.Command.execute") as run:
        b.apply()
    return [c.args[1] for c in run.call_args_list if c.args[0] == "dracut"]


def test_apply_builds_a_generic_fallback_after_the_hostonly_image():
    calls = _apply_calls([("6.12.1-arch1-1", "linux")])
    assert calls == [
        ["--force", "--fstab", "/boot/initramfs-linux.img", "6.12.1-arch1-1"],
        ["--force", "--no-hostonly", "--fstab",
         "/boot/initramfs-linux-fallback.img", "6.12.1-arch1-1"],
    ]


def test_apply_builds_a_fallback_for_every_kernel():
    calls = _apply_calls([("6.12-arch1", "linux"), ("6.6-lts", "linux-lts")])
    fallbacks = [c[3] for c in calls if "--no-hostonly" in c]
    assert fallbacks == ["/boot/initramfs-linux-fallback.img",
                         "/boot/initramfs-linux-lts-fallback.img"]


# --- freshness: the fallback is part of "converged" ------------------------- #

def _tree(tmp_path, *, fallback=True, vmlinuz=True):
    (tmp_path / "etc" / "dracut.conf.d").mkdir(parents=True)
    (tmp_path / "boot").mkdir()
    mods = tmp_path / "usr" / "lib" / "modules" / "6.9.1-arch1-1"
    mods.mkdir(parents=True)
    (mods / "pkgbase").write_text("linux\n")
    if vmlinuz:
        (tmp_path / "boot" / "vmlinuz-linux").write_text("K")
    (tmp_path / "boot" / "initramfs-linux.img").write_text("IMG")
    if fallback:
        (tmp_path / "boot" / "initramfs-linux-fallback.img").write_text("FB")
    b = DracutBackend(_cfg(), Target(root=str(tmp_path)))
    (tmp_path / "etc" / "dracut.conf.d" / "dasik.conf").write_text(b.desired_value())
    (tmp_path / "etc" / "crypttab").write_text(b.crypttab())
    # inputs and kernel at t=1000, images at t=2000: the state a good run leaves
    for p in [tmp_path / "etc" / "dracut.conf.d" / "dasik.conf",
              tmp_path / "etc" / "dracut.conf.d",
              tmp_path / "boot" / "vmlinuz-linux"]:
        if p.exists():
            os.utime(p, (1000, 1000))
    for img in (tmp_path / "boot").glob("initramfs-*.img"):
        os.utime(img, (2000, 2000))
    return b


def test_converged_when_both_images_are_fresh(tmp_path):
    b = _tree(tmp_path)
    assert b.actual_value() == b.desired_value()


def test_not_converged_without_a_fallback_image(tmp_path):
    b = _tree(tmp_path, fallback=False)
    assert b.actual_value() is None


def test_not_converged_when_the_fallback_predates_the_config(tmp_path):
    b = _tree(tmp_path)
    os.utime(tmp_path / "boot" / "initramfs-linux-fallback.img", (500, 500))
    assert b.actual_value() is None


def test_not_converged_when_the_fallback_predates_its_kernel(tmp_path):
    """The migrated-from-mkinitcpio machine: a fallback from May next to a
    kernel installed in September. Its modules are gone; it cannot boot."""
    b = _tree(tmp_path)
    os.utime(tmp_path / "boot" / "vmlinuz-linux", (3000, 3000))
    os.utime(tmp_path / "boot" / "initramfs-linux.img", (3000, 3000))
    assert b.actual_value() is None


def test_a_missing_vmlinuz_does_not_block_convergence(tmp_path):
    # A UKI setup, or a kernel image that lives elsewhere: nothing to compare.
    b = _tree(tmp_path, vmlinuz=False)
    assert b.actual_value() == b.desired_value()

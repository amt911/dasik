"""A second kernel you cannot boot is a second kernel you do not have.

Declaring `linux-lts` installs the package and mkinitcpio builds
/boot/initramfs-linux-lts.img — and then nothing points at it. dasik writes
exactly two systemd-boot entries, both hardcoded to /vmlinuz-linux, so the LTS
kernel sits on the ESP unbootable while `plan` says the machine matches the
config. Driven in a VM (round N):

    /boot/vmlinuz-linux      /boot/initramfs-linux.img
    /boot/vmlinuz-linux-lts  /boot/initramfs-linux-lts.img
    /boot/loader/entries: arch.conf  arch-fallback.conf     <- both /vmlinuz-linux

GRUB users never saw this: grub-mkconfig enumerates the kernels itself. It is a
systemd-boot gap, so the fix lives on that side.
"""
import os

from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.bootloader_action import BootloaderAction
from dasik.lib.state.change import Op
from dasik.lib.target.target import Target


def _esp(tmp_path, kernels=("linux",), fallbacks=(), entries=()):
    boot = tmp_path / "boot"
    (boot / "loader/entries").mkdir(parents=True, exist_ok=True)
    for k in kernels:
        (boot / f"vmlinuz-{k}").write_text("")
        (boot / f"initramfs-{k}.img").write_text("")
    for k in fallbacks:
        (boot / f"initramfs-{k}-fallback.img").write_text("")
    for name in entries:
        (boot / "loader/entries" / name).write_text("title old\n")
    (boot / "EFI/systemd").mkdir(parents=True, exist_ok=True)      # sd-boot marker
    (boot / "EFI/systemd/systemd-bootx64.efi").write_text("")
    return tmp_path


def _action(tmp_path, bootloader="sd-boot", packages=("base", "linux")):
    """Desired state comes from the CONFIG: the declared kernel packages."""
    return BootloaderAction({"bootloader": bootloader, "disks": {"disks": []},
                             "packages": list(packages)},
                            ActionContext(target=Target(root=str(tmp_path))))


def _items(changes, op):
    return sorted(c.item for c in changes if c.op is op)


def test_a_kernel_with_no_entry_is_planned(tmp_path):
    action = _action(_esp(tmp_path, kernels=("linux", "linux-lts"),
                          entries=("arch.conf", "arch-fallback.conf")),
                     packages=("base", "linux", "linux-lts"))

    assert _items(action.plan(managed=[]), Op.INSTALL) == ["entry:linux-lts"]


def test_the_default_kernel_keeps_its_own_entry_name(tmp_path):
    """arch.conf is the historical name for `linux`; renaming it would orphan
    every machine that already boots from it."""
    action = _action(_esp(tmp_path, kernels=("linux",),
                          entries=("arch.conf", "arch-fallback.conf")))

    assert action.plan(managed=[]) == []


def test_apply_writes_an_entry_that_points_at_that_kernel(tmp_path):
    root = _esp(tmp_path, kernels=("linux", "linux-lts"),
                entries=("arch.conf", "arch-fallback.conf"))
    action = _action(root, packages=("base", "linux", "linux-lts"))

    with patch("dasik.lib.actions.bootloader_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0)
        action.apply(action.plan(managed=[]))

    entry = (root / "boot/loader/entries/linux-lts.conf").read_text()
    assert "linux /vmlinuz-linux-lts" in entry
    assert "initrd /initramfs-linux-lts.img" in entry
    assert "linux-lts" in entry.splitlines()[0]          # the title names it
    assert action.plan(managed=[]) == []                 # converges


def test_its_fallback_image_gets_an_entry_too(tmp_path):
    root = _esp(tmp_path, kernels=("linux", "linux-lts"), fallbacks=("linux-lts",),
                entries=("arch.conf", "arch-fallback.conf"))
    action = _action(root, packages=("base", "linux", "linux-lts"))

    with patch("dasik.lib.actions.bootloader_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0)
        action.apply(action.plan(managed=[]))

    entry = (root / "boot/loader/entries/linux-lts-fallback.conf").read_text()
    assert "initrd /initramfs-linux-lts-fallback.img" in entry


def test_a_kernel_that_is_gone_takes_its_entry_with_it(tmp_path):
    root = _esp(tmp_path, kernels=("linux",),
                entries=("arch.conf", "arch-fallback.conf", "linux-lts.conf"))
    action = _action(root)

    changes = action.plan(managed=["entry:linux-lts"])

    assert _items(changes, Op.REMOVE) == ["entry:linux-lts"]

    with patch("dasik.lib.actions.bootloader_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0)
        action.apply(changes)

    assert not (root / "boot/loader/entries/linux-lts.conf").exists()


def test_an_entry_dasik_does_not_own_is_left_alone(tmp_path):
    """Somebody's hand-written entry for a kernel they manage themselves."""
    root = _esp(tmp_path, kernels=("linux",),
                entries=("arch.conf", "arch-fallback.conf", "mine.conf"))
    action = _action(root)

    assert action.plan(managed=[]) == []
    assert (root / "boot/loader/entries/mine.conf").exists()


def test_grub_is_left_to_grub_mkconfig(tmp_path):
    """grub-mkconfig enumerates kernels itself; a second entry writer there
    would fight it on every apply."""
    root = _esp(tmp_path, kernels=("linux", "linux-lts"))
    (root / "boot/grub").mkdir(parents=True, exist_ok=True)
    (root / "boot/grub/grub.cfg").write_text("")
    action = _action(root, bootloader="grub", packages=("base", "linux", "linux-lts"))

    assert [c for c in action.plan(managed=[]) if str(c.item).startswith("entry:")] == []


def test_a_kernel_whose_initramfs_is_missing_gets_no_entry(tmp_path):
    """An entry whose initrd is absent makes systemd-boot fail at boot with
    "Error preparing initrd: Not found" — the shape of #159. Half a kernel is
    not a bootable kernel."""
    root = _esp(tmp_path, kernels=("linux",), entries=("arch.conf", "arch-fallback.conf"))
    (root / "boot/vmlinuz-linux-zen").write_text("")        # no initramfs beside it

    action = _action(root)

    assert action.plan(managed=[]) == []
    assert not (root / "boot/loader/entries/linux-zen.conf").exists()


def test_the_new_entry_carries_the_same_options_as_arch_conf(tmp_path):
    """A second kernel boots the same root: without `rootflags=…subvol=@` it
    would not mount at all, and nothing would notice — the cmdline domain reads
    only the default entry."""
    root = _esp(tmp_path, kernels=("linux", "linux-lts"),
                entries=("arch-fallback.conf",))
    (root / "boot/loader/entries/arch.conf").write_text(
        "title Arch Linux\nlinux /vmlinuz-linux\ninitrd /initramfs-linux.img\n"
        "options root=LABEL=ROOT rw rootflags=subvol=@ splash\n")
    action = _action(root, packages=("base", "linux", "linux-lts"))

    with patch("dasik.lib.actions.bootloader_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0)
        action.apply(action.plan(managed=[]))

    entry = (root / "boot/loader/entries/linux-lts.conf").read_text()
    assert "options root=LABEL=ROOT rw rootflags=subvol=@ splash" in entry


def test_without_an_arch_conf_it_still_writes_a_bootable_options_line(tmp_path):
    root = _esp(tmp_path, kernels=("linux", "linux-lts"))
    action = _action(root, packages=("base", "linux", "linux-lts"))

    with patch("dasik.lib.actions.bootloader_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0)
        action.apply([c for c in action.plan(managed=[]) if str(c.item).startswith("entry:")])

    options = [l for l in (root / "boot/loader/entries/linux-lts.conf").read_text().splitlines()
               if l.startswith("options ")][0]
    assert "root=" in options and options.endswith("rw")


def test_the_firmware_split_packages_are_not_kernels(tmp_path):
    """`linux-firmware-marvell` and its siblings start with `linux-` and end in
    none of the excluded suffixes. Treating them as kernels asks for an entry
    that can never be written — and asks again on every plan, for ever."""
    root = _esp(tmp_path, kernels=("linux",), entries=("arch.conf", "arch-fallback.conf"))
    action = _action(root, packages=("base", "linux", "linux-firmware",
                                     "linux-firmware-marvell", "linux-firmware-nvidia"))

    assert action.plan(managed=[]) == []


def test_a_package_that_is_installed_without_a_kernel_image_is_not_a_kernel(tmp_path):
    """The name is a guess; the machine is the fact. If the package is installed
    and brought no vmlinuz, it is not a kernel whatever it is called."""
    root = _esp(tmp_path, kernels=("linux",), entries=("arch.conf", "arch-fallback.conf"))
    (root / "var/lib/pacman/local/linux-oddball-1.0-1").mkdir(parents=True)
    action = _action(root, packages=("base", "linux", "linux-oddball"))

    assert action.plan(managed=[]) == []


def test_a_kernel_that_is_not_installed_yet_is_still_planned(tmp_path):
    """The install case: the package arrives in the same apply, so the image is
    not there at plan time and the entry must still be proposed."""
    root = _esp(tmp_path, kernels=("linux",), entries=("arch.conf", "arch-fallback.conf"))
    (root / "var/lib/pacman/local").mkdir(parents=True)
    action = _action(root, packages=("base", "linux", "linux-lts"))

    assert _items(action.plan(managed=[]), Op.INSTALL) == ["entry:linux-lts"]

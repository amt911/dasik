"""dasik must not rewrite a boot entry that belongs to somebody else.

`apply` wrote its options line into EVERY *.conf in /boot/loader/entries:

    for entry in self._sdboot_entries():
        self._write_sdboot(entry, line)

On a shared ESP that is another distribution's entry — same directory, same
loader, different root — and dasik hands it ITS `root=`, `rootflags=` and the
rest. The other system then boots into this one's filesystem, or not at all.
Nothing in the plan mentions it: the domain is `kernel_cmdline`, not per-entry.

dasik's own entries are `arch.conf`, `arch-fallback.conf` and one per declared
kernel. Everything else on that ESP is somebody's business, not dasik's.
"""
import os

from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target

_OURS = ("title Arch Linux\nlinux /vmlinuz-linux\n"
         "initrd /initramfs-linux.img\noptions root=LABEL=ROOT rw\n")
_THEIRS = ("title Fedora Linux\nlinux /vmlinuz-6.9-fedora\n"
           "initrd /initramfs-fedora.img\noptions root=UUID=deadbeef ro quiet\n")
_WINDOWS = "title Windows\nefi /EFI/Microsoft/Boot/bootmgfw.efi\n"


def _esp(tmp_path, entries):
    d = tmp_path / "boot/loader/entries"
    d.mkdir(parents=True)
    for name, body in entries.items():
        (d / name).write_text(body)
    (tmp_path / "boot/loader/loader.conf").write_text("default arch\ntimeout 3\n")
    return tmp_path


def _apply(tmp_path, config=None):
    action = KernelCmdlineAction(
        {"bootloader": "sd-boot", "kernel_cmdline": ["quiet"], "packages": ["base", "linux"],
         **(config or {})},
        ActionContext(target=Target(root=str(tmp_path))))
    with patch("dasik.lib.actions.kernel_cmdline_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0)
        action.apply([Change("kernel_cmdline", Op.INSTALL, "quiet")])
    return tmp_path / "boot/loader/entries"


def test_another_distributions_entry_is_left_alone(tmp_path):
    entries = _apply(_esp(tmp_path, {"arch.conf": _OURS, "fedora.conf": _THEIRS}))

    assert (entries / "fedora.conf").read_text() == _THEIRS
    assert "quiet" in (entries / "arch.conf").read_text()


def test_our_own_entries_are_all_updated(tmp_path):
    entries = _apply(_esp(tmp_path, {"arch.conf": _OURS, "arch-fallback.conf": _OURS}))

    assert "quiet" in (entries / "arch.conf").read_text()
    assert "quiet" in (entries / "arch-fallback.conf").read_text()


def test_a_declared_kernels_entry_counts_as_ours(tmp_path):
    entries = _apply(_esp(tmp_path, {"arch.conf": _OURS, "linux-lts.conf": _OURS,
                                     "linux-lts-fallback.conf": _OURS}),
                     config={"packages": ["base", "linux", "linux-lts"]})

    assert "quiet" in (entries / "linux-lts.conf").read_text()
    assert "quiet" in (entries / "linux-lts-fallback.conf").read_text()


def test_an_undeclared_kernels_entry_is_not_ours(tmp_path):
    entries = _apply(_esp(tmp_path, {"arch.conf": _OURS, "linux-zen.conf": _THEIRS}))

    assert (entries / "linux-zen.conf").read_text() == _THEIRS


def test_an_entry_with_no_options_line_is_untouched(tmp_path):
    entries = _apply(_esp(tmp_path, {"arch.conf": _OURS, "windows.conf": _WINDOWS}))

    assert (entries / "windows.conf").read_text() == _WINDOWS

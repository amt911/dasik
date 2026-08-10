"""Which loader entry the plan compares against.

Reading `os.listdir()[0]` stopped being deterministic the moment a second entry
existed: `arch-fallback.conf` sorts before `arch.conf`, so the plan could diff
against the rescue entry instead of the one the firmware boots.
"""
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction
from dasik.lib.target.target import Target


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


def _entries(root, main: str, fallback: str, default: str = "arch"):
    entries = root / "boot/loader/entries"
    entries.mkdir(parents=True)
    (entries / "arch.conf").write_text(
        f"title Arch Linux\nlinux /vmlinuz-linux\noptions {main}\n")
    (entries / "arch-fallback.conf").write_text(
        f"title Arch Linux (fallback initramfs)\nlinux /vmlinuz-linux\noptions {fallback}\n")
    (root / "boot/loader/loader.conf").write_text(f"default {default}\ntimeout 3\n")


def test_reads_the_entry_loader_conf_points_at(tmp_path):
    _entries(tmp_path, main="root=LABEL=root rw quiet", fallback="root=LABEL=root rw")
    action = KernelCmdlineAction({"bootloader": "sd-boot"}, _ctx(tmp_path))
    assert "quiet" in action.actual()


def test_accepts_a_default_written_with_the_conf_suffix(tmp_path):
    _entries(tmp_path, main="root=LABEL=root rw quiet", fallback="root=LABEL=root rw",
             default="arch.conf")
    action = KernelCmdlineAction({"bootloader": "sd-boot"}, _ctx(tmp_path))
    assert "quiet" in action.actual()


def test_falls_back_to_arch_conf_without_a_loader_conf(tmp_path):
    entries = tmp_path / "boot/loader/entries"
    entries.mkdir(parents=True)
    (entries / "arch-fallback.conf").write_text("options root=LABEL=root rw\n")
    (entries / "arch.conf").write_text("options root=LABEL=root rw quiet\n")
    action = KernelCmdlineAction({"bootloader": "sd-boot"}, _ctx(tmp_path))
    assert "quiet" in action.actual()


def test_no_entries_reads_empty(tmp_path):
    action = KernelCmdlineAction({"bootloader": "sd-boot"}, _ctx(tmp_path))
    assert action.actual() == set()

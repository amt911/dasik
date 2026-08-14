"""A capture from nothing must still read the boot entry.

`dasik sync` on a `{}` seed is the way you adopt a machine you did not install.
On an sd-boot machine it produced this:

    "apparmor": {"enable": false, "audit": false, "desktop_notifications": true}

and `dasik check` then refused the file, because notifications without the audit
framework is a contradiction the schema does not accept.

Nothing was wrong with the machine. `KernelCmdlineAction` takes the bootloader
from the CONFIG, defaulting to grub, so with an empty seed it looked for
/etc/default/grub on a machine that boots systemd-boot, found nothing, and
reported every cmdline-derived fact as absent: `lsm=` (apparmor), `audit=1`,
`sysrq_always_enabled`, the cpu scaling driver, and the whole `kernel_cmdline`.
"""
import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.apparmor_action import ApparmorAction
from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction
from dasik.lib.target.target import Target

_OPTS = ("root=LABEL=ROOT rw audit=1 audit_backlog_limit=8192 "
         "lsm=landlock,lockdown,yama,integrity,apparmor,bpf sysrq_always_enabled=1")


def _sdboot_machine(tmp_path):
    (tmp_path / "boot/loader/entries").mkdir(parents=True)
    (tmp_path / "boot/loader/loader.conf").write_text("default arch\n")
    (tmp_path / "boot/loader/entries/arch.conf").write_text(
        f"title Arch\noptions {_OPTS}\n")
    (tmp_path / "usr/bin").mkdir(parents=True)
    (tmp_path / "usr/bin/apparmor_parser").write_text("")
    (tmp_path / "usr/bin/auditd").write_text("")
    return tmp_path


def _grub_machine(tmp_path):
    (tmp_path / "etc/default").mkdir(parents=True)
    (tmp_path / "etc/default/grub").write_text(f'GRUB_CMDLINE_LINUX="{_OPTS}"\n')
    (tmp_path / "usr/bin").mkdir(parents=True)
    (tmp_path / "usr/bin/apparmor_parser").write_text("")
    (tmp_path / "usr/bin/auditd").write_text("")
    return tmp_path


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


def test_a_bare_seed_reads_an_sd_boot_entry(tmp_path):
    params = KernelCmdlineAction({}, _ctx(_sdboot_machine(tmp_path))).live_params()

    assert "audit=1" in params
    assert "sysrq_always_enabled=1" in params


def test_a_bare_seed_still_reads_grub(tmp_path):
    params = KernelCmdlineAction({}, _ctx(_grub_machine(tmp_path))).live_params()

    assert "audit=1" in params


def test_a_declared_bootloader_still_wins(tmp_path):
    """A config that says grub is asking about grub, whatever is installed."""
    root = _sdboot_machine(tmp_path)
    (root / "etc/default").mkdir(parents=True)
    (root / "etc/default/grub").write_text('GRUB_CMDLINE_LINUX="quiet"\n')

    params = KernelCmdlineAction({"bootloader": "grub"}, _ctx(root)).live_params()

    assert params == ["quiet"]


def test_the_apparmor_capture_is_no_longer_a_contradiction(tmp_path):
    """The whole point: what sync writes must be a config check accepts."""
    captured = ApparmorAction({}, _ctx(_sdboot_machine(tmp_path))).import_state([])

    assert captured["apparmor"]["enable"] is True
    assert captured["apparmor"]["audit"] is True


def test_and_the_sysrq_flag_survives_a_bare_capture(tmp_path):
    captured = KernelCmdlineAction({}, _ctx(_sdboot_machine(tmp_path))).import_state([])

    assert captured.get("sysrq") is True

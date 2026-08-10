"""Kernel parameters derived from the `cpu` block and the `sysrq` flag.

They ride the AUTO channel, so an explicit `kernel_cmdline` still wins, and
`import_state` must subtract them — otherwise every `sync` would copy the
derived parameter into the config and duplicate the declaration.
"""
import pytest

from dasik.lib.actions import kernel_cmdline_action as kca
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction
from dasik.lib.target.target import Target


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


@pytest.fixture
def amd(monkeypatch):
    monkeypatch.setattr(KernelCmdlineAction, "_cpu_vendor", staticmethod(lambda: "amd"))


@pytest.fixture
def intel(monkeypatch):
    monkeypatch.setattr(KernelCmdlineAction, "_cpu_vendor", staticmethod(lambda: "intel"))


def test_auto_on_amd_derives_amd_pstate(amd):
    action = KernelCmdlineAction({"cpu": {"scaling_driver": "auto", "mode": "active"}})
    assert "amd_pstate=active" in action.desired_params


def test_auto_on_intel_derives_intel_pstate(intel):
    action = KernelCmdlineAction({"cpu": {"scaling_driver": "auto", "mode": "active"}})
    assert "intel_pstate=active" in action.desired_params


def test_explicit_driver_ignores_the_detected_vendor(intel):
    action = KernelCmdlineAction({"cpu": {"scaling_driver": "amd_pstate", "mode": "guided"}})
    assert "amd_pstate=guided" in action.desired_params


def test_guided_on_intel_degrades_to_active(intel):
    action = KernelCmdlineAction({"cpu": {"scaling_driver": "auto", "mode": "guided"}})
    assert "intel_pstate=active" in action.desired_params


def test_driver_none_derives_nothing(amd):
    action = KernelCmdlineAction({"cpu": {"scaling_driver": "none"}})
    assert not [p for p in action.desired_params if "pstate" in p]


def test_unknown_vendor_derives_nothing(monkeypatch):
    monkeypatch.setattr(KernelCmdlineAction, "_cpu_vendor", staticmethod(lambda: None))
    action = KernelCmdlineAction({"cpu": {"scaling_driver": "auto"}})
    assert not [p for p in action.desired_params if "pstate" in p]


def test_acpi_cpufreq_stands_the_builtin_driver_down(amd):
    action = KernelCmdlineAction({"cpu": {"scaling_driver": "acpi_cpufreq"}})
    assert "amd_pstate=disable" in action.desired_params


def test_explicit_kernel_cmdline_beats_the_derived_value(amd):
    action = KernelCmdlineAction({"cpu": {"scaling_driver": "auto", "mode": "active"},
                                  "kernel_cmdline": ["amd_pstate=passive"]})
    assert "amd_pstate=passive" in action.desired_params
    assert "amd_pstate=active" not in action.desired_params


def test_sysrq_flag_derives_the_parameter():
    action = KernelCmdlineAction({"sysrq": True})
    assert "sysrq_always_enabled=1" in action.desired_params


def test_sysrq_absent_derives_nothing():
    action = KernelCmdlineAction({})
    assert action.desired_params == []


def test_cpu_vendor_reads_proc_cpuinfo(monkeypatch, tmp_path):
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("vendor_id\t: AuthenticAMD\n")
    monkeypatch.setattr(kca, "_CPUINFO", str(cpuinfo))
    assert KernelCmdlineAction._cpu_vendor() == "amd"


def test_cpu_vendor_is_none_without_proc_cpuinfo(monkeypatch, tmp_path):
    monkeypatch.setattr(kca, "_CPUINFO", str(tmp_path / "missing"))
    assert KernelCmdlineAction._cpu_vendor() is None


def test_import_state_does_not_re_emit_derived_cpu_params(tmp_path, amd):
    entries = tmp_path / "boot/loader/entries"
    entries.mkdir(parents=True)
    (entries / "arch.conf").write_text(
        "options root=LABEL=root rw amd_pstate=active sysrq_always_enabled=1 quiet\n")
    (tmp_path / "boot/loader/loader.conf").write_text("default arch\n")

    action = KernelCmdlineAction({"bootloader": "sd-boot", "sysrq": True,
                                  "cpu": {"scaling_driver": "auto", "mode": "active"}},
                                 _ctx(tmp_path))
    captured = action.import_state()["kernel_cmdline"]

    assert "amd_pstate=active" not in captured
    assert "sysrq_always_enabled=1" not in captured
    assert "quiet" in captured


def _entry(tmp_path, options):
    entries = tmp_path / "boot/loader/entries"
    entries.mkdir(parents=True)
    (entries / "arch.conf").write_text(f"options {options}\n")
    (tmp_path / "boot/loader/loader.conf").write_text("default arch\n")


def test_import_state_captures_a_hand_set_pstate_as_the_block(tmp_path):
    """A parameter dasik knows how to derive comes back as its declaration.

    Keeping it in `kernel_cmdline` (as this used to) meant a synced config
    described the same policy twice, and never grew the `cpu` block that owns
    it.
    """
    _entry(tmp_path, "root=LABEL=root rw amd_pstate=active")

    captured = KernelCmdlineAction({"bootloader": "sd-boot"}, _ctx(tmp_path)).import_state()

    assert "amd_pstate=active" not in captured["kernel_cmdline"]


def test_import_state_captures_a_live_sysrq_as_the_flag(tmp_path):
    _entry(tmp_path, "root=LABEL=root rw sysrq_always_enabled=1 quiet")

    captured = KernelCmdlineAction({"bootloader": "sd-boot"}, _ctx(tmp_path)).import_state()

    assert captured["sysrq"] is True
    assert "sysrq_always_enabled=1" not in captured["kernel_cmdline"]
    assert "quiet" in captured["kernel_cmdline"]


def test_import_state_clears_a_flag_the_live_entry_does_not_carry(tmp_path):
    """sync captures reality: a declared flag that never reached the entry is
    not preserved just because it was declared."""
    _entry(tmp_path, "root=LABEL=root rw quiet")

    action = KernelCmdlineAction({"bootloader": "sd-boot", "sysrq": True}, _ctx(tmp_path))

    assert action.import_state()["sysrq"] is False


def test_import_state_leaves_the_flag_alone_without_a_readable_entry():
    """No target, nothing read — sync must not claim the flag is off."""
    captured = KernelCmdlineAction({"bootloader": "sd-boot", "sysrq": True}).import_state()

    assert "sysrq" not in captured

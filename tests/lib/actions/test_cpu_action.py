"""`sync` reconstructs the `cpu` block from the machine.

The block's convergence lives in the expand toggle (packages, units, the
cpupower default file) and in the kernel cmdline; nothing owned it on the way
back, so a synced config lost the declaration and kept a bare
`amd_pstate=active` in `kernel_cmdline` instead.
"""
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.cpu_action import CpuAction
from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction
from dasik.lib.models.cpu_model import CpuModel
from dasik.lib.target.target import Target


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


def _entry(tmp_path, options):
    entries = tmp_path / "boot/loader/entries"
    entries.mkdir(parents=True)
    (entries / "arch.conf").write_text(f"options {options}\n")
    (tmp_path / "boot/loader/loader.conf").write_text("default arch\n")


def _capture(tmp_path, config=None, enabled=True):
    """import_state() with `systemctl is-enabled` stubbed."""
    result = MagicMock(stdout=b"enabled\n" if enabled else b"disabled\n", returncode=0)
    with patch("dasik.lib.actions.cpu_action.Command.execute", return_value=result):
        action = CpuAction({"bootloader": "sd-boot", **(config or {})}, _ctx(tmp_path))
        return action.import_state()


def test_captures_the_driver_and_mode_from_the_live_entry(tmp_path):
    _entry(tmp_path, "root=LABEL=root rw amd_pstate=active quiet")

    assert _capture(tmp_path)["cpu"]["scaling_driver"] == "amd_pstate"
    assert _capture(tmp_path)["cpu"]["mode"] == "active"


def test_captures_an_intel_driver_with_its_mode(tmp_path):
    _entry(tmp_path, "root=LABEL=root rw intel_pstate=passive")

    assert _capture(tmp_path)["cpu"] == {
        "scaling_driver": "intel_pstate",
        "mode": "passive",
        "power_profiles_daemon": True,
    }


def test_a_disabled_pstate_is_the_acpi_cpufreq_declaration(tmp_path):
    """`amd_pstate=disable` is what dasik emits for scaling_driver=acpi_cpufreq
    (the built-in driver has to stand down first), so that is what comes back."""
    _entry(tmp_path, "root=LABEL=root rw amd_pstate=disable")

    assert _capture(tmp_path)["cpu"]["scaling_driver"] == "acpi_cpufreq"


def test_captures_the_cpupower_governor(tmp_path):
    _entry(tmp_path, "root=LABEL=root rw amd_pstate=active")
    (tmp_path / "etc/default").mkdir(parents=True)
    (tmp_path / "etc/default/cpupower").write_text(
        '# Managed by dasik\ngovernor="performance"\n')

    assert _capture(tmp_path)["cpu"]["governor"] == "performance"


def test_power_profiles_daemon_reflects_the_unit(tmp_path):
    _entry(tmp_path, "root=LABEL=root rw amd_pstate=active")

    assert _capture(tmp_path, enabled=False)["cpu"]["power_profiles_daemon"] is False


def test_a_machine_with_no_cpu_policy_captures_nothing(tmp_path):
    """ppd alone is already captured as a package and a unit; inventing a `cpu`
    block for every machine would be noise."""
    _entry(tmp_path, "root=LABEL=root rw quiet")

    assert _capture(tmp_path) == {}


def test_a_governor_alone_still_captures_the_block(tmp_path):
    _entry(tmp_path, "root=LABEL=root rw quiet")
    (tmp_path / "etc/default").mkdir(parents=True)
    (tmp_path / "etc/default/cpupower").write_text('governor="schedutil"\n')

    captured = _capture(tmp_path)["cpu"]

    assert captured["governor"] == "schedutil"
    assert captured["scaling_driver"] == "none"


def test_unreadable_entry_captures_nothing(tmp_path):
    assert _capture(tmp_path) == {}


def test_the_captured_block_is_a_valid_cpu_declaration(tmp_path):
    _entry(tmp_path, "root=LABEL=root rw amd_pstate=guided")

    assert CpuModel(**_capture(tmp_path)["cpu"]).scaling_driver == "amd_pstate"


def test_the_captured_block_re_derives_the_same_parameter(tmp_path):
    """Round-trip: applying a synced config must not change the boot entry."""
    _entry(tmp_path, "root=LABEL=root rw amd_pstate=guided")

    captured = _capture(tmp_path)
    derived = KernelCmdlineAction(captured).desired_params

    assert "amd_pstate=guided" in derived


def test_capture_only_action_plans_nothing_but_is_reached_by_sync(tmp_path):
    """Convergence belongs to the expand toggle; sync only visits v3 actions."""
    action = CpuAction({"cpu": {"scaling_driver": "amd_pstate"}}, _ctx(tmp_path))

    assert action.plan(managed=[]) == []
    assert CpuAction.is_v3() is True


@pytest.mark.parametrize("token", ["amd_pstate=active", "intel_pstate=active"])
def test_the_kernel_cmdline_domain_no_longer_keeps_the_parameter(tmp_path, token):
    """The two actions must agree: cmdline drops it, cpu captures it."""
    _entry(tmp_path, f"root=LABEL=root rw {token}")

    kept = KernelCmdlineAction({"bootloader": "sd-boot"},
                               _ctx(tmp_path)).import_state()["kernel_cmdline"]

    assert token not in kept
    assert _capture(tmp_path)["cpu"]["scaling_driver"] == token.split("=")[0]


def test_a_failing_ppd_probe_still_captures_the_block(tmp_path):
    """A probe that cannot run (no systemctl, no arch-chroot for this target)
    must not cost the whole `cpu` declaration: the parameter is right there in
    the entry. power_profiles_daemon falls back to the model's default rather
    than to False, which would drop a service the machine may well be running.
    """
    from dasik.lib.exceptions.exceptions import CommandNotFoundException

    _entry(tmp_path, "root=LABEL=root rw amd_pstate=active")

    with patch("dasik.lib.actions.cpu_action.Command.execute",
               side_effect=CommandNotFoundException("Binary not found: arch-chroot")):
        captured = CpuAction({"bootloader": "sd-boot"}, _ctx(tmp_path)).import_state()

    assert captured["cpu"]["scaling_driver"] == "amd_pstate"
    assert captured["cpu"]["power_profiles_daemon"] is True

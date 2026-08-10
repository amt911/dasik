"""Every feature must survive a round trip through `dasik sync`.

The mirror of `test_feature_detectability.py`: a feature `apply` converges but
`sync` cannot read back is a one-way street — capture a machine, re-apply the
captured config, and the feature silently disappears. Worse, the ones that ride
another domain (`sysrq` as a kernel parameter, `cpu` as a parameter plus a unit
plus a file) come back as hand-set noise instead of as their block, so the
config no longer says *why* the machine is that way.

Asserted per feature, end to end through the real registry: a machine carrying
it captures the declaration, a machine without it captures nothing (rather than
a false one), and re-planning the captured config proposes no change.
"""
import json

from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.action_registry import get_default_registry
from dasik.lib.actions.actions_handler_v2 import setup_actions
from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction
from dasik.lib.expand import expand_config, subtract_contributions
from dasik.lib.models.json_model import JsonModel
from dasik.lib.reconciler.reconciler import Reconciler
from dasik.lib.target.target import Target

_ENTRY = ("root=/dev/mapper/cryptroot rw amd_pstate=active "
          "sysrq_always_enabled=1 resume=/dev/mapper/cryptswap quiet")


def _machine(tmp_path, entry=_ENTRY, reflector=True, governor=True, sudoers=True):
    """A fake target root carrying (some of) the block-A features."""
    (tmp_path / "boot/loader/entries").mkdir(parents=True)
    (tmp_path / "boot/loader/loader.conf").write_text("default arch\n")
    (tmp_path / "boot/loader/entries/arch.conf").write_text(
        f"title Arch\noptions {entry}\n")
    if reflector:
        (tmp_path / "etc/xdg/reflector").mkdir(parents=True)
        (tmp_path / "etc/xdg/reflector/reflector.conf").write_text(
            "# Managed by dasik\n--country ES\n--protocol https\n"
            "--latest 20\n--sort rate\n--save /etc/pacman.d/mirrorlist\n")
    if governor:
        (tmp_path / "etc/default").mkdir(parents=True, exist_ok=True)
        (tmp_path / "etc/default/cpupower").write_text('governor="performance"\n')
    if sudoers:
        (tmp_path / "etc/sudoers.d").mkdir(parents=True, exist_ok=True)
        (tmp_path / "etc/sudoers.d/10-dasik").write_text(
            "# Managed by dasik\n%wheel ALL=(ALL:ALL) ALL\n")
    return tmp_path


def _synced(tmp_path, seed=None):
    """What `dasik sync` would write, for a target rooted at *tmp_path*.

    Mirrors _cmd_sync: reconcile, subtract what the toggles contribute, drop
    newly-added empty keys. Per-action failures (no arch-chroot for a fake
    root) are isolated by the Reconciler, exactly as in a real run.
    """
    seed = dict(seed or {"bootloader": "sd-boot"})
    setup_actions()
    target = Target(root=str(tmp_path))
    reconciler = Reconciler(config=seed, target=target, manifest=None,
                            action_metas=get_default_registry().get_all_actions())
    new_config, _manifest = reconciler.sync()
    new_config = subtract_contributions(new_config, seed)
    return {k: v for k, v in new_config.items() if k in seed or v}


# --- captured on a machine that has the feature ---------------------------- #

@pytest.mark.parametrize("key,expected", [
    ("sysrq", True),
    ("cpu", {"scaling_driver": "amd_pstate", "mode": "active",
             "power_profiles_daemon": True, "governor": "performance"}),
    ("reflector", {"countries": ["ES"], "protocols": ["https"], "latest": 20,
                   "sort": "rate", "save": "/etc/pacman.d/mirrorlist"}),
    ("sudo", {"wheel": True, "nopasswd": False, "rules": []}),
])
def test_sync_captures_the_feature(tmp_path, key, expected):
    captured = _synced(_machine(tmp_path))

    assert captured.get(key) == expected


def test_sync_leaves_the_derived_parameters_out_of_kernel_cmdline(tmp_path):
    """Captured as blocks, not as hand-set parameters — otherwise the same
    policy is declared twice and `cpu`/`sysrq` never appear."""
    captured = _synced(_machine(tmp_path))

    assert "amd_pstate=active" not in captured["kernel_cmdline"]
    assert "sysrq_always_enabled=1" not in captured["kernel_cmdline"]
    # …while what somebody really set by hand survives.
    assert "resume=/dev/mapper/cryptswap" in captured["kernel_cmdline"]
    assert "quiet" in captured["kernel_cmdline"]


# --- NOT invented on a machine that lacks it ------------------------------- #

def test_sync_does_not_invent_features_the_machine_lacks(tmp_path):
    bare = _machine(tmp_path, entry="root=LABEL=root rw quiet",
                    reflector=False, governor=False, sudoers=False)

    captured = _synced(bare)

    assert captured.get("sysrq") is None      # flag cleared, dropped as empty
    assert "cpu" not in captured
    assert "reflector" not in captured
    assert "sudo" not in captured


def test_sync_clears_a_flag_the_machine_does_not_carry(tmp_path):
    """A seed that declares sysrq against a machine without it captures False —
    sync reports reality, it does not preserve the declaration."""
    bare = _machine(tmp_path, entry="root=LABEL=root rw quiet",
                    reflector=False, governor=False, sudoers=False)

    captured = _synced(bare, seed={"bootloader": "sd-boot", "sysrq": True})

    assert captured["sysrq"] is False


# --- the captured config is usable ----------------------------------------- #

def test_the_captured_config_validates(tmp_path):
    JsonModel.model_validate(_synced(_machine(tmp_path)))


def test_the_captured_config_is_json_serializable(tmp_path):
    json.dumps(_synced(_machine(tmp_path)))


def test_replanning_the_captured_config_is_a_no_op(tmp_path):
    """sync → plan must be silent: capturing a machine and re-planning it is
    the round trip that proves the block reproduces the parameter it came
    from."""
    machine = _machine(tmp_path)
    captured = _synced(machine)

    action = KernelCmdlineAction(captured, ActionContext(target=Target(root=str(machine))))

    assert action.plan(managed=[]) == []


def test_the_units_a_feature_brings_are_reproducible_from_the_capture(tmp_path):
    """reflector.timer / power-profiles-daemon.service / systemd-boot-update
    ride the systemd domain. What matters is that RE-APPLYING the capture
    enables them again — some arrive listed in `systemd`, and some (here
    systemd-boot-update, which `bootloader: sd-boot` derives) are deliberately
    subtracted from the list because the block re-derives them."""
    enabled = MagicMock(returncode=0, stdout=(
        b"power-profiles-daemon.service enabled\n"
        b"reflector.timer enabled\n"
        b"systemd-boot-update.service enabled\n"))
    with patch("dasik.lib.actions.systemd_action.Command.execute", return_value=enabled):
        captured = _synced(_machine(tmp_path))

    reapplied = expand_config(captured)["systemd"]["enable_units"]

    assert set(reapplied) >= {"power-profiles-daemon.service", "reflector.timer",
                              "systemd-boot-update.service"}

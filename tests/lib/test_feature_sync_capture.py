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
from dasik.lib.actions.bootloader_action import BootloaderAction
from dasik.lib.actions.actions_handler_v2 import setup_actions
from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction
from dasik.lib.actions.systemd_conf_action import OomdAction
from dasik.lib.expand import expand_config, subtract_contributions
from dasik.lib.models.json_model import JsonModel
from dasik.lib.reconciler.reconciler import Reconciler
from dasik.lib.target.target import Target

_ENTRY = ("root=/dev/mapper/cryptroot rw amd_pstate=active "
          "sysrq_always_enabled=1 resume=/dev/mapper/cryptswap quiet splash")


def _machine(tmp_path, entry=_ENTRY, reflector=True, governor=True, sudoers=True,
             plymouth=True):
    """A fake target root carrying (some of) the block-A/B features."""
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
    if plymouth:
        (tmp_path / "usr/bin").mkdir(parents=True, exist_ok=True)
        (tmp_path / "usr/bin/plymouthd").write_text("")
        (tmp_path / "etc/plymouth").mkdir(parents=True, exist_ok=True)
        (tmp_path / "etc/plymouth/plymouthd.conf").write_text(
            "# Managed by dasik\n[Daemon]\nTheme=bgrt\n")
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
    ("plymouth", {"theme": "bgrt"}),
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
    # `splash` too: the machine has plymouth, so the block owns the parameter.
    assert "splash" not in captured["kernel_cmdline"]
    # …while what somebody really set by hand survives.
    assert "resume=/dev/mapper/cryptswap" in captured["kernel_cmdline"]
    assert "quiet" in captured["kernel_cmdline"]


# --- NOT invented on a machine that lacks it ------------------------------- #

def test_sync_does_not_invent_features_the_machine_lacks(tmp_path):
    bare = _machine(tmp_path, entry="root=LABEL=root rw quiet",
                    reflector=False, governor=False, sudoers=False, plymouth=False)

    captured = _synced(bare)

    assert captured.get("sysrq") is None      # flag cleared, dropped as empty
    assert "cpu" not in captured
    assert "reflector" not in captured
    assert "sudo" not in captured
    assert "plymouth" not in captured


def test_a_splash_nobody_owns_survives_as_a_plain_parameter(tmp_path):
    """No plymouth on the machine ⇒ `splash` is somebody else's parameter, and
    a capture that swallowed it would silently drop it on the next apply."""
    bare = _machine(tmp_path, entry="root=LABEL=root rw splash",
                    reflector=False, governor=False, sudoers=False, plymouth=False)

    captured = _synced(bare)

    assert "splash" in captured["kernel_cmdline"]
    assert "plymouth" not in captured


def test_sync_clears_a_flag_the_machine_does_not_carry(tmp_path):
    """A seed that declares sysrq against a machine without it captures False —
    sync reports reality, it does not preserve the declaration."""
    bare = _machine(tmp_path, entry="root=LABEL=root rw quiet",
                    reflector=False, governor=False, sudoers=False, plymouth=False)

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


# --- pendrive LUKS keyfile ------------------------------------------------- #
#
# The end-to-end `_synced` harness cannot reach this one: the capture keys off
# the volume's real LUKS UUID, which comes from `cryptsetup` on a live machine.
# So the machine is faked at that boundary instead — everything above it (the
# parsing, the subtraction, the re-plan) is the real code.

_DISKS_SEED = {"disks": [{
    "device": "/dev/vda", "partition_table": "gpt", "wipe_disk": True,
    "partitions": [
        {"label": "boot", "size": "512MiB", "filesystem": "fat32",
         "partition_type": "esp", "mountpoint": "/boot"},
        {"label": "root", "size": "rest", "filesystem": "ext4", "mountpoint": "/",
         "encrypt": True, "luks_name": "cryptroot", "luks_password": "pw"},
    ]}]}


def _captured_partition(cmdline, fstype=b"vfat\n"):
    from dasik.lib.actions.disk_partition_action import DiskPartitionAction

    def fake(cmd, args=None, *_rest, **_kw):
        if cmd == "lsblk":
            return MagicMock(stdout=fstype, returncode=0)
        if cmd == "cryptsetup" and args and args[0] == "status":
            return MagicMock(stdout=b"  device:  /dev/vda2\n", returncode=0)
        if cmd == "cryptsetup" and args and args[0] == "luksUUID":
            return MagicMock(stdout=b"THEUUID\n", returncode=0)
        return MagicMock(stdout=b"", returncode=0)

    action = DiskPartitionAction(_DISKS_SEED, ActionContext(target=Target(root="/")))
    with patch("dasik.lib.actions.disk_partition_action.Command.execute", side_effect=fake), \
         patch.object(DiskPartitionAction, "_kernel_cmdline_text", return_value=cmdline):
        frag = action.import_state(managed=[])
    return frag["disks"]["disks"][0]["partitions"][1]


def test_sync_captures_the_pendrive_unlock():
    part = _captured_partition("rd.luks.name=THEUUID=cryptroot "
                               "rd.luks.key=THEUUID=/keyfile:UUID=1234-ABCD "
                               "rd.luks.options=THEUUID=keyfile-timeout=10s")

    assert part["unlock_keyfile"] == "/keyfile"
    assert part["unlock_keydev"] == "UUID=1234-ABCD"
    assert part["unlock_keydev_fs"] == "vfat"
    # …and the timeout dasik re-derives is not ALSO captured as an option.
    assert part.get("luks_options", []) == []


def test_sync_does_not_invent_a_pendrive_unlock():
    part = _captured_partition("rd.luks.name=THEUUID=cryptroot root=/dev/mapper/cryptroot")

    assert part.get("unlock_keyfile") is None
    assert part.get("unlock_keydev") is None


def test_the_captured_pendrive_config_replans_to_nothing(tmp_path):
    """sync → plan must be silent: the captured fields have to re-derive the
    very parameters they were read from, `UUID=` prefix and timeout included."""
    part = _captured_partition("rd.luks.key=THEUUID=/keyfile:UUID=1234-ABCD "
                               "rd.luks.options=THEUUID=keyfile-timeout=10s")
    captured = {"bootloader": "sd-boot", "disks": {"disks": [
        {"device": "/dev/vda", "partitions": [part]}]}}
    JsonModel.model_validate(captured)          # the capture is a valid config

    # The capture also bakes in the volume's real header UUID, so the re-derived
    # parameters key off THAT, not the deterministic fallback.
    uuid = part["luks_uuid"]
    (tmp_path / "boot/loader/entries").mkdir(parents=True)
    (tmp_path / "boot/loader/loader.conf").write_text("default arch\n")
    (tmp_path / "boot/loader/entries/arch.conf").write_text(
        "title Arch\noptions "
        f"rd.luks.name={uuid}=cryptroot root=/dev/mapper/cryptroot rw "
        f"rd.luks.key={uuid}=/keyfile:UUID=1234-ABCD "
        f"rd.luks.options={uuid}=keyfile-timeout=10s\n")
    action = KernelCmdlineAction(expand_config(captured),
                                 ActionContext(target=Target(root=str(tmp_path))))

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


# --- root password --------------------------------------------------------- #

def _with_shadow(tmp_path, root_field):
    """A machine whose /etc/shadow gives root *root_field*."""
    machine = _machine(tmp_path)
    etc = machine / "etc"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "passwd").write_text("root:x:0:0::/root:/bin/bash\n")
    (etc / "group").write_text("wheel:x:998:\n")
    (etc / "shadow").write_text(f"root:{root_field}:19000:0:99999:7:::\n")
    return machine


def test_sync_captures_the_root_password(tmp_path):
    captured = _synced(_with_shadow(tmp_path, "$6$SET$h"))

    assert captured["users"] == [{"username": "root", "hashed_password": "$6$SET$h"}]


def test_sync_does_not_invent_a_root_password(tmp_path):
    """A locked root is not a password: capturing `!` would both describe a
    login the machine does not offer and fail the model's hash validator."""
    captured = _synced(_with_shadow(tmp_path, "!"))

    assert not captured.get("users")


def test_sync_clears_a_declared_root_password_the_machine_lacks(tmp_path):
    seed = {"bootloader": "sd-boot",
            "users": [{"username": "root", "hashed_password": "$6$GONE$h"}]}
    captured = _synced(_with_shadow(tmp_path, "!"), seed=seed)

    assert captured["users"] == []


def test_the_captured_root_password_config_validates(tmp_path):
    JsonModel.model_validate(_synced(_with_shadow(tmp_path, "$6$SET$h")))


# --- bootloader ------------------------------------------------------------ #

def _mark(machine, loader):
    if loader == "sd-boot":
        d = machine / "boot/EFI/systemd"
        d.mkdir(parents=True, exist_ok=True)
        (d / "systemd-bootx64.efi").write_text("")
    else:
        d = machine / "boot/grub"
        d.mkdir(parents=True, exist_ok=True)
        (d / "grub.cfg").write_text("")


def test_sync_captures_the_installed_bootloader_over_the_seed(tmp_path):
    machine = _machine(tmp_path)
    _mark(machine, "grub")

    assert _synced(machine, seed={"bootloader": "sd-boot"})["bootloader"] == "grub"


def test_replanning_the_captured_bootloader_is_a_no_op(tmp_path):
    """The round trip that matters for a switch: capture a grub machine and the
    plan must not propose reinstalling — or removing — anything."""
    machine = _machine(tmp_path)
    _mark(machine, "grub")
    captured = _synced(machine, seed={"bootloader": "sd-boot"})

    action = BootloaderAction(captured, ActionContext(target=Target(root=str(machine))))

    assert action.plan(managed=[]) == []


def test_a_machine_carrying_two_loaders_still_plans_the_cleanup_after_sync(tmp_path):
    """Not a round-trip violation: a leftover loader IS divergence, so the plan
    staying loud about it is the point — silence would hide it forever."""
    machine = _machine(tmp_path)
    _mark(machine, "grub")
    _mark(machine, "sd-boot")
    captured = _synced(machine, seed={"bootloader": "grub"})

    action = BootloaderAction(captured, ActionContext(target=Target(root=str(machine))))
    ops = {(c.op.name, c.item) for c in action.plan(managed=[])}

    assert ("REMOVE", "grub") in ops        # captured sd-boot, so grub is stale


# --- /etc/systemd/*.conf (oomd, system, user) ------------------------------ #
#
# The reason these needed an owner at all: they are pacman BACKUP files, so
# DropFilesAction's discovery skips them, and /etc/systemd is not one of its
# sections either. Nothing read them back.

_STOCK_OOMD = "[OOM]\n#SwapUsedLimit=90%\n#DefaultMemoryPressureDurationSec=30s\n"


def _with_oomd(tmp_path, text):
    machine = _machine(tmp_path)
    conf = machine / "etc/systemd/oomd.conf"
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text(text)
    return machine


def test_sync_captures_a_setting_from_the_pacman_owned_file(tmp_path):
    captured = _synced(_with_oomd(
        tmp_path, "[OOM]\nDefaultMemoryPressureDurationSec=20s\n"))

    assert captured["oomd"] == {"DefaultMemoryPressureDurationSec": "20s"}


def test_sync_does_not_invent_settings_from_a_stock_file(tmp_path):
    """Commented-out defaults are documentation, not configuration."""
    captured = _synced(_with_oomd(tmp_path, _STOCK_OOMD))

    assert "oomd" not in captured
    assert "systemd_system_conf" not in captured
    assert "systemd_user_conf" not in captured


def test_the_captured_oomd_config_validates(tmp_path):
    JsonModel.model_validate(_synced(_with_oomd(
        tmp_path, "[OOM]\nDefaultMemoryPressureDurationSec=20s\n")))


def test_replanning_the_captured_oomd_config_is_a_no_op(tmp_path):
    machine = _with_oomd(tmp_path, "[OOM]\nDefaultMemoryPressureDurationSec=20s\n")
    captured = _synced(machine)

    action = OomdAction(captured, ActionContext(target=Target(root=str(machine))))

    assert action.plan(managed=[]) == []

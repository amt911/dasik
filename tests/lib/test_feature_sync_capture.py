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
             plymouth=True, libvirt_autostart=True):
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
    if libvirt_autostart:
        networks = tmp_path / "etc/libvirt/qemu/networks"
        (networks / "autostart").mkdir(parents=True, exist_ok=True)
        (networks / "default.xml").write_text("<network><name>default</name></network>\n")
        # Absolute, the way virsh writes it — so inside this scratch root it
        # dangles, and only `islink` sees it.
        (networks / "autostart/default.xml").symlink_to(
            "/etc/libvirt/qemu/networks/default.xml")
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
    ("kvm", {"default_network": True}),
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
                    reflector=False, governor=False, sudoers=False, plymouth=False,
                    libvirt_autostart=False)

    captured = _synced(bare)

    assert captured.get("sysrq") is None      # flag cleared, dropped as empty
    assert "cpu" not in captured
    assert "reflector" not in captured
    assert "sudo" not in captured
    assert "plymouth" not in captured
    assert "kvm" not in captured


def test_a_splash_nobody_owns_survives_as_a_plain_parameter(tmp_path):
    """No plymouth on the machine ⇒ `splash` is somebody else's parameter, and
    a capture that swallowed it would silently drop it on the next apply."""
    bare = _machine(tmp_path, entry="root=LABEL=root rw splash",
                    reflector=False, governor=False, sudoers=False, plymouth=False,
                    libvirt_autostart=False)

    captured = _synced(bare)

    assert "splash" in captured["kernel_cmdline"]
    assert "plymouth" not in captured


def test_sync_clears_a_flag_the_machine_does_not_carry(tmp_path):
    """A seed that declares sysrq against a machine without it captures False —
    sync reports reality, it does not preserve the declaration."""
    bare = _machine(tmp_path, entry="root=LABEL=root rw quiet",
                    reflector=False, governor=False, sudoers=False, plymouth=False,
                    libvirt_autostart=False)

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


def test_sync_clears_a_declared_setting_the_machine_does_not_have(tmp_path):
    """A declaration is not evidence — sync reports the machine.

    Otherwise a hand-removed drop-in stays in the captured config forever, and
    re-applying it looks like a no-op when it is really a change.
    """
    captured = _synced(_with_oomd(tmp_path, _STOCK_OOMD),
                       seed={"bootloader": "sd-boot",
                             "oomd": {"SwapUsedLimit": "90%"}})

    assert captured["oomd"] == {}


def test_replanning_the_captured_oomd_config_is_a_no_op(tmp_path):
    machine = _with_oomd(tmp_path, "[OOM]\nDefaultMemoryPressureDurationSec=20s\n")
    captured = _synced(machine)

    action = OomdAction(captured, ActionContext(target=Target(root=str(machine))))

    assert action.plan(managed=[]) == []


# --- encrypted swap (random key) ------------------------------------------- #
#
# To lsblk the partition is a 1 MiB ext2 filesystem — a type dasik cannot
# represent, so discovery used to skip it and the captured layout came back with
# no swap at all. /etc/crypttab is the only thing that says otherwise.

_SWAP_CRYPTTAB = ("swap LABEL=cryptswap /dev/urandom "
                  "swap,offset=2048,cipher=aes-xts-plain64,size=512,sector-size=4096\n")
_SWAP_FSTAB = ("UUID=abc / ext4 defaults 0 0\n"
               "/dev/mapper/swap none swap defaults 0 0\n")


def _swap_tree():
    return [{"name": "vda", "path": "/dev/vda", "type": "disk", "pttype": "gpt",
             "children": [
                 {"name": "vda1", "path": "/dev/vda1", "type": "part",
                  "fstype": "vfat", "size": 512 * 1024**2,
                  "parttypename": "EFI System", "mountpoint": "/boot"},
                 {"name": "vda2", "path": "/dev/vda2", "type": "part",
                  "fstype": "ext2", "label": "cryptswap", "size": 2 * 1024**3,
                  "parttypename": "Linux swap"},
                 {"name": "vda3", "path": "/dev/vda3", "type": "part",
                  "fstype": "ext4", "size": 20 * 1024**3,
                  "parttypename": "Linux filesystem", "mountpoint": "/"},
             ]}]


def _swap_machine(tmp_path, crypttab=_SWAP_CRYPTTAB):
    machine = _machine(tmp_path)
    (machine / "etc").mkdir(parents=True, exist_ok=True)
    (machine / "etc/crypttab").write_text(crypttab)
    (machine / "etc/fstab").write_text(_SWAP_FSTAB)
    return machine


def _synced_with_disks(tmp_path, crypttab=_SWAP_CRYPTTAB):
    from dasik.lib.actions.disk_partition_action import DiskPartitionAction
    with patch.object(DiskPartitionAction, "_lsblk_tree", return_value=_swap_tree()), \
         patch.object(DiskPartitionAction, "_findmnt_btrfs_rows", return_value=[]):
        return _synced(_swap_machine(tmp_path, crypttab))


def _captured_swap(captured):
    parts = captured["disks"]["disks"][0]["partitions"]
    return next((p for p in parts if p.get("swap_encryption") == "random"), None)


def test_sync_captures_the_random_swap_partition(tmp_path):
    swap = _captured_swap(_synced_with_disks(tmp_path))

    assert swap is not None
    assert swap["filesystem"] == "swap"
    assert swap["label"] == "swap"          # the mapper name, not "cryptswap"


def test_sync_invents_no_swap_on_a_machine_without_one(tmp_path):
    captured = _synced_with_disks(tmp_path, crypttab="")

    assert _captured_swap(captured) is None


def test_the_captured_swap_config_validates(tmp_path):
    captured = _synced_with_disks(tmp_path)

    JsonModel(**captured)          # what `dasik check` does


def test_replanning_the_captured_swap_config_is_a_no_op(tmp_path):
    from dasik.lib.actions.encrypted_swap_action import EncryptedSwapAction

    captured = _synced_with_disks(tmp_path)
    action = EncryptedSwapAction(expand_config(captured),
                                 ActionContext(target=Target(root=str(tmp_path))))

    assert action.plan(managed=["swap"]) == []


# --- apparmor -------------------------------------------------------------- #
#
# The mirror: a machine running AppArmor must capture the block, not the bare
# `lsm=` parameter. Capturing the parameter alone would produce a config that
# re-applies the same policy while never installing AppArmor — the parameter
# names a module that is not there.

_LSM = "lsm=landlock,lockdown,yama,integrity,apparmor,bpf"


def _with_apparmor(tmp_path, params=f"{_ENTRY} {_LSM}", installed=True, auditd=False,
                   profiles=()):
    machine = _machine(tmp_path)
    (machine / "boot/loader/entries/arch.conf").write_text(
        f"title Arch\noptions {params}\n")
    binaries = machine / "usr/bin"
    binaries.mkdir(parents=True, exist_ok=True)
    if installed:
        (binaries / "apparmor_parser").write_text("")
    if auditd:
        (binaries / "auditd").write_text("")
    profile_dir = machine / "etc/apparmor.d"
    profile_dir.mkdir(parents=True, exist_ok=True)
    for name, content in profiles:
        (profile_dir / name).write_text(content)
    return machine


def test_sync_captures_the_apparmor_block(tmp_path):
    captured = _synced(_with_apparmor(tmp_path))

    assert captured["apparmor"] == {"enable": True, "audit": False,
                                    "desktop_notifications": False}


def test_sync_invents_no_apparmor_on_a_machine_without_it(tmp_path):
    captured = _synced(_with_apparmor(tmp_path, installed=False))

    assert "apparmor" not in captured


def test_sync_captures_the_audit_mode(tmp_path):
    captured = _synced(_with_apparmor(tmp_path, params=f"{_ENTRY} {_LSM} audit=1",
                                      auditd=True))

    assert captured["apparmor"]["audit"] is True


def test_sync_captures_a_local_profile(tmp_path):
    captured = _synced(_with_apparmor(
        tmp_path, profiles=[("usr.bin.foo", "profile foo {}\n")]))

    assert captured["apparmor"]["extra_profiles"] == [
        {"name": "usr.bin.foo", "content": "profile foo {}\n"}]


def test_sync_leaves_the_lsm_parameter_out_of_kernel_cmdline(tmp_path):
    """Captured as the block, not as a hand-set parameter — otherwise the same
    policy is declared twice and `apparmor` never appears."""
    captured = _synced(_with_apparmor(tmp_path))

    assert not [p for p in captured["kernel_cmdline"] if p.startswith("lsm=")]


def test_the_captured_apparmor_config_validates(tmp_path):
    JsonModel(**_synced(_with_apparmor(tmp_path)))


def test_replanning_the_captured_apparmor_config_is_a_no_op(tmp_path):
    machine = _with_apparmor(tmp_path)
    captured = _synced(machine)
    action = KernelCmdlineAction(expand_config(captured),
                                 ActionContext(target=Target(root=str(machine))))

    assert [c for c in action.plan(managed=[]) if "lsm" in c.item] == []


# --- pam ------------------------------------------------------------------- #

def _pam_machine(tmp_path, pam_config):
    """A machine that has had `pam_config` applied to it."""
    from dasik.lib.actions.pam_action import PamAction
    machine = _machine(tmp_path)
    (machine / "etc/security").mkdir(parents=True, exist_ok=True)
    (machine / "etc/pam.d").mkdir(parents=True, exist_ok=True)
    (machine / "etc/security/faillock.conf").write_text("# deny = 3\n")
    (machine / "etc/pam.d/passwd").write_text(
        "#%PAM-1.0\npassword\tinclude\t\tsystem-auth\n")
    action = PamAction({"pam": pam_config},
                       ActionContext(target=Target(root=str(machine))))
    action.apply(action.plan(managed=[]))
    return machine


def test_sync_captures_the_pam_policy(tmp_path):
    captured = _synced(_pam_machine(tmp_path, {"faillock": {"deny": 4}}))

    assert captured["pam"]["faillock"]["deny"] == 4
    assert captured["pam"]["faillock"]["persistent"] is True


def test_sync_invents_no_pam_policy_on_a_stock_machine(tmp_path):
    captured = _synced(_machine(tmp_path))

    assert "pam" not in captured


def test_the_captured_pam_config_validates(tmp_path):
    JsonModel(**_synced(_pam_machine(tmp_path, {"faillock": {}, "limits": {}})))


def test_replanning_the_captured_pam_config_is_a_no_op(tmp_path):
    from dasik.lib.actions.pam_action import PamAction

    machine = _pam_machine(tmp_path, {"faillock": {"deny": 4}, "limits": {}})
    captured = _synced(machine)
    action = PamAction(expand_config(captured),
                       ActionContext(target=Target(root=str(machine))))

    assert action.plan(managed=["faillock", "limits"]) == []


def test_the_package_behind_the_policy_is_reproducible_from_the_capture(tmp_path):
    """`subtract_contributions` strips libpwquality as a toggle contribution;
    what must survive is the declaration that re-derives it."""
    captured = _synced(_pam_machine(tmp_path, {"pwquality": {"minlen": 12}}))

    assert captured["pam"]["pwquality"]["minlen"] == 12
    assert "libpwquality" in expand_config(captured)["packages"]


# --- the network manager (issue #196) --------------------------------------- #

def test_the_capture_of_a_hostname_only_machine_validates(tmp_path):
    """A config with a `hostname` and no `network` block is valid, and so must
    its capture be. It used to come back as `network: {"type": ""}` — which the
    schema rejects, so `sync` produced a file dasik itself refused."""
    machine = _machine(tmp_path)
    (machine / "etc").mkdir(parents=True, exist_ok=True)
    (machine / "etc/hostname").write_text("arch\n")

    captured = _synced(machine, seed={"bootloader": "sd-boot", "hostname": "arch"})

    assert captured["hostname"] == "arch"
    assert "network" not in captured
    JsonModel.model_validate(captured)


# --- a PKGBUILD that was never uploaded to the AUR ------------------------- #
#
# The end-to-end `_synced` harness cannot reach this one either: PackagesAction
# reads reality with `pacman -Qq…`, which a fake root has no way to answer, so
# the Reconciler isolates it and the fragment never appears. The pacman boundary
# is faked here; the capture logic above it is the real code.

_GIT_SRC = {"type": "pkgbuild-git",
            "url": "https://git.example.org/pkgbuilds/config-saver.git",
            "ref": "a520605367e13ec25db4c3c7e1c4bf46175ba8cd", "subdir": "."}


def _packages_capture(seed, manifest, installed=("config-saver",)):
    from dasik.lib.actions.packages_action import PackagesAction

    action = PackagesAction(seed, ActionContext(target=Target(root="/"),
                                                manifest=manifest))
    action._installed_all = MagicMock(return_value=set(installed))
    # Same reason, for the reason probe: `plan` asks `_explicit_raw()`
    # (`pacman -Qqe`, raw) whether a declared package is explicit, and an
    # unstubbed one asks the machine running the tests — which has no
    # pacman at all on CI.
    action._explicit_raw = MagicMock(return_value=set(installed))
    action.actual = MagicMock(return_value=set(installed))
    action._unit_provider_packages = MagicMock(return_value=set())
    return action.import_state(list(installed))


_GIT_MANIFEST = {"managed": {"packages": ["config-saver"]},
                 "action_state": {"packages": {"sources": {"config-saver": _GIT_SRC}}}}


def test_sync_captures_the_git_source_of_a_package_no_repo_has():
    captured = _packages_capture({}, _GIT_MANIFEST)

    assert captured["package_sources"] == {"config-saver": _GIT_SRC}


def test_sync_invents_no_source_on_a_machine_that_has_no_git_package():
    assert "package_sources" not in _packages_capture(
        {"packages": ["git"]}, None, installed=("git",))


def test_the_captured_git_source_validates_and_re_plans_to_nothing():
    captured = _packages_capture({}, _GIT_MANIFEST)
    config = {
        "locales": {"selected_locales": [], "desired_locale": "en_US.UTF-8",
                    "desired_tty_layout": "us"},
        "timezone": {"region": "Europe", "city": "Madrid"},
        "network": {"type": "NetworkManager", "add_default_hosts": True},
        "hostname": "arch", **captured,
    }
    JsonModel(**config)          # `dasik check` on the capture

    from dasik.lib.actions.packages_action import PackagesAction
    replan = PackagesAction(config, ActionContext(target=Target(root="/"),
                                                  manifest=_GIT_MANIFEST))
    replan._installed_all = MagicMock(return_value={"config-saver"})
    replan._explicit_raw = MagicMock(return_value={"config-saver"})
    replan.actual = MagicMock(return_value={"config-saver"})
    assert replan.plan(managed=["config-saver"]) == []


# --- containers (the runtime) ---------------------------------------------- #
#
# Reached through the real registry: the probes are files under the target root
# and one `systemctl is-enabled`, so only that command needs faking.

def _container_machine(tmp_path, runtime="podman", subuid="andres:100000:65536\n"):
    machine = _machine(tmp_path)
    (machine / "usr/bin").mkdir(parents=True, exist_ok=True)
    (machine / "usr/bin" / ("podman" if runtime == "podman" else "dockerd")).write_text("")
    (machine / "etc").mkdir(parents=True, exist_ok=True)
    (machine / "etc/subuid").write_text(subuid)
    (machine / "etc/subgid").write_text(subuid)
    return machine


def _synced_containers(tmp_path, **kw):
    from dasik.lib.actions.containers_action import ContainersAction

    machine = _container_machine(tmp_path, **kw)
    with patch.object(ContainersAction, "_unit_enabled", return_value=False):
        return _synced(machine)


def test_sync_captures_the_container_runtime(tmp_path):
    captured = _synced_containers(tmp_path)

    assert captured["containers"]["runtime"] == "podman"
    assert captured["containers"]["rootless"] is True


def test_sync_invents_no_runtime_on_a_machine_without_one(tmp_path):
    assert "containers" not in _synced(_machine(tmp_path))


def test_the_captured_runtime_validates(tmp_path):
    JsonModel.model_validate(_synced_containers(tmp_path))


def test_replanning_the_captured_runtime_is_a_no_op(tmp_path):
    from dasik.lib.actions.containers_action import ContainersAction

    machine = _container_machine(tmp_path)
    with patch.object(ContainersAction, "_unit_enabled", return_value=False):
        captured = _synced(machine)
    captured.setdefault("users", [{"username": "andres", "hashed_password": "$6$a$b"}])
    action = ContainersAction(expand_config(captured),
                              ActionContext(target=Target(root=str(machine))))

    assert action.plan(managed=["andres"]) == []


def test_the_package_behind_the_runtime_is_reproducible_from_the_capture(tmp_path):
    captured = _synced_containers(tmp_path)

    assert "podman" in expand_config(captured)["packages"]


# --- config-saver ----------------------------------------------------------- #

def _saver_machine(tmp_path):
    machine = _machine(tmp_path)
    (machine / "usr/bin").mkdir(parents=True, exist_ok=True)
    (machine / "usr/bin/config-saver").write_text("")
    (machine / "etc/config-saver/configs").mkdir(parents=True)
    (machine / "etc/config-saver/configs/dotfiles.json").write_text(
        '{"directories": ["$HOME/.config"]}')
    return machine


def _synced_saver(tmp_path, seed=None):
    from dasik.lib.actions.config_saver_action import ConfigSaverAction

    with patch.object(ConfigSaverAction, "_pkg_owned", return_value=False), \
         patch.object(ConfigSaverAction, "_unit_enabled", return_value=False):
        return _synced(_saver_machine(tmp_path), seed=seed)


def test_sync_captures_the_config_saver_documents(tmp_path):
    captured = _synced_saver(tmp_path)

    assert captured["config_saver"]["configs"] == {
        "dotfiles": {"directories": ["$HOME/.config"]}}


def test_sync_invents_no_config_saver_block(tmp_path):
    assert "config_saver" not in _synced(_machine(tmp_path))


def test_the_captured_config_saver_block_validates(tmp_path):
    JsonModel.model_validate(_synced_saver(tmp_path))


def test_the_captured_document_is_not_also_a_hand_written_file(tmp_path):
    """It rides `files`, so subtract_contributions must attribute it to the
    block — or the capture carries the same JSON twice."""
    captured = _synced_saver(tmp_path)
    paths = [f["path"] for f in captured.get("files", [])]

    assert "/etc/config-saver/configs/dotfiles.json" not in paths
    assert "/etc/config-saver/configs/dotfiles.json" in \
        [f["path"] for f in expand_config(captured)["files"]]


# --- wireguard tunnels (issue #249) ---------------------------------------- #

_WGQ_BODY = "[Interface]\nAddress = 10.0.0.2/24\nPrivateKey = SECRET\n"
_NMC_BODY = ("[connection]\nid=work\ntype=wireguard\n\n"
             "[wireguard]\nprivate-key=SECRET\n")


def _machine_with_tunnels(tmp_path, wg_quick=True, nm=True):
    root = _machine(tmp_path)
    if wg_quick:
        (root / "etc/wireguard").mkdir(parents=True, exist_ok=True)
        (root / "etc/wireguard/eu-mad.conf").write_text(_WGQ_BODY)
    if nm:
        d = root / "etc/NetworkManager/system-connections"
        d.mkdir(parents=True, exist_ok=True)
        (d / "work.nmconnection").write_text(_NMC_BODY)
        (d / "MyWifi.nmconnection").write_text(
            "[connection]\nid=MyWifi\ntype=wifi\n")
    return root


def test_sync_captures_a_tunnel_as_its_own_block(tmp_path):
    captured = _synced(_machine_with_tunnels(tmp_path, nm=False))

    assert captured["wireguard"] == [{
        "name": "eu-mad", "source": "wg/eu-mad.conf", "backend": "wg-quick",
        "enable": False, "content": _WGQ_BODY}]


def test_sync_captures_both_backends_and_ignores_other_nm_connections(tmp_path):
    captured = _synced(_machine_with_tunnels(tmp_path))

    assert [(t["name"], t["backend"]) for t in captured["wireguard"]] == [
        ("eu-mad", "wg-quick"), ("work", "networkmanager")]


def test_a_machine_without_a_tunnel_invents_none(tmp_path):
    assert "wireguard" not in _synced(_machine(tmp_path))


def test_the_tunnel_is_captured_once_not_twice(tmp_path):
    """The defect this ownership move fixes: while DropFilesAction discovered
    /etc/wireguard too, the same private key came back as the block AND as a
    `files` entry — and the orphan entry kept writing the tunnel after the
    block was turned off."""
    captured = _synced(_machine_with_tunnels(tmp_path))

    paths = [f["path"] for f in captured.get("files", [])]
    assert not [p for p in paths
                if p.startswith("/etc/wireguard/")
                or p.endswith("work.nmconnection")]


def test_the_captured_tunnel_validates_and_replans_to_nothing(tmp_path):
    """sync -> check -> plan, the invariant that matters: the capture has to be
    a config dasik accepts, and one that proposes no further change."""
    from dasik.lib.actions.drop_files_action import DropFilesAction
    from dasik.lib.json_parser.wireguard_extract import extract_to_wireguard_dir

    root = _machine_with_tunnels(tmp_path)
    captured = _synced(root)

    # `check`: the capture, with bodies extracted to files as sync writes them,
    # is a config the schema accepts.
    extracted = extract_to_wireguard_dir(captured, tmp_path / "repo")
    JsonModel.model_validate(extracted.config)
    assert set(extracted.writes) == {
        tmp_path / "repo/wg/eu-mad.conf", tmp_path / "repo/wg/work.nmconnection"}

    # `plan` on the captured config, against the machine it came from: silent.
    (root / "etc/wireguard/eu-mad.conf").chmod(0o600)
    (root / "etc/NetworkManager/system-connections/work.nmconnection").chmod(0o600)
    action = DropFilesAction(expand_config(captured),
                             ActionContext(target=Target(root=str(root))))
    managed = ["/etc/wireguard/eu-mad.conf",
               "/etc/NetworkManager/system-connections/work.nmconnection"]
    # Scoped to the tunnels: this fake root also carries the block-A features,
    # whose own drift is asserted by their own rows above.
    assert [c for c in action.plan(managed=managed) if c.item in managed] == []


# --- tailscale preferences ------------------------------------------------- #
#
# The domain that prompted this block: `tailscale` the package and
# `tailscaled.service` the unit were already declarable, so a machine looked
# fully captured while every preference on it was invisible.

def _ts_machine(tmp_path, block):
    from dasik.lib.actions.tailscale_action import TailscaleAction
    action = TailscaleAction({"tailscale": block},
                             ActionContext(target=Target(root=str(tmp_path))))
    action.apply(action.plan(managed=[]))
    return tmp_path


def _ts_captured(root, seed=None):
    from dasik.lib.actions.tailscale_action import TailscaleAction
    return TailscaleAction(seed or {},
                           ActionContext(target=Target(root=str(root)))).import_state()


def test_tailscale_prefs_are_captured_as_their_own_block(tmp_path):
    root = _ts_machine(tmp_path, {"accept_routes": True, "ssh": False,
                                  "advertise_routes": ["10.0.0.0/8"]})
    assert _ts_captured(root) == {"tailscale": {
        "accept_routes": True, "ssh": False,
        "advertise_routes": ["10.0.0.0/8"]}}


def test_a_machine_without_the_conffile_invents_no_tailscale_block(tmp_path):
    assert _ts_captured(tmp_path) == {}


def test_a_declared_block_the_machine_lacks_is_cleared_not_kept(tmp_path):
    """sync reports the machine. Silence would leave the stale declaration, since
    ConfigWriter.merge overwrites keys and never deletes them."""
    assert _ts_captured(tmp_path, {"tailscale": {"accept_routes": True}}) == {
        "tailscale": {}}


def test_the_captured_tailscale_block_validates(tmp_path):
    """`check` on a config `sync` just wrote — the round trip that keeps being
    missed. A capture the tool then refuses is a broken capture."""
    root = _ts_machine(tmp_path, {"accept_routes": True, "hostname": "box",
                                  "operator": "andres"})
    JsonModel.model_validate(_ts_captured(root))


def test_sync_then_plan_is_silent_for_tailscale(tmp_path):
    """The real invariant: capture, then plan the capture against the machine it
    came from, and nothing is proposed."""
    from dasik.lib.actions.tailscale_action import TailscaleAction
    root = _ts_machine(tmp_path, {"accept_routes": True, "shields_up": False})
    captured = _ts_captured(root)
    action = TailscaleAction(expand_config(captured),
                             ActionContext(target=Target(root=str(root))))
    assert action.plan(managed=[]) == []


def test_the_package_the_block_derives_is_not_written_back(tmp_path):
    """subtract_contributions must strip what the toggle re-derives, or the
    captured config grows a `tailscale` package line every round trip."""
    original = {"tailscale": {"accept_routes": True}}
    stripped = subtract_contributions(expand_config(original), original)
    assert "tailscale" not in stripped.get("packages", [])


# --- several FIDO2 keys ---------------------------------------------------- #
#
# The full matrix (one key captures as `true`, three as `3`, none invents
# nothing, and the capture re-plans to silence) lives beside the action, in
# tests/lib/actions/test_sync_captures_fido2_count.py, because it needs a
# luksDump fixture per count. What belongs HERE is the rule it enforces: the
# capture states how many keyslots the header really carries, and a machine
# with three keys must never come back describing one.

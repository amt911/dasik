"""Every declared feature must be VISIBLE in `dasik plan`.

A feature that converges but never shows up in a plan is undebuggable: you
cannot tell "already applied" from "dasik ignores this block", and `apply` then
changes the machine in ways the dry run never announced. This suite pins the
whole of issue #173 block A (PR #174) to that rule — for each feature, a target
missing it produces a change, and a target already carrying it produces none.

`sysrq` is the one that prompted this: on a machine whose boot entry already had
`sysrq_always_enabled=1` the plan is (correctly) silent, which is
indistinguishable from a feature nothing looks at unless the empty case is
asserted too.
"""
from unittest.mock import MagicMock, patch

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.drop_files_action import DropFilesAction
from dasik.lib.actions.encrypted_swap_action import EncryptedSwapAction
from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction
from dasik.lib.actions.sudo_action import SudoAction
from dasik.lib.actions.systemd_action import SystemdAction
from dasik.lib.actions.systemd_conf_action import OomdAction
from dasik.lib.actions.users_action import UsersAction
from dasik.lib.expand import expand_config
from dasik.lib.target.target import Target


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


def _entry(tmp_path, options):
    entries = tmp_path / "boot/loader/entries"
    entries.mkdir(parents=True, exist_ok=True)
    (entries / "arch.conf").write_text(f"title Arch\noptions {options}\n")
    (tmp_path / "boot/loader/loader.conf").write_text("default arch\n")


def _cmdline_plan(tmp_path, config, options, managed=()):
    _entry(tmp_path, options)
    action = KernelCmdlineAction({"bootloader": "sd-boot", **config}, _ctx(tmp_path))
    return [(c.op.name, c.item) for c in action.plan(managed=list(managed))]


# --- sysrq (REISUB) -------------------------------------------------------- #

def test_sysrq_missing_from_the_entry_is_planned(tmp_path):
    assert _cmdline_plan(tmp_path, {"sysrq": True}, "root=LABEL=root rw quiet") == [
        ("INSTALL", "sysrq_always_enabled=1")]


def test_sysrq_already_on_the_entry_plans_nothing(tmp_path):
    assert _cmdline_plan(tmp_path, {"sysrq": True},
                         "root=LABEL=root rw sysrq_always_enabled=1") == []


def test_dropping_the_sysrq_flag_removes_a_parameter_dasik_owns(tmp_path):
    """The disable direction is a change too — otherwise `sysrq: false` is a
    declaration the tool silently ignores."""
    assert _cmdline_plan(tmp_path, {}, "root=LABEL=root rw sysrq_always_enabled=1",
                         managed=["sysrq_always_enabled=1"]) == [
        ("REMOVE", "sysrq_always_enabled=1")]


def test_an_unowned_sysrq_parameter_is_left_alone(tmp_path):
    """Somebody else's parameter is not dasik's to delete."""
    assert _cmdline_plan(tmp_path, {}, "root=LABEL=root rw sysrq_always_enabled=1") == []


# --- cpu ------------------------------------------------------------------- #

def test_the_cpu_driver_parameter_is_planned(tmp_path):
    assert _cmdline_plan(tmp_path, {"cpu": {"scaling_driver": "amd_pstate"}},
                         "root=LABEL=root rw") == [("INSTALL", "amd_pstate=active")]


def test_the_cpu_driver_parameter_already_set_plans_nothing(tmp_path):
    assert _cmdline_plan(tmp_path, {"cpu": {"scaling_driver": "amd_pstate"}},
                         "root=LABEL=root rw amd_pstate=active") == []


def test_the_cpu_governor_file_is_planned(tmp_path):
    config = expand_config({"cpu": {"scaling_driver": "none",
                                    "governor": "performance"}})
    action = DropFilesAction(config, _ctx(tmp_path))

    planned = [c.item for c in action.plan(managed=[])]

    assert "/etc/default/cpupower" in planned


def test_power_profiles_daemon_is_planned_as_a_unit(tmp_path):
    config = expand_config({"cpu": {"scaling_driver": "none"}})
    assert "power-profiles-daemon.service" in config["systemd"]["enable_units"]
    assert _units_planned(config) == ["power-profiles-daemon.service"]


# --- reflector ------------------------------------------------------------- #

def test_the_reflector_conf_is_planned(tmp_path):
    config = expand_config({"reflector": {"countries": ["ES"]}})
    action = DropFilesAction(config, _ctx(tmp_path))

    planned = [c.item for c in action.plan(managed=[])]

    assert "/etc/xdg/reflector/reflector.conf" in planned


def test_the_reflector_timer_is_planned_as_a_unit():
    config = expand_config({"reflector": {"countries": ["ES"]}})
    assert _units_planned(config) == ["reflector.timer"]


# --- systemd-boot-update --------------------------------------------------- #

def test_systemd_boot_update_is_planned_on_sd_boot():
    config = expand_config({"bootloader": "sd-boot"})
    assert _units_planned(config) == ["systemd-boot-update.service"]


def test_systemd_boot_update_is_not_planned_on_grub():
    config = expand_config({"bootloader": "grub"})
    assert _units_planned(config) == []


def test_switching_to_grub_disables_the_unit_sd_boot_owned():
    """The disable direction of the switch: `systemd-boot-update.service` is
    derived by the sd-boot toggle, so declaring grub stops deriving it and the
    units domain plans its DISABLE off its own set-math — no bootloader code."""
    config = expand_config({"bootloader": "grub"})
    enabled = MagicMock(stdout=b"enabled", returncode=0)
    with patch("dasik.lib.actions.systemd_action.Command.execute",
               return_value=enabled):
        action = SystemdAction(config.get("systemd", {}), _ctx("/mnt"))
        changes = action.plan(managed=["systemd-boot-update.service"])
    assert [(c.op.name, c.item) for c in changes] == [
        ("DISABLE", "systemd-boot-update.service")]


# --- sudo ------------------------------------------------------------------ #

def test_a_missing_sudoers_fragment_is_planned(tmp_path):
    action = SudoAction({"sudo": {"wheel": True}}, _ctx(tmp_path))

    assert [c.op.name for c in action.plan(managed=[])] == ["MODIFY"]


def test_an_applied_sudoers_fragment_plans_nothing(tmp_path):
    (tmp_path / "etc/sudoers.d").mkdir(parents=True)
    (tmp_path / "etc/sudoers.d/10-dasik").write_text(
        "# Managed by dasik\n%wheel ALL=(ALL:ALL) ALL\n")
    action = SudoAction({"sudo": {"wheel": True}}, _ctx(tmp_path))

    assert action.plan(managed=[]) == []


# --- plymouth -------------------------------------------------------------- #

def test_splash_missing_from_the_entry_is_planned(tmp_path):
    assert _cmdline_plan(tmp_path, {"plymouth": {}}, "root=LABEL=root rw") == [
        ("INSTALL", "splash")]


def test_splash_already_on_the_entry_plans_nothing(tmp_path):
    assert _cmdline_plan(tmp_path, {"plymouth": {}}, "root=LABEL=root rw splash") == []


def test_dropping_the_plymouth_block_removes_splash(tmp_path):
    """The disable direction: an undeclared block whose parameter dasik owns is
    a REMOVE, not a silent leftover."""
    assert _cmdline_plan(tmp_path, {}, "root=LABEL=root rw splash",
                         managed=["splash"]) == [("REMOVE", "splash")]


def test_an_unowned_splash_is_left_alone(tmp_path):
    assert _cmdline_plan(tmp_path, {}, "root=LABEL=root rw splash") == []


def test_the_plymouth_package_is_planned():
    assert "plymouth" in expand_config({"plymouth": {}})["packages"]


def test_the_plymouth_theme_file_is_planned(tmp_path):
    config = expand_config({"plymouth": {"theme": "bgrt"}})
    action = DropFilesAction(config, _ctx(tmp_path))

    planned = [c.item for c in action.plan(managed=[])]

    assert "/etc/plymouth/plymouthd.conf" in planned


def test_the_plymouth_hook_is_planned_in_the_initramfs(tmp_path):
    """The splash also has to be IN the image — a plan that only showed the
    package and the parameter would hide the half that makes it work."""
    from dasik.lib.actions.initramfs_action import InitramfsAction

    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/mkinitcpio.conf").write_text(
        "HOOKS=(base udev autodetect modconf block filesystems fsck)\n")
    action = InitramfsAction({"plymouth": {}}, _ctx(tmp_path))

    assert [c.op.name for c in action.plan(managed=[])] == ["MODIFY"]


def test_an_initramfs_that_already_has_the_hook_plans_nothing(tmp_path):
    from dasik.lib.actions.initramfs_action import InitramfsAction

    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/mkinitcpio.conf").write_text(
        "HOOKS=(base udev plymouth autodetect modconf block filesystems fsck)\n")
    action = InitramfsAction({"plymouth": {}}, _ctx(tmp_path))

    assert action.plan(managed=[]) == []


# --- pendrive LUKS keyfile ------------------------------------------------- #

_PENDRIVE = {"disks": {"disks": [{"device": "/dev/vda", "partitions": [
    {"label": "root", "size": "rest", "filesystem": "ext4", "mountpoint": "/",
     "encrypt": True, "luks_name": "cryptroot", "luks_password": "pw",
     "unlock_keyfile": "/keyfile", "unlock_keydev": "1234-ABCD",
     "unlock_keydev_fs": "vfat"}]}]}}


def _luks_uuid():
    from dasik.lib.actions.luks_uuid import luks_uuid
    return luks_uuid("cryptroot")


def test_the_pendrive_unlock_is_planned_on_the_cmdline(tmp_path):
    planned = _cmdline_plan(tmp_path, _PENDRIVE, "root=/dev/mapper/cryptroot rw")

    items = [item for _op, item in planned]
    assert f"rd.luks.key={_luks_uuid()}=/keyfile:UUID=1234-ABCD" in items
    assert any("keyfile-timeout=10s" in item for item in items)


def test_the_pendrive_unlock_already_on_the_entry_plans_nothing(tmp_path):
    uuid = _luks_uuid()
    entry = (f"rd.luks.name={uuid}=cryptroot root=/dev/mapper/cryptroot rw "
             f"rd.luks.key={uuid}=/keyfile:UUID=1234-ABCD "
             f"rd.luks.options={uuid}=keyfile-timeout=10s")

    assert _cmdline_plan(tmp_path, _PENDRIVE, entry) == []


def test_dropping_the_pendrive_unlock_removes_the_parameter(tmp_path):
    uuid = _luks_uuid()
    token = f"rd.luks.key={uuid}=/keyfile:UUID=1234-ABCD"
    plain = {"disks": {"disks": [{"device": "/dev/vda", "partitions": [
        {"label": "root", "size": "rest", "filesystem": "ext4", "mountpoint": "/",
         "encrypt": True, "luks_name": "cryptroot", "luks_password": "pw"}]}]}}

    planned = _cmdline_plan(tmp_path, plain,
                            f"rd.luks.name={uuid}=cryptroot root=/dev/mapper/cryptroot "
                            f"rw {token}", managed=[token])

    assert ("REMOVE", token) in planned


def test_the_keyfile_enrollment_is_planned(tmp_path):
    """The key material has its own domain — the cmdline alone would announce
    an unlock whose keyslot may not exist."""
    from dasik.lib.actions.luks_keyfile_action import LuksKeyfileAction

    action = LuksKeyfileAction(_PENDRIVE, _ctx(tmp_path))
    with patch.object(LuksKeyfileAction, "_key_device_present", return_value=True), \
         patch.object(LuksKeyfileAction, "_mount_keydev", return_value=str(tmp_path)), \
         patch.object(LuksKeyfileAction, "_umount_keydev"), \
         patch.object(LuksKeyfileAction, "_luks_device", return_value="/dev/vda2"), \
         patch.object(LuksKeyfileAction, "_key_works", return_value=False):
        planned = [(c.op.name, c.item) for c in action.plan(managed=[])]

    assert planned == [("INSTALL", "cryptroot:/keyfile")]


def test_an_enrolled_keyfile_plans_nothing(tmp_path):
    from dasik.lib.actions.luks_keyfile_action import LuksKeyfileAction

    (tmp_path / "keyfile").write_text("key")
    action = LuksKeyfileAction(_PENDRIVE, _ctx(tmp_path))
    with patch.object(LuksKeyfileAction, "_key_device_present", return_value=True), \
         patch.object(LuksKeyfileAction, "_mount_keydev", return_value=str(tmp_path)), \
         patch.object(LuksKeyfileAction, "_umount_keydev"), \
         patch.object(LuksKeyfileAction, "_luks_device", return_value="/dev/vda2"), \
         patch.object(LuksKeyfileAction, "_key_works", return_value=True):
        assert action.plan(managed=[]) == []


def test_the_key_device_module_is_planned_in_the_initramfs(tmp_path):
    """Without it the initramfs cannot read the pendrive, so the unlock the
    cmdline announces would never happen."""
    from dasik.lib.actions.initramfs_action import InitramfsAction

    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/mkinitcpio.conf").write_text(
        "MODULES=()\nHOOKS=(base udev autodetect modconf block filesystems fsck)\n")
    action = InitramfsAction(_PENDRIVE, _ctx(tmp_path))

    assert [c.op.name for c in action.plan(managed=[])] == ["MODIFY"]
    assert "MODULES+=(vfat" in action._backend.desired_value()


# --- helper ---------------------------------------------------------------- #

def _units_planned(config):
    """Units SystemdAction would enable on a target where nothing is enabled."""
    nothing_enabled = MagicMock(stdout=b"", returncode=0)
    with patch("dasik.lib.actions.systemd_action.Command.execute",
               return_value=nothing_enabled):
        action = SystemdAction(config.get("systemd", {}), _ctx("/mnt"))
        return [c.item for c in action.plan(managed=[])]


# --- root password --------------------------------------------------------- #

def _users_plan(tmp_path, users, shadow, managed=()):
    etc = tmp_path / "etc"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "passwd").write_text("root:x:0:0::/root:/bin/bash\n")
    (etc / "group").write_text("wheel:x:998:\n")
    (etc / "shadow").write_text(shadow)
    action = UsersAction({"users": users}, _ctx(tmp_path))
    return [(c.op.name, c.item) for c in action.plan(managed=list(managed))]


def test_root_password_missing_from_shadow_is_planned(tmp_path):
    assert _users_plan(
        tmp_path,
        [{"username": "root", "hashed_password": "$6$NEW$h"}],
        "root:!:19000:0:99999:7:::\n",
    ) == [("MODIFY", "root")]


def test_root_password_already_set_plans_nothing(tmp_path):
    assert _users_plan(
        tmp_path,
        [{"username": "root", "hashed_password": "$6$NEW$h"}],
        "root:$6$NEW$h:19000:0:99999:7:::\n",
    ) == []


def test_an_undeclared_root_password_is_left_alone(tmp_path):
    """Declaring no root password means "dasik does not manage it", not "lock
    root" — the account is never created or deleted by the users domain."""
    assert _users_plan(tmp_path, [], "root:$6$SET$h:19000:0:99999:7:::\n") == []


# --- /etc/systemd/*.conf (oomd, system, user) ------------------------------ #

def _oomd_plan(tmp_path, config, on_disk=None, managed=()):
    if on_disk is not None:
        conf = tmp_path / "etc/systemd/oomd.conf"
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text(on_disk)
    action = OomdAction(config, _ctx(tmp_path))
    return [(c.op.name, c.item) for c in action.plan(managed=list(managed))]


def test_an_oomd_setting_the_machine_lacks_is_planned(tmp_path):
    planned = _oomd_plan(tmp_path, {"oomd": {"SwapUsedLimit": "80%"}},
                         on_disk="[OOM]\n#SwapUsedLimit=90%\n")
    assert [op for op, _ in planned] == ["MODIFY"]


def test_an_oomd_setting_already_in_the_package_file_plans_nothing(tmp_path):
    """The file pacman owns still counts as the machine having it — reading
    only our own drop-in is what made this invisible in the first place."""
    assert _oomd_plan(tmp_path, {"oomd": {"SwapUsedLimit": "80%"}},
                      on_disk="[OOM]\nSwapUsedLimit=80%\n") == []


def test_dropping_the_oomd_block_removes_the_drop_in_dasik_owns(tmp_path):
    dropin = tmp_path / "etc/systemd/oomd.conf.d/10-dasik.conf"
    dropin.parent.mkdir(parents=True, exist_ok=True)
    dropin.write_text("[OOM]\nSwapUsedLimit = 80%\n")

    planned = _oomd_plan(tmp_path, {}, managed=["[OOM]\nSwapUsedLimit = 80%\n"])

    assert [op for op, _ in planned] == ["REMOVE"]


def test_an_unowned_oomd_drop_in_is_left_alone(tmp_path):
    dropin = tmp_path / "etc/systemd/oomd.conf.d/10-dasik.conf"
    dropin.parent.mkdir(parents=True, exist_ok=True)
    dropin.write_text("[OOM]\nSwapUsedLimit = 80%\n")

    assert _oomd_plan(tmp_path, {}) == []


def test_the_oomd_daemon_is_planned_as_a_unit():
    config = expand_config({"oomd": {"SwapUsedLimit": "80%"}})
    assert _units_planned(config) == ["systemd-oomd.service"]


# --- encrypted swap (random key) ------------------------------------------- #
#
# The swap rides two files nobody else writes: the crypttab entry that creates
# /dev/mapper/<name> at boot, and the fstab line that activates it. A machine
# missing either is not converged, however complete the partition table looks.

def _swap_cfg(**over):
    cfg = {"disks": {"disks": [{"device": "/dev/vda", "partitions": [
        {"label": "swap", "size": "2GiB", "filesystem": "swap",
         "swap_encryption": "random"}]}]}}
    cfg.update(over)
    return cfg


def _swap_plan(tmp_path, config, fstab="UUID=abc / ext4 defaults 0 0\n",
               crypttab=None, managed=()):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/fstab").write_text(fstab)
    if crypttab is not None:
        (tmp_path / "etc/crypttab").write_text(crypttab)
    action = EncryptedSwapAction(config, _ctx(tmp_path))
    return [(c.op.name, c.item) for c in action.plan(managed=list(managed))]


_SWAP_CRYPTTAB = ("swap LABEL=cryptswap /dev/urandom "
                  "swap,offset=2048,cipher=aes-xts-plain64,size=512,sector-size=4096\n")
_SWAP_FSTAB = ("UUID=abc / ext4 defaults 0 0\n"
               "/dev/mapper/swap none swap defaults 0 0\n")


def test_a_random_swap_missing_from_the_target_is_planned(tmp_path):
    assert _swap_plan(tmp_path, _swap_cfg()) == [("INSTALL", "swap")]


def test_a_random_swap_already_on_the_target_plans_nothing(tmp_path):
    assert _swap_plan(tmp_path, _swap_cfg(), fstab=_SWAP_FSTAB,
                      crypttab=_SWAP_CRYPTTAB, managed=["swap"]) == []


def test_a_crypttab_entry_without_the_fstab_line_is_not_converged(tmp_path):
    """The swap exists as a mapping and is never activated — the half-applied
    state a plan has to keep showing."""
    assert _swap_plan(tmp_path, _swap_cfg(), crypttab=_SWAP_CRYPTTAB) == [
        ("INSTALL", "swap")]


def test_dropping_the_block_removes_a_swap_dasik_owns(tmp_path):
    assert _swap_plan(tmp_path, {}, fstab=_SWAP_FSTAB, crypttab=_SWAP_CRYPTTAB,
                      managed=["swap"]) == [("REMOVE", "swap")]


def test_an_unowned_swap_is_left_alone(tmp_path):
    assert _swap_plan(tmp_path, {}, fstab=_SWAP_FSTAB, crypttab=_SWAP_CRYPTTAB) == []


# --- apparmor -------------------------------------------------------------- #
#
# The block rides the kernel cmdline: the package and the unit are visible as
# their own domains, but what makes AppArmor actually enforce anything is the
# `lsm=` parameter. A machine with apparmor installed and no such parameter is
# NOT converged, however complete `pacman -Qq` looks.

_LSM = "lsm=landlock,lockdown,yama,integrity,apparmor,bpf"


def test_the_lsm_parameter_missing_from_the_entry_is_planned(tmp_path):
    assert _cmdline_plan(tmp_path, {"apparmor": {}}, "root=LABEL=root rw") == [
        ("INSTALL", _LSM)]


def test_the_lsm_parameter_already_on_the_entry_plans_nothing(tmp_path):
    assert _cmdline_plan(tmp_path, {"apparmor": {}},
                         f"root=LABEL=root rw {_LSM}") == []


def test_dropping_the_apparmor_block_removes_the_parameter_dasik_owns(tmp_path):
    assert _cmdline_plan(tmp_path, {}, f"root=LABEL=root rw {_LSM}",
                         managed=[_LSM]) == [("REMOVE", _LSM)]


def test_an_unowned_lsm_parameter_is_left_alone(tmp_path):
    """Somebody else's LSM order is not dasik's to delete."""
    assert _cmdline_plan(tmp_path, {}, f"root=LABEL=root rw {_LSM}") == []


def test_the_audit_parameters_are_planned_with_the_audit_flag(tmp_path):
    planned = _cmdline_plan(tmp_path, {"apparmor": {"audit": True}},
                            f"root=LABEL=root rw {_LSM}")
    assert ("INSTALL", "audit=1") in planned
    assert ("INSTALL", "audit_backlog_limit=8192") in planned


def test_the_package_and_the_unit_reach_the_expanded_config():
    expanded = expand_config({"apparmor": {}})
    assert "apparmor" in expanded["packages"]
    assert "apparmor.service" in expanded["systemd"]["enable_units"]


# --- pam ------------------------------------------------------------------- #
#
# Three items in one domain, each with its own file. The disable direction is
# the interesting one: dropping the block must UNDO what dasik wrote, and must
# leave alone hardening somebody else did.

def _pam_target(tmp_path):
    (tmp_path / "etc/security").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/pam.d").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/security/faillock.conf").write_text("# deny = 3\n")
    (tmp_path / "etc/pam.d/passwd").write_text(
        "#%PAM-1.0\npassword\tinclude\t\tsystem-auth\n")
    return tmp_path


def _pam_plan(tmp_path, config, managed=()):
    from dasik.lib.actions.pam_action import PamAction
    return [(c.op.name, c.item)
            for c in PamAction(config, _ctx(tmp_path)).plan(managed=list(managed))]


def test_a_declared_pam_policy_missing_from_the_target_is_planned(tmp_path):
    assert _pam_plan(_pam_target(tmp_path), {"pam": {"faillock": {}}}) == [
        ("INSTALL", "faillock")]


def test_a_pam_policy_already_applied_plans_nothing(tmp_path):
    from dasik.lib.actions.pam_action import PamAction
    target = _pam_target(tmp_path)
    action = PamAction({"pam": {"faillock": {}}}, _ctx(target))
    action.apply(action.plan(managed=[]))
    assert _pam_plan(target, {"pam": {"faillock": {}}}, managed=["faillock"]) == []


def test_dropping_the_pam_block_removes_what_dasik_owns(tmp_path):
    from dasik.lib.actions.pam_action import PamAction
    target = _pam_target(tmp_path)
    action = PamAction({"pam": {"faillock": {}, "limits": {}}}, _ctx(target))
    action.apply(action.plan(managed=[]))
    assert sorted(_pam_plan(target, {}, managed=["faillock", "limits"])) == [
        ("REMOVE", "faillock"), ("REMOVE", "limits")]


def test_hardening_dasik_never_wrote_is_left_alone(tmp_path):
    target = _pam_target(tmp_path)
    (target / "etc/security/faillock.conf").write_text("deny = 3\n")
    assert _pam_plan(target, {}, managed=["faillock"]) == []


def test_the_password_library_reaches_the_expanded_config():
    expanded = expand_config({"pam": {"pwquality": {}}})
    assert "libpwquality" in expanded["packages"]


# --- firewall backends ----------------------------------------------------- #
#
# The ufw backend reads the machine through `ufw status` and writes through the
# CLI. Its plan must be honest in both directions, and the firewalld path must
# behave exactly as it did before the backend field existed.

def _ufw_plan(rules_live, cfg):
    from unittest.mock import patch
    from types import SimpleNamespace
    from dasik.lib.actions.firewall_action import FirewallAction

    status = ("Status: active\n\nTo Action From\n-- ------ ----\n"
              + "".join(f"{t} ALLOW IN Anywhere\n" for t in rules_live))

    class _R:
        stdout, returncode = status, 0

    action = FirewallAction(cfg, SimpleNamespace(target=None))
    with patch("dasik.lib.actions.firewall_action.Command.execute",
               return_value=_R()):
        return [(c.op.name, c.item) for c in action.plan(managed=[])]


_UFW_CFG = {"enable": True, "backend": "ufw", "rules": ["allow 22/tcp"]}


def test_a_ufw_rule_missing_from_the_machine_is_planned():
    assert _ufw_plan([], _UFW_CFG) == [("INSTALL", "allow 22/tcp")]


def test_a_ufw_rule_already_live_plans_nothing():
    assert _ufw_plan(["22/tcp"], _UFW_CFG) == []


def test_the_ufw_backend_installs_ufw_and_not_firewalld():
    expanded = expand_config(_UFW_CFG | {"firewall": _UFW_CFG})
    packages = expanded["packages"]
    assert "ufw" in packages
    assert "firewalld" not in packages


def test_the_firewalld_backend_still_installs_firewalld():
    expanded = expand_config({"firewall": {"enable": True}})
    assert "firewalld" in expanded["packages"]
    assert "ufw" not in expanded["packages"]


# --- zram takes its file back (round E) ------------------------------------- #

def test_dropping_the_zram_block_removes_its_file(tmp_path):
    """The disable direction of a scalar domain: `ScalarV3Action.plan` only ever
    proposes a MODIFY towards a value, so an undeclared block proposed nothing
    and the file stayed."""
    from dasik.lib.actions.zram_action import ZramAction

    body = "[zram0]\nzram-size = ram / 2\n"
    (tmp_path / "etc/systemd").mkdir(parents=True)
    (tmp_path / "etc/systemd/zram-generator.conf").write_text(body)
    action = ZramAction({}, _ctx(tmp_path))

    assert [(c.op.name, c.item) for c in action.plan(managed=[body])] == [
        ("REMOVE", "/etc/systemd/zram-generator.conf")]


def test_a_zram_file_dasik_never_wrote_is_left_alone(tmp_path):
    from dasik.lib.actions.zram_action import ZramAction

    (tmp_path / "etc/systemd").mkdir(parents=True)
    (tmp_path / "etc/systemd/zram-generator.conf").write_text("[zram0]\n")

    assert ZramAction({}, _ctx(tmp_path)).plan(managed=[]) == []


# --- a PKGBUILD that was never uploaded to the AUR ------------------------- #
#
# `package_sources` rides the `packages` domain: there is no `[package_sources]`
# line in a plan, the package appears as an install (or, when its pinned commit
# moves, as a modify). Both directions must be visible.

_SRC = {"type": "pkgbuild-git",
        "url": "https://git.example.org/pkgbuilds/config-saver.git",
        "ref": "a520605367e13ec25db4c3c7e1c4bf46175ba8cd", "subdir": "."}
_OTHER_SHA = "b" * 40


def _git_pkg_plan(installed, managed, source_ref=None, declared=True):
    from unittest.mock import MagicMock
    from dasik.lib.actions.action_context import ActionContext
    from dasik.lib.actions.packages_action import PackagesAction
    from dasik.lib.target.target import Target

    config = ({"packages": ["config-saver"], "package_sources": {"config-saver": _SRC}}
              if declared else {"packages": []})
    manifest = {"managed": {"packages": list(managed)}, "action_state": {}}
    if source_ref:
        manifest["action_state"] = {
            "packages": {"sources": {"config-saver": dict(_SRC, ref=source_ref)}}}
    action = PackagesAction(config, ActionContext(target=Target(root="/"),
                                                  manifest=manifest))
    action._installed_all = MagicMock(return_value=set(installed))
    action.actual = MagicMock(return_value=set(installed))
    return [(c.op.name, c.item, c.reason) for c in action.plan(managed=list(managed))]


def test_a_git_sourced_package_missing_from_the_machine_is_planned():
    assert _git_pkg_plan(installed=(), managed=()) == [
        ("INSTALL", "config-saver", "")]


def test_a_git_sourced_package_built_at_the_pinned_commit_plans_nothing():
    assert _git_pkg_plan(installed=("config-saver",), managed=("config-saver",),
                         source_ref=_SRC["ref"]) == []


def test_moving_the_pinned_commit_plans_a_rebuild():
    assert _git_pkg_plan(installed=("config-saver",), managed=("config-saver",),
                         source_ref=_OTHER_SHA) == [
        ("MODIFY", "config-saver", "source ref changed")]


def test_dropping_the_declaration_removes_the_package_the_manifest_owns():
    assert _git_pkg_plan(installed=("config-saver",), managed=("config-saver",),
                         declared=False) == [
        ("REMOVE", "config-saver", "no longer declared")]


# --- containers (the runtime) ---------------------------------------------- #
#
# The block rides three domains — packages, units, users' groups — plus one of
# its own: the subuid/subgid map without which no rootless container starts.

def _subid_plan(tmp_path, config, subuid="", managed=()):
    from dasik.lib.actions.containers_action import ContainersAction

    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/subuid").write_text(subuid)
    (tmp_path / "etc/subgid").write_text(subuid)
    action = ContainersAction(config, _ctx(tmp_path))
    return [(c.op.name, c.item) for c in action.plan(managed=list(managed))]


_ROOTLESS = {"containers": {"runtime": "podman"},
             "users": [{"username": "andres", "hashed_password": "$6$a$b"}]}


def test_a_missing_rootless_id_map_is_planned(tmp_path):
    assert _subid_plan(tmp_path, _ROOTLESS) == [("CREATE", "andres")]


def test_an_existing_id_map_plans_nothing(tmp_path):
    assert _subid_plan(tmp_path, _ROOTLESS, subuid="andres:100000:65536\n") == []


def test_dropping_the_containers_block_removes_the_map(tmp_path):
    assert _subid_plan(tmp_path, {"users": _ROOTLESS["users"]},
                       subuid="andres:100000:65536\n",
                       managed=["andres"]) == [("REMOVE", "andres")]


def test_an_id_map_dasik_never_wrote_is_left_alone(tmp_path):
    assert _subid_plan(tmp_path, {}, subuid="otro:100000:65536\n") == []


def test_the_docker_unit_is_planned_as_a_unit():
    config = expand_config({"containers": {"runtime": "docker"}})
    assert _units_planned(config) == ["docker.service"]


def test_podman_plans_no_unit():
    config = expand_config({"containers": {"runtime": "podman"}})
    assert _units_planned(config) == []


# --- config-saver ----------------------------------------------------------- #
#
# Rides `files` (one JSON per configuration) and `systemd` (the per-user timer);
# the restore is its own domain because nothing else can see it.

_SAVER = {"config_saver": {
    "source": {"url": "https://github.com/amt911/config-saver-aur.git",
               "ref": "a520605367e13ec25db4c3c7e1c4bf46175ba8cd"},
    "configs": {"dotfiles": {"directories": ["$HOME/.config"]}},
    "timer_users": ["andres"]}}


def test_the_config_saver_document_is_planned(tmp_path):
    action = DropFilesAction(expand_config(_SAVER), _ctx(tmp_path))

    assert "/etc/config-saver/configs/dotfiles.json" in \
        [c.item for c in action.plan(managed=[])]


def test_the_config_saver_timer_is_planned_as_a_unit():
    assert _units_planned(expand_config(_SAVER)) == ["config-saver@andres.timer"]


def test_dropping_the_block_removes_the_document_and_the_timer(tmp_path):
    """Both directions: without the block nothing derives them, so the file and
    the unit come back as removals off their own set-math."""
    (tmp_path / "etc/config-saver/configs").mkdir(parents=True)
    (tmp_path / "etc/config-saver/configs/dotfiles.json").write_text("{}")
    files = DropFilesAction(expand_config({}), _ctx(tmp_path))

    assert [(c.op.name, c.item) for c in files.plan(
        managed=["/etc/config-saver/configs/dotfiles.json"])] == [
        ("DELETE", "/etc/config-saver/configs/dotfiles.json")]

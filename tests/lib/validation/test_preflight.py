"""Cross-field preflight: reject an incoherent config BEFORE the first mutation.

Schema validation (pydantic) only proves each field's shape. These checks are the
ones the 2026-07-19 install needed: a user demanding the `docker` group that no
declared package creates (useradd would have failed after the disk was already
wiped), a display-manager unit no package provides, and a /etc/crypttab entry with
a malformed option pointing at a device that does not exist.
"""
from dasik.lib.validation.preflight import preflight, has_errors


def _errors(cfg):
    return [i for i in preflight(cfg) if i.level == "error"]


def _warnings(cfg):
    return [i for i in preflight(cfg) if i.level == "warning"]


# --- supplementary groups -------------------------------------------------- #

def test_group_with_known_provider_not_declared_is_an_error():
    cfg = {"users": [{"username": "andres", "groups": ["docker", "wheel"]}],
           "packages": ["podman", "podman-docker", "docker-buildx"]}
    errs = _errors(cfg)
    assert len(errs) == 1
    assert errs[0].code == "group_without_provider"
    assert "docker" in errs[0].message
    assert has_errors(preflight(cfg))


def test_group_provided_by_declared_package_is_accepted():
    cfg = {"users": [{"username": "andres", "groups": ["docker", "libvirt"]}],
           "packages": ["docker", "libvirt"]}
    assert _errors(cfg) == []


def test_base_groups_need_no_package():
    cfg = {"users": [{"username": "andres",
                      "groups": ["wheel", "video", "audio", "input", "storage"]}]}
    assert _errors(cfg) == []


def test_unknown_group_is_a_warning_not_an_error():
    cfg = {"users": [{"username": "andres", "groups": ["mycustomgroup"]}]}
    assert _errors(cfg) == []
    assert [w.code for w in _warnings(cfg)] == ["unknown_group"]


# --- systemd units with a known provider ----------------------------------- #

def test_non_dm_unit_without_its_package_is_only_a_warning():
    """openssh & friends are often pulled in as a dependency, so we cannot prove
    the unit will be missing — inform, do not block."""
    cfg = {"systemd": {"enable_units": ["sshd.service"]}, "packages": ["base"]}
    assert _errors(cfg) == []
    assert [w.code for w in _warnings(cfg)] == ["unit_without_provider"]


def test_display_manager_unit_without_its_package_is_an_error():
    cfg = {"systemd": {"enable_units": ["sddm.service"]},
           "packages": ["plasma-meta"]}
    errs = _errors(cfg)
    assert [e.code for e in errs] == ["unit_without_provider"]
    assert "sddm" in errs[0].message


def test_plasmalogin_unit_accepted_via_plasma_meta():
    cfg = {"systemd": {"enable_units": ["plasmalogin.service"]},
           "packages": ["plasma-meta"]}
    assert _errors(cfg) == []


def test_two_display_managers_enabled_is_an_error():
    cfg = {"systemd": {"enable_units": ["sddm.service", "plasmalogin.service"]},
           "packages": ["sddm", "plasma-login-manager"]}
    assert [e.code for e in _errors(cfg)] == ["multiple_display_managers"]


# --- crypttab -------------------------------------------------------------- #

def _crypttab(content, **extra):
    cfg = {"files": [{"path": "/etc/crypttab", "content": content}]}
    cfg.update(extra)
    return cfg


_DISKS = {"disks": {"disks": [{
    "device": "/dev/vda",
    "partitions": [
        {"label": "esp", "mountpoint": "/boot", "filesystem": "fat32"},
        {"label": "root", "filesystem": "btrfs", "encrypt": True,
         "luks_name": "cryptroot"},
    ]}]}}


def test_crypttab_malformed_option_is_an_error():
    cfg = _crypttab("cryptswap LABEL=cryptswap /dev/urandom swap,cipher=aes-xts-plain64,size512\n",
                    **_DISKS)
    codes = [e.code for e in _errors(cfg)]
    assert "crypttab_bad_option" in codes


def test_crypttab_swap_on_undeclared_device_is_an_error():
    """`swap` reformats the device on every boot — it must name a declared one."""
    cfg = _crypttab("cryptswap LABEL=cryptswap /dev/urandom swap,size=512\n", **_DISKS)
    codes = [e.code for e in _errors(cfg)]
    assert "crypttab_undeclared_device" in codes


def test_crypttab_entry_for_declared_luks_partition_is_accepted():
    cfg = _crypttab("cryptroot LABEL=root none luks,discard\n", **_DISKS)
    assert _errors(cfg) == []


def test_crypttab_comments_and_blank_lines_ignored():
    cfg = _crypttab("# <name> <device> <password> <options>\n\n", **_DISKS)
    assert _errors(cfg) == []


# --- clean config ---------------------------------------------------------- #

def test_display_manager_config_files_for_another_dm_warn():
    cfg = {"systemd": {"enable_units": ["plasmalogin.service"]},
           "packages": ["plasma-meta"],
           "sddm_conf_d": [{"name": "kde_settings.conf", "content": "[Theme]\n"}]}
    assert _errors(cfg) == []
    assert [w.code for w in _warnings(cfg)] == ["display_manager_config_mismatch"]


def test_coherent_config_has_no_issues():
    cfg = {
        # `sudo` is declared on purpose: a user in `wheel` with no package
        # providing sudo is exactly the wheel_without_sudo warning below.
        "users": [{"username": "andres", "groups": ["wheel", "libvirt"]}],
        # networkmanager is declared: enabling its unit without it is the
        # unit_without_provider warning, not a coherent config.
        "packages": ["libvirt", "plasma-meta", "sudo", "networkmanager"],
        "systemd": {"enable_units": ["plasmalogin.service", "NetworkManager.service"]},
    }
    assert preflight(cfg) == []


# --- sudo ------------------------------------------------------------------ #

def test_explicit_sudo_block_without_the_sudo_package_is_an_error():
    issues = preflight({"sudo": {"wheel": True}, "packages": ["base"]}, efi_boot=True)
    assert any(i.code == "sudo_without_provider" and i.level == "error" for i in issues)


def test_sudo_block_with_base_devel_is_accepted():
    issues = preflight({"sudo": {"wheel": True}, "packages": ["base", "base-devel"]}, efi_boot=True)
    assert not any(i.code == "sudo_without_provider" for i in issues)


def test_implicit_wheel_default_without_sudo_only_warns():
    issues = preflight({"users": [{"username": "andres", "groups": ["wheel"]}],
                        "packages": ["base"]}, efi_boot=True)
    assert any(i.code == "wheel_without_sudo" and i.level == "warning" for i in issues)
    assert not any(i.code == "sudo_without_provider" for i in issues)


def test_no_sudo_finding_when_nothing_asks_for_it():
    issues = preflight({"packages": ["base"]}, efi_boot=True)
    assert not any(i.code in ("sudo_without_provider", "wheel_without_sudo") for i in issues)


# --- cpu / power-profiles-daemon ------------------------------------------- #

def test_ppd_with_an_explicit_governor_warns():
    issues = preflight({"cpu": {"power_profiles_daemon": True, "governor": "performance"}},
                       efi_boot=True)
    assert any(i.code == "ppd_and_governor" and i.level == "warning" for i in issues)


def test_ppd_with_tlp_is_an_error():
    issues = preflight({"cpu": {"power_profiles_daemon": True}, "packages": ["tlp"]},
                       efi_boot=True)
    assert any(i.code == "ppd_and_tlp" and i.level == "error" for i in issues)


def test_governor_without_ppd_is_clean():
    issues = preflight({"cpu": {"power_profiles_daemon": False, "governor": "performance"}},
                       efi_boot=True)
    assert not any(i.code in ("ppd_and_governor", "ppd_and_tlp") for i in issues)


def test_ppd_unit_without_its_package_warns():
    issues = preflight({"systemd": {"enable_units": ["power-profiles-daemon.service"]},
                        "packages": ["base"]}, efi_boot=True)
    assert any(i.code == "unit_without_provider" and i.level == "warning" for i in issues)

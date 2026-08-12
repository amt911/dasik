"""What a declared `apparmor` block contributes to the base domains.

The package alone protects nothing — the kernel parameter that activates the LSM
is derived by KernelCmdlineAction, and is asserted there. Here: packages, units,
the audit group and the profile files.
"""
from dasik.lib.expand import expand_config
from dasik.lib.expand.toggles import expand_apparmor


def test_an_absent_block_contributes_nothing():
    assert expand_apparmor({}) == {}


def test_a_disabled_block_contributes_nothing():
    assert expand_apparmor({"apparmor": {"enable": False}}) == {}


def test_the_base_contribution_is_the_package_and_the_unit():
    out = expand_apparmor({"apparmor": {}})
    assert out["packages"] == ["apparmor"]
    assert out["units"] == ["apparmor.service"]
    assert "user_groups" not in out
    assert "files" not in out


def test_audit_adds_the_daemon_the_group_and_the_tmpfiles_override():
    out = expand_apparmor({"apparmor": {"audit": True}})
    assert "audit" in out["packages"]
    assert "auditd.service" in out["units"]
    assert out["user_groups"] == ["audit"]
    override = [f for f in out["files"] if f["path"] == "/etc/tmpfiles.d/audit.conf"]
    assert override, "the log directory override must be contributed"
    assert "750 root audit" in override[0]["content"]


def test_profiles_become_files_under_the_profile_directory():
    out = expand_apparmor({"apparmor": {"extra_profiles": [
        {"name": "usr.bin.foo", "content": "profile foo {}\n"}]}})
    assert {"path": "/etc/apparmor.d/usr.bin.foo",
            "content": "profile foo {}\n"} in out["files"]


def test_the_expanded_config_carries_it_all_through():
    expanded = expand_config({"apparmor": {"audit": True},
                              "users": [{"username": "andres", "password": "x"}]})
    assert "apparmor" in expanded["packages"]
    assert "apparmor.service" in expanded["systemd"]["enable_units"]
    assert "auditd.service" in expanded["systemd"]["enable_units"]
    # The group is what lets a desktop user read /var/log/audit at all.
    assert "audit" in expanded["users"][0]["groups"]

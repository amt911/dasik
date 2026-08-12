"""Capturing the `apparmor` block back from a machine.

Convergence rides other domains (a package, a unit, a kernel parameter, files),
so without this the feature is a one-way street: sync a machine running AppArmor
and the captured config comes back with the parameter hand-set in
`kernel_cmdline` and no `apparmor` block — the same policy, spelled the way
dasik cannot reason about, and re-applying it never installs AppArmor.
"""
import os
from types import SimpleNamespace

from dasik.lib.actions.apparmor_action import ApparmorAction

_LSM = "lsm=landlock,lockdown,yama,integrity,apparmor,bpf"


class _Target:
    def __init__(self, root):
        self.root = str(root)

    def path(self, canonical):
        return os.path.join(self.root, canonical.lstrip("/"))


def _machine(tmp_path, installed=True, params="root=LABEL=root rw", profiles=(),
             auditd=False):
    (tmp_path / "boot/loader/entries").mkdir(parents=True, exist_ok=True)
    (tmp_path / "boot/loader/loader.conf").write_text("default arch\n")
    (tmp_path / "boot/loader/entries/arch.conf").write_text(
        f"title Arch\noptions {params}\n")
    binaries = tmp_path / "usr/bin"
    binaries.mkdir(parents=True, exist_ok=True)
    if installed:
        (binaries / "apparmor_parser").write_text("")
    if auditd:
        (binaries / "auditd").write_text("")
    profile_dir = tmp_path / "etc/apparmor.d"
    profile_dir.mkdir(parents=True, exist_ok=True)
    for name, content in profiles:
        (profile_dir / name).write_text(content)
    return tmp_path


def _action(tmp_path, cfg=None):
    return ApparmorAction({"bootloader": "sd-boot", **(cfg or {})},
                          SimpleNamespace(target=_Target(tmp_path)))


def _unowned(monkeypatch):
    """Nothing in /etc/apparmor.d belongs to a package (the default in tests)."""
    monkeypatch.setattr(ApparmorAction, "_pacman_owner", lambda self, path: None)


def test_a_machine_without_apparmor_captures_nothing(tmp_path):
    assert _action(_machine(tmp_path, installed=False)).import_state() == {}


def test_an_installed_and_active_apparmor_captures_the_block(tmp_path, monkeypatch):
    _unowned(monkeypatch)
    machine = _machine(tmp_path, params=f"root=LABEL=root rw {_LSM}")

    assert _action(machine).import_state() == {
        "apparmor": {"enable": True, "audit": False}}


def test_apparmor_installed_but_not_the_active_lsm_captures_it_disabled(tmp_path, monkeypatch):
    """The package without the kernel parameter enforces nothing. Reporting
    `enable: true` would describe a machine that is not actually protected."""
    _unowned(monkeypatch)
    machine = _machine(tmp_path)

    assert _action(machine).import_state()["apparmor"]["enable"] is False


def test_an_lsm_naming_other_modules_only_does_not_count(tmp_path, monkeypatch):
    _unowned(monkeypatch)
    machine = _machine(tmp_path, params="root=LABEL=root rw lsm=landlock,yama,bpf")

    assert _action(machine).import_state()["apparmor"]["enable"] is False


def test_the_audit_daemon_is_captured(tmp_path, monkeypatch):
    _unowned(monkeypatch)
    machine = _machine(tmp_path, params=f"root=LABEL=root rw {_LSM} audit=1",
                       auditd=True)

    assert _action(machine).import_state()["apparmor"]["audit"] is True


def test_auditd_without_the_kernel_parameter_is_not_the_audit_mode(tmp_path, monkeypatch):
    """auditd may be installed for its own sake. What this block owns is the
    pair — the daemon AND the parameter that feeds it."""
    _unowned(monkeypatch)
    machine = _machine(tmp_path, params=f"root=LABEL=root rw {_LSM}", auditd=True)

    assert _action(machine).import_state()["apparmor"]["audit"] is False


def test_local_profiles_are_captured(tmp_path, monkeypatch):
    _unowned(monkeypatch)
    machine = _machine(tmp_path, params=f"root=LABEL=root rw {_LSM}",
                       profiles=[("usr.bin.foo", "profile foo {}\n")])

    assert _action(machine).import_state()["apparmor"]["extra_profiles"] == [
        {"name": "usr.bin.foo", "content": "profile foo {}\n"}]


def test_package_owned_profiles_are_not_captured(tmp_path, monkeypatch):
    """The profiles the apparmor package ships are already implied by the
    package; capturing them would drag hundreds of files into the config."""
    monkeypatch.setattr(ApparmorAction, "_pacman_owner", lambda self, path: "apparmor")
    machine = _machine(tmp_path, params=f"root=LABEL=root rw {_LSM}",
                       profiles=[("usr.bin.pacman", "profile pacman {}\n")])

    assert "extra_profiles" not in _action(machine).import_state()["apparmor"]


def test_the_profile_subdirectories_are_not_walked(tmp_path, monkeypatch):
    """abstractions/, tunables/ and local/ are AppArmor's own machinery, not
    profiles somebody wrote."""
    _unowned(monkeypatch)
    machine = _machine(tmp_path, params=f"root=LABEL=root rw {_LSM}")
    (machine / "etc/apparmor.d/abstractions").mkdir(parents=True, exist_ok=True)
    (machine / "etc/apparmor.d/abstractions/base").write_text("…\n")

    assert "extra_profiles" not in _action(machine).import_state()["apparmor"]


def test_it_plans_nothing(tmp_path):
    assert _action(_machine(tmp_path)).plan(managed=[]) == []


def test_it_owns_no_manifest_domain(tmp_path):
    assert _action(_machine(tmp_path)).managed_keys() == {}

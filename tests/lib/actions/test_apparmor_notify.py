"""AppArmor desktop notifications (`aa-notify`).

The wiki's recipe, made declarative: the three python/tk dependencies plus an
autostart entry in every desktop user's `$HOME`. It is the first consumer of the
`home_files` domain, and the reason that domain had to exist — the entry lives
at `~/.config/autostart/apparmor-notify.desktop` and nowhere else.
"""
import pytest
from pydantic import ValidationError

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.apparmor_action import ApparmorAction
from dasik.lib.expand import expand_config, subtract_contributions
from dasik.lib.models.apparmor_model import ApparmorModel
from dasik.lib.target.target import Target


_AUTOSTART = ".config/autostart/apparmor-notify.desktop"
_USERS = [{"username": "andres", "hashed_password": "$6$x$y"},
          {"username": "root", "hashed_password": "$6$x$y"}]


def _config(**over):
    apparmor = {"enable": True, "audit": True, "desktop_notifications": True}
    apparmor.update(over.pop("apparmor", {}))
    return {"apparmor": apparmor, "users": _USERS, **over}


# --- the model --------------------------------------------------------------#

def test_notifications_are_off_by_default():
    assert ApparmorModel().desktop_notifications is False


def test_notifications_require_the_audit_framework():
    """aa-notify reads /var/log/audit/audit.log. Without auditd there is no log,
    so the notifier starts and shows nothing forever."""
    with pytest.raises(ValidationError, match="audit"):
        ApparmorModel(enable=True, audit=False, desktop_notifications=True)


def test_notifications_with_audit_validate():
    assert ApparmorModel(enable=True, audit=True,
                         desktop_notifications=True).desktop_notifications


# --- expansion ------------------------------------------------------------- #

def test_the_notifier_dependencies_are_installed():
    packages = expand_config(_config())["packages"]
    for pkg in ("python-notify2", "python-psutil", "tk"):
        assert pkg in packages


def test_no_dependencies_without_the_flag():
    packages = expand_config(_config(apparmor={"desktop_notifications": False}))["packages"]
    assert "python-notify2" not in packages


def test_every_desktop_user_gets_the_autostart_entry():
    home_files = expand_config(_config())["home_files"]

    assert [f["user"] for f in home_files] == ["andres"]      # root has no desktop
    assert home_files[0]["path"] == _AUTOSTART


def test_the_entry_runs_aa_notify_against_the_audit_log():
    content = expand_config(_config())["home_files"][0]["content"]

    assert "Exec=aa-notify -p -s 1 -w 60 -f /var/log/audit/audit.log" in content
    assert "TryExec=aa-notify" in content
    assert content.startswith("[Desktop Entry]")


def test_a_declared_home_file_survives_the_expansion():
    mine = {"user": "andres", "path": ".gitconfig", "content": "[user]\n"}
    home_files = expand_config(_config(home_files=[mine]))["home_files"]

    assert mine in home_files
    assert len(home_files) == 2


# --- capture ---------------------------------------------------------------- #

def _machine(tmp_path, notify=True, users=("andres",)):
    (tmp_path / "usr/bin").mkdir(parents=True)
    (tmp_path / "usr/bin/apparmor_parser").write_text("")
    (tmp_path / "usr/bin/auditd").write_text("")
    (tmp_path / "boot/loader/entries").mkdir(parents=True)
    (tmp_path / "boot/loader/loader.conf").write_text("default arch\n")
    (tmp_path / "boot/loader/entries/arch.conf").write_text(
        "title Arch\noptions root=LABEL=root rw lsm=landlock,lockdown,yama,"
        "integrity,apparmor,bpf audit=1\n")
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/passwd").write_text(
        "root:x:0:0::/root:/bin/bash\n"
        + "".join(f"{u}:x:1000:1000::/home/{u}:/bin/bash\n" for u in users))
    if notify:
        for user in users:
            d = tmp_path / f"home/{user}/.config/autostart"
            d.mkdir(parents=True)
            (d / "apparmor-notify.desktop").write_text("[Desktop Entry]\n")
    return tmp_path


def _captured(tmp_path, **kw):
    action = ApparmorAction({"bootloader": "sd-boot"},
                            ActionContext(target=Target(root=str(_machine(tmp_path, **kw)))))
    return action.import_state([])["apparmor"]


def test_sync_reports_the_notifier_the_machine_has(tmp_path):
    assert _captured(tmp_path)["desktop_notifications"] is True


def test_sync_invents_no_notifier(tmp_path):
    assert _captured(tmp_path, notify=False)["desktop_notifications"] is False


def test_the_captured_block_re_derives_the_autostart_entry(tmp_path):
    """The round trip that matters: what sync captures must expand back into the
    same file, or the capture describes a machine it cannot rebuild."""
    captured = {"apparmor": _captured(tmp_path), "users": _USERS}

    assert expand_config(captured)["home_files"][0]["path"] == _AUTOSTART


def test_the_derived_entry_is_not_captured_as_a_hand_written_home_file():
    """`subtract_contributions` must attribute the entry to the block, or the
    captured config carries it twice — once as the block, once as noise."""
    config = _config()
    captured = subtract_contributions(expand_config(config), config)

    assert captured["home_files"] == []

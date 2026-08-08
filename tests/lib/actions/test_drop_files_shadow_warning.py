"""Writing over a package's file is legitimate — silently is not.

`/etc/pam.d/sudo` belongs to the sudo package; dropping dasik's own version
means pacman will leave a `.pacnew` beside it on every upgrade and the override
never picks the change up. For PAM that is how a machine stops accepting logins
months later. Arch also ships vendor PAM files under /usr/lib/pam.d, where
`pacman -Qo /etc/pam.d/<x>` finds nothing but the shadowing is just as total.

A warning, not an error: overriding is a real thing people do on purpose (the
laptop config does it deliberately for the fingerprint).
"""
from unittest.mock import MagicMock, patch

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.drop_files_action import DropFilesAction
from dasik.lib.target.target import Target


def _action(tmp_path, path, content="x\n"):
    cfg = {"files": [{"path": path, "content": content}]}
    return DropFilesAction(cfg, ActionContext(target=Target(root=str(tmp_path))))


def _vendor_file(tmp_path, canonical):
    """Create the /usr/lib counterpart inside the fake target."""
    path = tmp_path / ("usr/lib/" + canonical[len("/etc/"):])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("vendor\n")


def _plan_with(action, owner=None):
    """Plan with `pacman -Qo` answering *owner* (None = nothing owns it)."""
    def fake_execute(cmd, args, **kw):
        if cmd == "pacman" and args and args[0] == "-Qo" and owner:
            return MagicMock(returncode=0,
                             stdout=f"{args[1]} is owned by {owner}\n".encode())
        return MagicMock(returncode=1, stdout=b"")

    with patch("dasik.lib.actions.drop_files_action.Command.execute", side_effect=fake_execute), \
         patch("dasik.lib.actions.drop_files_action.run_logger") as logger:
        action.plan(managed=[])
        return logger.get.return_value


def test_warns_when_a_package_owns_the_path(tmp_path):
    action = _action(tmp_path, "/etc/pam.d/sudo")
    log = _plan_with(action, owner="sudo 1.9.17.p2-6")
    assert log.warning.called
    message = " ".join(str(a) for a in log.warning.call_args[0])
    assert "/etc/pam.d/sudo" in message


def test_the_warning_names_the_owning_package(tmp_path):
    action = _action(tmp_path, "/etc/pam.d/sudo")
    log = _plan_with(action, owner="sudo 1.9.17.p2-6")
    text = str(log.warning.call_args)
    assert "sudo" in text


def test_warns_for_a_vendor_pam_file_nobody_owns_under_etc(tmp_path):
    """polkit-1 / plasmalogin live in /usr/lib/pam.d; `pacman -Qo /etc/...` misses them."""
    _vendor_file(tmp_path, "/etc/pam.d/polkit-1")
    action = _action(tmp_path, "/etc/pam.d/polkit-1")
    log = _plan_with(action, owner=None)
    assert log.warning.called
    assert "/usr/lib/pam.d/polkit-1" in str(log.warning.call_args)


def test_no_warning_for_a_file_that_is_dasik_s_own(tmp_path):
    action = _action(tmp_path, "/etc/modprobe.d/nested_virt.conf")
    log = _plan_with(action, owner=None)
    assert not log.warning.called


def test_the_write_still_happens(tmp_path):
    """The warning must not turn into a refusal."""
    action = _action(tmp_path, "/etc/pam.d/sudo")
    with patch("dasik.lib.actions.drop_files_action.Command.execute",
               return_value=MagicMock(returncode=0, stdout=b"owned by sudo")), \
         patch("dasik.lib.actions.drop_files_action.run_logger"):
        changes = action.plan(managed=[])
    assert [c.item for c in changes] == ["/etc/pam.d/sudo"]

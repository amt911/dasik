"""A managed file that is now a symlink must be replaced, not written through.

Found in a VM by replacing a file dasik owns with `ln -s /dev/null …`:

    plan  → ~ [files] modify …/dotfiles.json  (content drift)
    apply → "Applied: now at generation 9"
    ls    → still a symlink to /dev/null
    plan  → ~ [files] modify …/dotfiles.json  (content drift)   forever

`open(path, "w")` follows the link, so the content went to /dev/null, the file
never converged, and the plan repeated for ever.

A file dasik manages is a regular file; anything else in its place is replaced.
(The `home_files` domain has the same hole and it is worse there — `~/.config`
belongs to the user and dasik writes it as root — but that code is still on its
own branch, so it is fixed there.)
"""
import os

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.drop_files_action import DropFilesAction
from dasik.lib.target.target import Target


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


# --- /etc files ------------------------------------------------------------- #

_ETC = {"files": [{"path": "/etc/dasik-test.conf", "content": "managed=yes\n"}]}


def _drop(tmp_path):
    return DropFilesAction(_ETC, _ctx(tmp_path))


def test_a_symlink_where_a_managed_file_belongs_is_planned(tmp_path):
    (tmp_path / "etc").mkdir()
    (tmp_path / "elsewhere").write_text("managed=yes\n")   # same content!
    os.symlink(tmp_path / "elsewhere", tmp_path / "etc/dasik-test.conf")

    changes = _drop(tmp_path).plan(managed=["/etc/dasik-test.conf"])

    assert [c.op.name for c in changes] == ["MODIFY"]
    assert "symlink" in changes[0].reason


def test_apply_replaces_the_symlink_with_a_real_file(tmp_path):
    (tmp_path / "etc").mkdir()
    target = tmp_path / "elsewhere"
    target.write_text("someone else's file\n")
    os.symlink(target, tmp_path / "etc/dasik-test.conf")

    action = _drop(tmp_path)
    action.apply(action.plan(managed=[]))

    written = tmp_path / "etc/dasik-test.conf"
    assert not written.is_symlink()
    assert written.read_text() == "managed=yes\n"
    assert target.read_text() == "someone else's file\n"   # untouched


def test_and_then_it_converges(tmp_path):
    (tmp_path / "etc").mkdir()
    os.symlink("/dev/null", tmp_path / "etc/dasik-test.conf")

    action = _drop(tmp_path)
    action.apply(action.plan(managed=[]))

    assert action.plan(managed=["/etc/dasik-test.conf"]) == []

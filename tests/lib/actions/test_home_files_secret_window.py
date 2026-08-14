"""A dotfile with a mode is a secret too — and it must never be world-readable.

`home_files` carries a mode for the same reason `files` does: an SSH config, a
`.netrc`, a token an app reads. dasik wrote the content first, then chmod'ed,
then chown'ed — so between the write and the chmod the secret was on disk
readable by everyone, and between the write and the chown it belonged to root
inside a home the user controls.

The window is what is tested: mode and owner are asserted from inside the write,
at the instant the descriptor exists and no content has landed.
"""
import os

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.home_files_action import HomeFilesAction
from dasik.lib.target.target import Target

_SECRET = "Host git\n  IdentityFile ~/.ssh/id_ed25519\n"


def _machine(tmp_path):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "home/test").mkdir(parents=True, exist_ok=True)
    uid = os.getuid()
    (tmp_path / "etc/passwd").write_text(f"test:x:{uid}:{uid}::/home/test:/bin/bash\n")
    return tmp_path


def _action(tmp_path, mode="0600"):
    entry = {"user": "test", "path": ".ssh/config", "content": _SECRET}
    if mode:
        entry["mode"] = mode
    return HomeFilesAction({"home_files": [entry]},
                           ActionContext(target=Target(root=str(tmp_path))))


def test_the_dotfile_is_created_with_its_mode(tmp_path, monkeypatch):
    action = _action(_machine(tmp_path))
    opens = []
    real_open = os.open
    monkeypatch.setattr(os, "open",
                        lambda path, flags, mode=0o777: (opens.append((str(path), mode)),
                                                         real_open(path, flags, mode))[1])

    action.apply(action.plan(managed=[]))

    assert [m for p, m in opens if p.endswith(".ssh/config")] == [0o600]
    assert oct(os.stat(tmp_path / "home/test/.ssh/config").st_mode & 0o777) == "0o600"


def test_a_dotfile_left_world_readable_is_tightened_before_the_new_content(tmp_path, monkeypatch):
    root = _machine(tmp_path)
    (root / "home/test/.ssh").mkdir(parents=True)
    stale = root / "home/test/.ssh/config"
    stale.write_text("Host old\n")
    os.chmod(stale, 0o644)
    action = _action(root)

    observed = {}
    real_fdopen = os.fdopen

    def spy(fd, *a, **kw):
        observed["mode"] = oct(os.stat(stale).st_mode & 0o777)
        observed["content"] = stale.read_text()
        return real_fdopen(fd, *a, **kw)

    monkeypatch.setattr(os, "fdopen", spy)
    action.apply(action.plan(managed=[]))

    assert observed["mode"] == "0o600", "the secret was written into a world-readable file"
    assert observed["content"] == ""
    assert stale.read_text() == _SECRET


def test_a_dotfile_with_no_mode_is_written_as_before(tmp_path):
    root = _machine(tmp_path)
    action = _action(root, mode=None)

    action.apply(action.plan(managed=[]))

    assert (root / "home/test/.ssh/config").read_text() == _SECRET

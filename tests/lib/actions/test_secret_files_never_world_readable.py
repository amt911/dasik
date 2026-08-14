"""A declared 0600 file must never exist at 0644, not even for an instant.

`files` entries carry a mode because WireGuard and NetworkManager keyfiles hold
private keys. dasik wrote the content first and chmod'ed after, so between the
two the key sat on disk readable by every user on the machine — and if the
process died in between (a power cut, a failed apply), it stayed that way.

The window is what is tested here: the mode is asserted from INSIDE the write,
at the moment the content lands.
"""
import os

from unittest.mock import MagicMock

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.drop_files_action import DropFilesAction
from dasik.lib.target.target import Target

_PATH = "/etc/wireguard/wg0.conf"
_SECRET = "[Interface]\nPrivateKey = SECRET\n"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(DropFilesAction, "_vendor_copy", lambda self, path: None)
    monkeypatch.setattr("dasik.lib.actions.drop_files_action.run_logger.get",
                        lambda: MagicMock())


def _action(tmp_path, mode="0600"):
    entry = {"path": _PATH, "content": _SECRET}
    if mode:
        entry["mode"] = mode
    action = DropFilesAction({"files": [entry]},
                             ActionContext(target=Target(root=str(tmp_path))))
    action._pacman_owner = lambda path: None      # type: ignore[assignment]
    return action


def test_the_secret_file_is_CREATED_with_its_mode(tmp_path, monkeypatch):
    """O_CREAT's mode argument, not a chmod after the fact."""
    action = _action(tmp_path)
    opens = []
    real_open = os.open
    monkeypatch.setattr(os, "open",
                        lambda path, flags, mode=0o777: (opens.append((path, mode)),
                                                         real_open(path, flags, mode))[1])

    action.apply(action.plan(managed=[]))

    secret_opens = [m for p, m in opens if p.endswith("wg0.conf")]
    assert secret_opens == [0o600]
    assert oct(os.stat(str(tmp_path) + _PATH).st_mode & 0o777) == "0o600"


def test_an_existing_world_readable_file_is_tightened_before_any_content(tmp_path, monkeypatch):
    """The dangerous case: the key is already there at 0644 from an older dasik."""
    (tmp_path / "etc/wireguard").mkdir(parents=True)
    stale = tmp_path / "etc/wireguard/wg0.conf"
    stale.write_text("[Interface]\nPrivateKey = OLD\n")
    os.chmod(stale, 0o644)
    action = _action(tmp_path)

    observed = {}
    real_fdopen = os.fdopen

    def spy(fd, *a, **kw):
        # the descriptor is open and truncated; nothing is written yet
        observed["mode"] = oct(os.stat(stale).st_mode & 0o777)
        observed["content"] = stale.read_text()
        return real_fdopen(fd, *a, **kw)

    monkeypatch.setattr(os, "fdopen", spy)
    action.apply(action.plan(managed=[]))

    assert observed["mode"] == "0o600", "the key was still world-readable at write time"
    assert observed["content"] == ""
    assert stale.read_text() == _SECRET
    assert oct(os.stat(stale).st_mode & 0o777) == "0o600"


def test_a_file_with_no_declared_mode_is_written_as_before(tmp_path, monkeypatch):
    action = _action(tmp_path, mode=None)

    action.apply(action.plan(managed=[]))

    assert (tmp_path / "etc/wireguard/wg0.conf").read_text() == _SECRET

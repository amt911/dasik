"""The LUKS keyfile must be root-only from the moment it exists.

`dd if=/dev/random of=<keyfile>` creates the file with the process umask — 0644
for root — and dasik chmod'ed it to 0600 afterwards. For the length of that dd
(4 MiB of /dev/random, which blocks) the key that unlocks the disk was readable
by every user on the machine, and it stayed that way if the run died in between.

The pendrive case was already safe by accident: FAT is mounted `umask=0077` and
rejects chmod outright. A keyfile on any real filesystem was not.

The file is now created empty and private first; dd only fills it in (`of=`
truncates the contents, not the mode).
"""
import os

from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.luks_keyfile_action import LuksKeyfileAction


def _create(tmp_path, local):
    action = LuksKeyfileAction({}, None)
    with patch("dasik.lib.actions.luks_keyfile_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0)
        action._create_keyfile(str(local))
    return execute


def test_the_keyfile_exists_and_is_private_before_dd_writes_the_key(tmp_path):
    local = tmp_path / "keys" / "unlock.key"

    execute = _create(tmp_path, local)

    assert local.exists(), "dd must fill in a file that already exists"
    assert oct(local.stat().st_mode & 0o777) == "0o600"
    # and dd was still asked to write it
    (cmd, args), _ = execute.call_args
    assert cmd == "dd" and f"of={local}" in args


def test_the_mode_is_set_before_the_command_runs_not_after(tmp_path):
    """The whole point: no window between the key existing and it being private."""
    local = tmp_path / "keys" / "unlock.key"
    seen = {}

    action = LuksKeyfileAction({}, None)
    with patch("dasik.lib.actions.luks_keyfile_action.Command.execute") as execute:
        def record(*_a, **_kw):
            seen["mode"] = oct(os.stat(local).st_mode & 0o777)
            return MagicMock(returncode=0)
        execute.side_effect = record
        action._create_keyfile(str(local))

    assert seen["mode"] == "0o600"


def test_an_existing_keyfile_is_still_left_alone(tmp_path):
    """A pendrive may already carry the key another machine unlocks with."""
    local = tmp_path / "unlock.key"
    local.write_text("existing key material")

    execute = _create(tmp_path, local)

    execute.assert_not_called()
    assert local.read_text() == "existing key material"


def test_a_filesystem_that_refuses_the_mode_does_not_abort_the_install(tmp_path, monkeypatch):
    """FAT answers EPERM; the mount carries umask=0077, so the key is root-only
    anyway and aborting would leave a keyfile that was never enrolled."""
    local = tmp_path / "keys" / "unlock.key"
    real_open = os.open

    def fat(path, flags, mode=0o777):
        return real_open(path, flags, 0o666)      # mode argument ignored, like FAT

    monkeypatch.setattr(os, "open", fat)
    monkeypatch.setattr(os, "fchmod", lambda *_a: (_ for _ in ()).throw(PermissionError()))

    execute = _create(tmp_path, local)

    assert local.exists()
    (cmd, _args), _ = execute.call_args
    assert cmd == "dd"

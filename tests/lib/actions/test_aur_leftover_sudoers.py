"""A build that died leaves a passwordless-sudo account behind.

The AUR installer grants its build user NOPASSWD sudo so makepkg can sync repo
dependencies, and removes it in a `finally`. That covers an exception; it does
not cover SIGKILL, a full disk, or the power going out mid-build — and what is
left on the machine is:

    /etc/sudoers.d/_aurbuilder:  _aurbuilder ALL=(ALL) NOPASSWD: ALL

Nothing looks at it afterwards. The sudo domain owns only its own fragment, so
the leftover is invisible to `plan`, to `sync`, and to the next apply unless that
apply happens to build another AUR package.

Two things here: the fragment is created 0440 (sudo's own convention, and not
world-readable), and a leftover from a previous run is reported.
"""
import os

from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.aur_installer import AurInstaller
from dasik.lib.target.target import Target


def _installer(tmp_path):
    (tmp_path / "etc/sudoers.d").mkdir(parents=True, exist_ok=True)
    return AurInstaller(Target(root=str(tmp_path)))


def test_the_fragment_is_not_world_readable(tmp_path):
    inst = _installer(tmp_path)
    with patch.object(AurInstaller, "_run", return_value=MagicMock(returncode=0)):
        inst._ensure_prerequisites()

    path = tmp_path / "etc/sudoers.d/_aurbuilder"
    assert oct(path.stat().st_mode & 0o777) == "0o440"


def test_a_leftover_from_a_dead_build_is_reported(tmp_path):
    path = tmp_path / "etc/sudoers.d/_aurbuilder"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("_aurbuilder ALL=(ALL) NOPASSWD: ALL\n")
    inst = _installer(tmp_path)

    with patch.object(AurInstaller, "_run", return_value=MagicMock(returncode=0)), \
         patch("dasik.lib.actions.aur_installer.run_logger.get") as logger:
        inst._ensure_prerequisites()

    said = " ".join(str(c) for c in logger.return_value.warning.call_args_list)
    assert "_aurbuilder" in said and "passwordless" in said.lower()


def test_a_first_run_says_nothing(tmp_path):
    inst = _installer(tmp_path)

    with patch.object(AurInstaller, "_run", return_value=MagicMock(returncode=0)), \
         patch("dasik.lib.actions.aur_installer.run_logger.get") as logger:
        inst._ensure_prerequisites()

    logger.return_value.warning.assert_not_called()


def test_cleanup_still_removes_it(tmp_path):
    inst = _installer(tmp_path)
    path = str(tmp_path / "etc/sudoers.d/_aurbuilder")
    with patch.object(AurInstaller, "_run", return_value=MagicMock(returncode=0)):
        inst._ensure_prerequisites()
        assert os.path.exists(path)
        inst._cleanup(False, path)

    assert not os.path.exists(path)

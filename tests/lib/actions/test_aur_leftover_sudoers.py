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


def test_a_caller_that_owns_the_fragment_is_not_told_a_build_died(tmp_path):
    """A git package whose makedepends lives in the AUR comes through here from
    INSIDE PkgbuildGitInstaller, which wrote that fragment seconds ago and still
    needs it. The leftover warning then fires on every such build and says
    something untrue — "a previous AUR build did not finish" — right where
    somebody debugging a failed build will read it."""
    path = tmp_path / "etc/sudoers.d/_aurbuilder"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("_aurbuilder ALL=(ALL) NOPASSWD: ALL\n")
    inst = _installer(tmp_path)

    with patch.object(AurInstaller, "_run", return_value=MagicMock(returncode=0)), \
         patch("dasik.lib.actions.aur_installer.run_logger.get") as logger:
        inst._ensure_prerequisites(fragment_is_ours=True)

    logger.return_value.warning.assert_not_called()
    # still written, because the build about to run needs it
    assert path.exists()


def test_cleanup_still_removes_it(tmp_path):
    inst = _installer(tmp_path)
    path = str(tmp_path / "etc/sudoers.d/_aurbuilder")
    with patch.object(AurInstaller, "_run", return_value=MagicMock(returncode=0)):
        inst._ensure_prerequisites()
        assert os.path.exists(path)
        inst._cleanup(False, path)

    assert not os.path.exists(path)

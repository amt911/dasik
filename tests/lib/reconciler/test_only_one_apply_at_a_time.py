"""Two applies against one target must not run at the same time.

Nothing stopped them. Both read the same manifest, both mutate the same machine,
and the last one to finish writes the ownership record — so whatever the other
installed is now unowned, and the next plan proposes to remove it. pacman has its
own lock and would refuse the second transaction, but everything else in dasik
(files, units, disks, the manifest itself) has none.

The lock is per TARGET, not global: applying to /mnt from a live ISO while
something else manages / is a normal thing to do.
"""
import os

import pytest

from dasik.lib.state.apply_lock import ApplyLock, ApplyLockBusy
from dasik.lib.target.target import Target


def _target(tmp_path):
    return Target(root=str(tmp_path))


def test_the_first_one_gets_it(tmp_path):
    with ApplyLock(_target(tmp_path)):
        pass            # released cleanly


def test_the_second_one_is_refused_while_it_is_held(tmp_path):
    target = _target(tmp_path)
    with ApplyLock(target):
        with pytest.raises(ApplyLockBusy) as exc:
            with ApplyLock(target):
                pass

    assert "another dasik" in str(exc.value).lower()
    assert str(os.getpid()) in str(exc.value)      # says WHO holds it


def test_it_is_free_again_afterwards(tmp_path):
    target = _target(tmp_path)
    with ApplyLock(target):
        pass
    with ApplyLock(target):
        pass


def test_a_crash_does_not_leave_it_held(tmp_path):
    target = _target(tmp_path)
    with pytest.raises(RuntimeError):
        with ApplyLock(target):
            raise RuntimeError("apply blew up")

    with ApplyLock(target):
        pass            # the next run is not blocked by the corpse


def test_two_different_targets_do_not_block_each_other(tmp_path):
    """Applying to /mnt from a live ISO while something manages / is normal."""
    a, b = tmp_path / "mnt", tmp_path / "other"
    a.mkdir(); b.mkdir()
    with ApplyLock(Target(root=str(a))), ApplyLock(Target(root=str(b))):
        pass


def test_a_stale_lock_file_from_a_dead_process_is_not_fatal(tmp_path):
    """The file lives under the target; a power cut leaves it behind. flock is
    held by the PROCESS, so a leftover file locks nothing."""
    target = _target(tmp_path)
    path = tmp_path / "var/lib/dasik/apply.lock"
    path.parent.mkdir(parents=True)
    path.write_text("99999999\n")

    with ApplyLock(target):
        pass


def test_a_target_it_cannot_write_to_does_not_block_the_apply(tmp_path):
    """The lock is a safety net, not a gate: a target where the file cannot be
    created is one where the apply is about to fail on its own, with a better
    message than "could not lock"."""
    unwritable = tmp_path / "ro"
    unwritable.mkdir()
    os.chmod(unwritable, 0o500)
    try:
        with ApplyLock(Target(root=str(unwritable))):
            pass        # must not raise
    finally:
        os.chmod(unwritable, 0o700)

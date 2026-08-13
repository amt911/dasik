"""A disk that matches by name but not by content must say so.

`_disk_converged` compares LABELS and nothing else, so a partition declared
`btrfs` on a disk that carries `ext4` under the same label reads as converged:

    $ dasik plan
    No changes - system matches config.

The machine still has ext4. dasik cannot convert it in place and must not wipe
a populated disk without `wipe_disk: true` — so the plan being empty is right.
Saying nothing is not: that is the disk domain, and "no changes" there means
"your filesystems are what you declared".

The convergence rule itself is left exactly as it was, on purpose: it also
decides whether a `wipe_disk: true` config repartitions, and a stricter answer
there would erase a disk on every apply.
"""
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.disk_partition_action import DiskPartitionAction
from dasik.lib.target.target import Target


def _cfg(fs="btrfs", wipe=False, encrypt=False):
    part = {"label": "ROOT", "size": "rest", "filesystem": fs,
            "partition_type": "linux", "mountpoint": "/"}
    if encrypt:
        part.update({"encrypt": True, "luks_name": "cryptroot"})
    return {"disks": [{"device": "/dev/vda", "partition_table": "gpt",
                       "wipe_disk": wipe,
                       "partitions": [
                           {"label": "ESP", "size": "512MiB", "filesystem": "fat32",
                            "partition_type": "esp", "mountpoint": "/boot"},
                           part]}]}


def _run(cfg, lsblk_pairs):
    """lsblk_pairs: what `lsblk -no LABEL,FSTYPE` reports."""
    action = DiskPartitionAction(cfg, ActionContext(target=Target(root="/")))
    warnings = []

    def fake(cmd, args=None, *a, **k):
        if cmd == "lsblk" and args and "LABEL,FSTYPE" in " ".join(args):
            body = "".join(f"{lab} {fs}\n" for lab, fs in lsblk_pairs)
            return MagicMock(stdout=body.encode(), returncode=0)
        if cmd == "lsblk":
            body = "".join(f"{lab}\n" for lab, _ in lsblk_pairs)
            return MagicMock(stdout=body.encode(), returncode=0)
        return MagicMock(stdout=b"", returncode=0)

    logger = MagicMock()
    logger.warning = lambda msg, **kw: warnings.append(msg)
    from dasik.lib.logging import run_logger
    with patch("dasik.lib.actions.disk_partition_action.Command.execute", side_effect=fake), \
         patch.object(run_logger, "get", return_value=logger):
        changes = action.plan(managed=["/dev/vda"])
    return [(c.op.name, c.item) for c in changes], warnings


def test_a_filesystem_that_does_not_match_is_reported():
    changes, warnings = _run(_cfg(fs="btrfs"), [("ESP", "vfat"), ("ROOT", "ext4")])

    assert changes == []                       # dasik still refuses to act
    assert any("ROOT" in w and "ext4" in w and "btrfs" in w for w in warnings)


def test_a_matching_disk_says_nothing():
    changes, warnings = _run(_cfg(fs="ext4"), [("ESP", "vfat"), ("ROOT", "ext4")])

    assert (changes, warnings) == ([], [])


def test_fat32_is_vfat_to_lsblk():
    """The declared spelling and the reported one differ for FAT."""
    _changes, warnings = _run(_cfg(fs="ext4"), [("ESP", "vfat"), ("ROOT", "ext4")])

    assert not any("ESP" in w for w in warnings)


def test_an_encrypted_partition_reports_crypto_LUKS():
    changes, warnings = _run(_cfg(fs="ext4", encrypt=True),
                             [("ESP", "vfat"), ("ROOT", "crypto_LUKS")])

    assert (changes, warnings) == ([], [])


def test_a_partition_declared_encrypted_that_is_not_is_reported():
    """The one that matters most: dasik would derive rd.luks.name for a volume
    that is plain, and the plan said nothing at all."""
    changes, warnings = _run(_cfg(fs="ext4", encrypt=True),
                             [("ESP", "vfat"), ("ROOT", "ext4")])

    assert changes == []
    assert any("ROOT" in w and "crypto_LUKS" in w for w in warnings)


def test_the_wipe_decision_is_untouched():
    """A `wipe_disk: true` config on a disk whose labels match must STILL be
    converged — deciding otherwise erases the disk on every apply."""
    changes, _warnings = _run(_cfg(fs="btrfs", wipe=True),
                              [("ESP", "vfat"), ("ROOT", "ext4")])

    assert changes == []


def test_a_probe_that_cannot_answer_warns_about_nothing():
    action = DiskPartitionAction(_cfg(), ActionContext(target=Target(root="/")))
    with patch("dasik.lib.actions.disk_partition_action.Command.execute",
               side_effect=OSError("no lsblk")):
        assert action.plan(managed=["/dev/vda"]) == []

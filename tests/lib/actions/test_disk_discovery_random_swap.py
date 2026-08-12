"""A random-key swap looks like a 1 MiB ext2 partition to lsblk.

The swap itself only exists behind /dev/mapper, created at boot — the partition
carries nothing but the label the crypttab entry addresses it by. dasik cannot
represent ext2, so before this the whole partition was SKIPPED during discovery:
a `sync` of a machine with an encrypted swap produced a layout with no swap at
all, and re-applying it silently dropped the feature.

/etc/crypttab is what identifies it, so that is what discovery reads.
"""
import os
from unittest.mock import patch

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.disk_partition_action import DiskPartitionAction
from dasik.lib.target.target import Target


CRYPTTAB = ("swap LABEL=cryptswap /dev/urandom "
            "swap,offset=2048,cipher=aes-xts-plain64,size=512,sector-size=4096\n")


def _tree():
    return [{"name": "vda", "path": "/dev/vda", "type": "disk", "pttype": "gpt",
             "children": [
                 {"name": "vda1", "path": "/dev/vda1", "type": "part",
                  "fstype": "vfat", "size": 512 * 1024**2,
                  "parttypename": "EFI System", "mountpoint": "/boot"},
                 {"name": "vda2", "path": "/dev/vda2", "type": "part",
                  "fstype": "ext2", "label": "cryptswap", "size": 2 * 1024**3,
                  "parttypename": "Linux swap"},
             ]}]


def _no_crypt(cmd, args=None, *rest, **kw):
    raise FileNotFoundError(cmd)


def _discover(tmp_path, crypttab):
    os.makedirs(tmp_path / "etc", exist_ok=True)
    (tmp_path / "etc" / "crypttab").write_text(crypttab)
    action = DiskPartitionAction({}, ActionContext(target=Target(root=str(tmp_path))))
    with patch.object(DiskPartitionAction, "_lsblk_tree", return_value=_tree()), \
         patch.object(DiskPartitionAction, "_findmnt_btrfs_rows", return_value=[]), \
         patch("dasik.lib.actions.disk_partition_action.Command.execute",
               side_effect=_no_crypt):
        return action.import_state(managed=[])


def _partitions(frag):
    return frag["disks"]["disks"][0]["partitions"]


def test_the_partition_is_captured_as_a_random_key_swap(tmp_path):
    parts = _partitions(_discover(tmp_path, CRYPTTAB))
    swap = [p for p in parts if p.get("swap_encryption") == "random"]
    assert len(swap) == 1
    assert swap[0]["filesystem"] == "swap"


def test_the_captured_label_is_the_mapper_name_not_the_ext2_label(tmp_path):
    """Re-applying must derive the SAME ext2 label. Names are derived as
    label -> crypt<label>, so capturing "cryptswap" would produce
    "cryptcryptswap" on the next apply and the crypttab entry would point at a
    label nothing provides."""
    swap = [p for p in _partitions(_discover(tmp_path, CRYPTTAB))
            if p.get("swap_encryption") == "random"][0]
    assert swap["label"] == "swap"


def test_the_captured_partition_is_never_destructive(tmp_path):
    swap = [p for p in _partitions(_discover(tmp_path, CRYPTTAB))
            if p.get("swap_encryption") == "random"][0]
    assert swap["format"] is False


def test_a_plain_ext2_partition_is_still_skipped(tmp_path):
    """Without a crypttab entry naming it, an ext2 partition is exactly what it
    looks like: a filesystem dasik cannot represent."""
    parts = _partitions(_discover(tmp_path, ""))
    assert [p["label"] for p in parts] == ["boot"]


def test_a_crypttab_entry_with_a_persistent_key_is_not_a_random_swap(tmp_path):
    parts = _partitions(_discover(
        tmp_path, "swap LABEL=cryptswap /etc/keyfile swap,offset=2048\n"))
    assert [p["label"] for p in parts] == ["boot"]


def test_a_crypttab_entry_without_the_swap_option_is_not_one_either(tmp_path):
    parts = _partitions(_discover(
        tmp_path, "swap LABEL=cryptswap /dev/urandom cipher=aes-xts-plain64\n"))
    assert [p["label"] for p in parts] == ["boot"]

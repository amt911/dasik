"""`sync` must report the subvolumes the machine has mounted, not the config.

VM-proven on 2026-08-12 (encrypted megamix): a config declaring btrfs
subvolumes without `mount_options` synced back with
`mount_options: ["compress-force=zstd"]` on every one of them — the *model
default*, materialized by `model_dump()`, not anything the machine reported.
The machine mounts each subvolume with the partition's own
`compress-force=zstd:3` and nothing else.

The captured config then derived
`rootflags=compress-force=zstd:3,compress-force=zstd,subvol=@` — the same
option twice with different values — so `sync` → `plan` proposed a change on a
machine that had just been captured, and the value it proposed was one the
kernel would resolve by silently taking the last.
"""
from unittest.mock import patch

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.disk_partition_action import DiskPartitionAction
from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction
from dasik.lib.models.disk_model import BtrfsSubvolume, Partition
from dasik.lib.target.target import Target


_DECLARED = {
    "disks": [{
        "device": "/dev/vda", "partition_table": "gpt", "wipe_disk": True,
        "partitions": [
            {"label": "esp", "size": "512MiB", "filesystem": "fat32",
             "partition_type": "esp", "mountpoint": "/boot"},
            {"label": "root", "size": "rest", "filesystem": "btrfs",
             "partition_type": "linux", "mountpoint": None, "encrypt": True,
             "luks_name": "cryptroot", "luks_password": "x",
             "mount_options": ["compress-force=zstd:3"],
             # No mount_options here — exactly how a human writes it, and where
             # the model default sneaks in.
             "btrfs_subvolumes": [{"name": "@", "mountpoint": "/"},
                                  {"name": "@home", "mountpoint": "/home"}]},
        ]}]}

# What findmnt reports on the booted machine: every subvolume inherits the
# partition's option, and carries nothing of its own.
_ROWS = [
    ("/", "/dev/mapper/cryptroot[/@]",
     "rw,relatime,compress-force=zstd:3,space_cache=v2,subvolid=256,subvol=/@"),
    ("/home", "/dev/mapper/cryptroot[/@home]",
     "rw,relatime,compress-force=zstd:3,space_cache=v2,subvolid=257,subvol=/@home"),
]


def _captured(rows=_ROWS):
    action = DiskPartitionAction(_DECLARED, ActionContext(target=Target(root="/")))
    with patch.object(DiskPartitionAction, "_findmnt_btrfs_rows", return_value=rows), \
         patch("dasik.lib.actions.disk_partition_action.Command.execute",
               side_effect=FileNotFoundError("no cryptsetup here")):
        return action.import_state(managed=[])


def _root_partition(fragment):
    return [p for p in fragment["disks"]["disks"][0]["partitions"]
            if p.get("btrfs_subvolumes")][0]


def test_the_model_default_is_not_captured_as_if_it_were_reality():
    part = _root_partition(_captured())

    assert part["mount_options"] == ["compress-force=zstd:3"]
    for subvol in part["btrfs_subvolumes"]:
        assert subvol["mount_options"] == [], subvol["name"]


def test_an_option_only_one_subvolume_really_has_is_captured():
    rows = list(_ROWS)
    rows[1] = ("/home", "/dev/mapper/cryptroot[/@home]",
               "rw,compress-force=zstd:3,compress=lzo,subvol=/@home")
    part = _root_partition(_captured(rows))

    home = next(s for s in part["btrfs_subvolumes"] if s["name"] == "@home")
    assert "compress=lzo" in home["mount_options"]


def test_an_unmounted_subvolume_keeps_what_the_config_declared():
    """Nothing to read means nothing to correct — capturing an empty list there
    would silently drop an option from a subvolume that simply is not mounted."""
    part = _root_partition(_captured(rows=[]))

    names = {s["name"] for s in part["btrfs_subvolumes"]}
    assert names == {"@", "@home"}
    for subvol in part["btrfs_subvolumes"]:
        assert subvol["mount_options"] == ["compress-force=zstd"]


def test_the_captured_config_derives_the_rootflags_the_machine_already_has():
    """The invariant: `sync` → `plan` is silent."""
    captured = _captured()
    derived = KernelCmdlineAction(captured, None)._derived()
    rootflags = [t for t in derived if t.startswith("rootflags=")]

    assert rootflags == ["rootflags=compress-force=zstd:3,subvol=@"]


# --- and a mount option can never be spelled twice ------------------------- #

def test_one_option_never_appears_twice_with_different_values():
    """`compress-force=zstd:3,compress-force=zstd` is not a merge, it is a
    contradiction the kernel resolves by taking the last one — silently not
    what either line asked for."""
    partition = Partition(label="root", size="rest", filesystem="btrfs",
                          mount_options=["compress-force=zstd:3", "noatime"])
    subvol = BtrfsSubvolume(name="@", mountpoint="/",
                            mount_options=["compress-force=zstd"])

    merged = DiskPartitionAction._subvol_mount_options(partition, subvol)

    compress = [o for o in merged if o.startswith("compress-force=")]
    assert compress == ["compress-force=zstd"], merged   # the subvolume is more specific
    assert "noatime" in merged
    assert merged[-1] == "subvol=@"

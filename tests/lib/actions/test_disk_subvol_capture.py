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


def test_a_declared_noatime_option_is_captured_alongside_compress():
    """Root cause A (rootflags sync drift): `_live_subvol_options` used to keep
    only `compress*`, so a REAL declared option like `noatime` was silently
    dropped from a synced config — a reinstall from the capture lost it."""
    rows = list(_ROWS)
    rows[0] = ("/", "/dev/mapper/cryptroot[/@]",
               "rw,noatime,compress-force=zstd:3,space_cache=v2,subvolid=256,subvol=/@")
    part = _root_partition(_captured(rows))

    root = next(s for s in part["btrfs_subvolumes"] if s["name"] == "@")
    assert root["mount_options"] == ["noatime"]   # compress-force is the partition base


def test_a_data_disks_subvolume_of_the_same_name_never_leaks_into_root():
    """S1 (review of fix/rootflags-sync-drift, measured via probe_managed_and_
    merge.py P4): `_live_subvol_options` used to key by subvolume NAME across
    the WHOLE machine, so a data disk that happens to reuse `@` (a common
    layout for VM image stores) could transplant its own options onto the
    ROOT subvolume's capture — the root disk's `noatime`/`compress-force`
    would be silently replaced by the data disk's `nodatacow`, and the
    derived `rootflags=` would then mount `/` uncompressed with checksums
    off for new files."""
    declared = {
        "disks": [
            _DECLARED["disks"][0],
            {"device": "/dev/sda", "partition_table": "gpt",
             "partitions": [
                 {"label": "data", "size": "rest", "filesystem": "btrfs",
                  "partition_type": "linux", "mountpoint": None,
                  "btrfs_subvolumes": [{"name": "@", "mountpoint": "/mnt/data"}]},
             ]},
        ]}
    rows = list(_ROWS) + [
        ("/mnt/data", "/dev/sda1[/@]",
         "rw,relatime,nodatacow,space_cache=v2,subvolid=256,subvol=/@"),
    ]
    action = DiskPartitionAction(declared, ActionContext(target=Target(root="/")))
    with patch.object(DiskPartitionAction, "_findmnt_btrfs_rows", return_value=rows), \
         patch("dasik.lib.actions.disk_partition_action.Command.execute",
               side_effect=FileNotFoundError("no cryptsetup here")):
        captured = action.import_state(managed=[])

    part = _root_partition(captured)
    root_sv = next(s for s in part["btrfs_subvolumes"] if s["name"] == "@")

    assert root_sv["mount_options"] == []            # unchanged: compress-force is the base
    assert "nodatacow" not in root_sv["mount_options"]


def test_a_trailing_slash_on_the_declared_mountpoint_does_not_skip_correction():
    """NIT-4 (re-review round 2): `BtrfsSubvolume.mountpoint` has no validator,
    so a human-written `"/home/"` (trailing slash) must still find the
    findmnt row for `/home` -- the declaration is a spelling choice, not a
    different mountpoint, and skipping the correction would silently leave
    the model-default `compress-force=zstd` in the capture instead of what
    the machine actually reports."""
    declared = {
        "disks": [{
            **_DECLARED["disks"][0],
            "partitions": [
                _DECLARED["disks"][0]["partitions"][0],
                {**_DECLARED["disks"][0]["partitions"][1],
                 "btrfs_subvolumes": [
                     {"name": "@", "mountpoint": "/"},
                     {"name": "@home", "mountpoint": "/home/"},
                 ]},
            ],
        }]}
    action = DiskPartitionAction(declared, ActionContext(target=Target(root="/")))
    with patch.object(DiskPartitionAction, "_findmnt_btrfs_rows", return_value=_ROWS), \
         patch("dasik.lib.actions.disk_partition_action.Command.execute",
               side_effect=FileNotFoundError("no cryptsetup here")):
        captured = action.import_state(managed=[])

    part = _root_partition(captured)
    home = next(s for s in part["btrfs_subvolumes"] if s["name"] == "@home")
    assert home["mount_options"] == []   # compress-force is the partition base


def test_a_foreign_filesystem_at_the_declared_path_is_not_read_as_the_subvolume():
    """NIT-5 (re-review round 2): the correction is keyed by TARGET only (S1);
    it must not also trust that whatever is mounted there IS the declared
    partition's subvolume. If the SOURCE resolvable from the declaration (the
    LUKS mapper name here) disagrees with what findmnt reports at that path,
    the declaration stands -- nothing is invented from a filesystem that
    happens to share the mountpoint."""
    rows = list(_ROWS)
    # Same target ("/"), but mounted from a DIFFERENT device than the
    # declared LUKS mapping (cryptroot) -- e.g. a foreign fs bind-mounted at
    # the same path.
    rows[0] = ("/", "/dev/mapper/some-other-luks[/@]",
               "rw,relatime,nodatacow,space_cache=v2,subvolid=256,subvol=/@")
    part = _root_partition(_captured(rows))

    root_sv = next(s for s in part["btrfs_subvolumes"] if s["name"] == "@")
    # Unchanged: the declaration stands (here, the model's own default —
    # nothing was corrected because the source at "/" does not match this
    # partition's LUKS mapping).
    assert root_sv["mount_options"] == ["compress-force=zstd"]
    assert "nodatacow" not in root_sv["mount_options"]


def test_a_clamped_spelling_stays_declared_and_recaptures_silently():
    """SF-2 (re-review round 2): a declared `zstd:16` and a live (clamped)
    `zstd:15` are the SAME setting -- the capture must keep the user's own
    spelling (`zstd:16`), not overwrite it with what findmnt reports, and the
    subvolume's own list must stay empty (nothing "new" to capture)."""
    declared = {
        "disks": [{
            **_DECLARED["disks"][0],
            "partitions": [
                _DECLARED["disks"][0]["partitions"][0],
                {**_DECLARED["disks"][0]["partitions"][1],
                 "mount_options": ["compress-force=zstd:16"]},
            ],
        }]}
    rows = [
        ("/", "/dev/mapper/cryptroot[/@]",
         "rw,relatime,compress-force=zstd:15,space_cache=v2,subvolid=256,subvol=/@"),
        ("/home", "/dev/mapper/cryptroot[/@home]",
         "rw,relatime,compress-force=zstd:15,space_cache=v2,subvolid=257,subvol=/@home"),
    ]
    action = DiskPartitionAction(declared, ActionContext(target=Target(root="/")))
    with patch.object(DiskPartitionAction, "_findmnt_btrfs_rows", return_value=rows), \
         patch("dasik.lib.actions.disk_partition_action.Command.execute",
               side_effect=FileNotFoundError("no cryptsetup here")):
        captured = action.import_state(managed=[])

    part = _root_partition(captured)
    assert part["mount_options"] == ["compress-force=zstd:16"]   # user's own spelling kept
    root_sv = next(s for s in part["btrfs_subvolumes"] if s["name"] == "@")
    assert root_sv["mount_options"] == []   # nothing new to capture -- same setting

    # And the captured config re-derives the SAME rootflags= literal it was
    # captured with -- a `plan` off this capture would compare that literal
    # against the same live token again and stay silent (no apply needed).
    derived = KernelCmdlineAction(captured, None)._derived()
    rootflags = [t for t in derived if t.startswith("rootflags=")]
    assert rootflags == ["rootflags=compress-force=zstd:16,subvol=@"]


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

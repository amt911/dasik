"""fstab lines for an encrypted disk kept across a reinstall.

The install writes /etc/fstab from ``genfstab -U /mnt``, which sees only what is
mounted under /mnt. A preserved LUKS volume (``wipe_disk: false``,
``format: false``) is closed on the live ISO — nothing opens it — so its
mountpoints never reached the installed fstab: the disk was unlocked at boot
(``rd.luks.name``) and then mounted nowhere.

The mapper path dasik itself names (``rd.luks.name=<uuid>=<luks_name>``, the
crypttab line) is stable, so the lines are written WITHOUT unlocking the volume.
"""
from types import SimpleNamespace
from unittest.mock import patch

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.base_install_action import BaseInstallAction
from dasik.lib.actions.preserved_fstab import preserved_fstab_lines
from dasik.lib.target.target import Target


def _games(**over):
    part = {"label": "Games", "filesystem": "btrfs", "format": False,
            "encrypt": True, "luks_name": "games", "luks_uuid": "u-1",
            "mount_options": ["compress-force=zstd:3"],
            "btrfs_subvolumes": [
                {"name": "@", "mountpoint": "/home/andres/Games", "mount_options": []},
                {"name": "@snapshots", "mountpoint": "/home/andres/Games/.snapshots",
                 "mount_options": ["noatime"]},
            ]}
    part.update(over)
    return part


def _cfg(*parts, wipe=False):
    return {"disks": {"disks": [{"device": "/dev/vdb", "wipe_disk": wipe,
                                 "partitions": list(parts)}]}}


def test_every_subvolume_of_a_preserved_volume_gets_its_mapper_line():
    assert preserved_fstab_lines(_cfg(_games()), "") == [
        "/dev/mapper/games /home/andres/Games btrfs compress-force=zstd:3,subvol=@ 0 0",
        "/dev/mapper/games /home/andres/Games/.snapshots btrfs "
        "compress-force=zstd:3,noatime,subvol=@snapshots 0 0",
    ]


def test_a_mountpoint_genfstab_already_wrote_is_not_doubled():
    """The user opened and mounted it by hand before installing: genfstab has it."""
    fstab = "UUID=x /home/andres/Games btrfs rw,subvol=/@ 0 0\n"
    lines = preserved_fstab_lines(_cfg(_games()), fstab)
    assert [ln.split()[1] for ln in lines] == ["/home/andres/Games/.snapshots"]


def test_a_plain_filesystem_on_a_preserved_volume():
    part = _games(filesystem="ext4", btrfs_subvolumes=[], mount_options=[],
                  mountpoint="/data")
    assert preserved_fstab_lines(_cfg(part), "") == [
        "/dev/mapper/games /data ext4 defaults 0 2"]


def test_nothing_for_volumes_the_install_formats_or_mounts_itself():
    assert preserved_fstab_lines(_cfg(_games(format=True)), "") == []
    assert preserved_fstab_lines(_cfg(_games(), wipe=True), "") == []
    assert preserved_fstab_lines(_cfg(_games(encrypt=False)), "") == []
    assert preserved_fstab_lines({}, "") == []


def test_swap_and_root_are_never_written_here():
    swap = _games(filesystem="swap", btrfs_subvolumes=[], mountpoint=None)
    root = _games(btrfs_subvolumes=[{"name": "@", "mountpoint": "/"}])
    assert preserved_fstab_lines(_cfg(swap, root), "") == []


def test_install_appends_the_preserved_lines_after_genfstab(tmp_path):
    (tmp_path / "etc").mkdir(parents=True)
    cfg = {"enable_microcode": False, **_cfg(_games())}
    action = BaseInstallAction(cfg, ActionContext(target=Target(root=str(tmp_path))))
    gen = b"UUID=abc / btrfs rw,subvol=/@ 0 0\n"

    def fake(cmd, args, **kw):
        return SimpleNamespace(returncode=0, stdout=gen if cmd == "genfstab" else b"")

    with patch("dasik.lib.actions.base_install_action.Command.execute", side_effect=fake):
        action._install()
    text = (tmp_path / "etc" / "fstab").read_text()
    assert text.startswith(gen.decode())
    assert ("/dev/mapper/games /home/andres/Games btrfs "
            "compress-force=zstd:3,subvol=@ 0 0\n") in text


# --- day 2: BtrfsSubvolumeAction must recognise the mapper lines ---------- #

from dasik.lib.actions.btrfs_subvol_fstab import without_btrfs_lines


def test_adopting_a_subvolume_drops_its_mapper_line_elsewhere():
    """A subvolume written as /dev/mapper/<name> by the install, adopted at a
    new mountpoint, must not keep its old line (it would be mounted twice)."""
    text = ("UUID=r / btrfs subvol=@ 0 0\n"
            "/dev/mapper/games /old btrfs compress-force=zstd:3,subvol=@snapshots 0 0\n")
    kept = without_btrfs_lines(text, "/new", "@snapshots", "fs-uuid",
                               device="/dev/mapper/games")
    assert kept == ["UUID=r / btrfs subvol=@ 0 0"]


def test_the_mapper_match_is_per_device():
    text = "/dev/mapper/other /old btrfs subvol=@snapshots 0 0\n"
    assert without_btrfs_lines(text, "/new", "@snapshots", "fs-uuid",
                               device="/dev/mapper/games") == text.splitlines()


def test_btrfs_subvolume_action_passes_the_device_through(tmp_path):
    from dasik.lib.actions.btrfs_subvolume_action import BtrfsSubvolumeAction
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "fstab").write_text(
        "/dev/mapper/games /old btrfs subvol=@snapshots 0 0\n")
    action = BtrfsSubvolumeAction({}, ActionContext(target=Target(root=str(tmp_path))))
    new = "UUID=fs /new btrfs subvol=@snapshots 0 0"
    action._fstab_set("/new", new, "@snapshots", "fs", "/dev/mapper/games")
    assert (tmp_path / "etc" / "fstab").read_text() == new + "\n"

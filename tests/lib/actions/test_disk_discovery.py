"""Live disk-layout discovery for `sync` from an empty seed (best-effort, all
disks, skip unrepresentable filesystems, always non-destructive)."""
from unittest.mock import patch, MagicMock

from dasik.lib.actions.disk_partition_action import DiskPartitionAction
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.models.disk_model import DiskLayout


def _ctx(root="/"):
    return ActionContext(target=Target(root=root))


# --- pure mapping helpers ------------------------------------------------

def test_map_fs_known_and_unknown():
    assert DiskPartitionAction._map_fs("vfat") == "fat32"
    assert DiskPartitionAction._map_fs("btrfs") == "btrfs"
    assert DiskPartitionAction._map_fs("ntfs") is None
    assert DiskPartitionAction._map_fs(None) is None
    assert DiskPartitionAction._map_fs("crypto_LUKS") is None   # handled elsewhere


def test_map_ptype():
    assert DiskPartitionAction._map_ptype("EFI System") == "esp"
    assert DiskPartitionAction._map_ptype("Linux swap") == "linux-swap"
    assert DiskPartitionAction._map_ptype("Linux filesystem") == "linux"
    assert DiskPartitionAction._map_ptype(None) == "linux"


def test_bytes_to_size_whole_mib():
    assert DiskPartitionAction._bytes_to_size(512 * 1024 * 1024) == "512MiB"
    assert DiskPartitionAction._bytes_to_size(0) == "1MiB"   # never zero (model rejects)


def test_safe_label_prefers_valid_candidate():
    assert DiskPartitionAction._safe_label(["boot"], "sda1", set()) == "boot"


def test_safe_label_rejects_spaces_falls_back_to_devname():
    # a real ntfs label "Disco 1TB WD" has spaces -> synthesize from device name
    assert DiskPartitionAction._safe_label(["Disco 1TB WD"], "sda2", set()) == "sda2"


def test_role_label_by_mount_and_type():
    rl = DiskPartitionAction._role_label
    assert rl("linux", ["/"], "btrfs") == "root"
    assert rl("esp", ["/boot"], "fat32") == "boot"
    assert rl("linux", ["/home"], "ext4") == "home"
    assert rl("esp", [None], "fat32") == "esp"           # ESP, no mountpoint
    assert rl("linux-swap", [None], "swap") == "swap"
    assert rl("linux", [None], "ext4") == "part"         # nothing to go on


def test_discovery_synthesizes_role_labels_not_device_names():
    # Two unlabeled ESPs (one at /boot) + an unlabeled btrfs root with subvols:
    # labels come out role-based, not 'nvme0n1p1'/'nvme0n1p5'.
    tree = [{"name": "nvme0n1", "path": "/dev/nvme0n1", "type": "disk", "pttype": "gpt",
             "children": [
                 {"name": "nvme0n1p1", "path": "/dev/nvme0n1p1", "type": "part",
                  "fstype": "vfat", "size": 200 * 1024**2, "parttypename": "EFI System"},
                 {"name": "nvme0n1p5", "path": "/dev/nvme0n1p5", "type": "part",
                  "fstype": "vfat", "size": 1024 * 1024**2, "parttypename": "EFI System",
                  "mountpoint": "/boot"},
             ]}]
    frag = _discover(tree)
    labels = [p["label"] for p in frag["disks"]["disks"][0]["partitions"]]
    assert labels == ["esp", "boot"]      # not nvme0n1p1 / nvme0n1p5


def test_safe_label_dedups():
    used = {"root"}
    got = DiskPartitionAction._safe_label(["root"], "root", used)
    assert got != "root" and got.startswith("root")


# --- fixtures resembling the user's real lsblk tree ----------------------

def _tree():
    return [
        {"name": "sda", "path": "/dev/sda", "type": "disk", "pttype": "gpt",
         "children": [
             {"name": "sda1", "path": "/dev/sda1", "type": "part",
              "fstype": None, "size": 16 * 1024 * 1024, "parttypename": "BIOS boot"},
             {"name": "sda2", "path": "/dev/sda2", "type": "part",
              "fstype": "ntfs", "label": "Disco 1TB WD", "size": 731 * 1024**3,
              "parttypename": "Microsoft basic data"},
             {"name": "sda3", "path": "/dev/sda3", "type": "part",
              "fstype": "crypto_LUKS", "size": 200 * 1024**3,
              "parttypename": "Linux filesystem",
              "children": [{"name": "cryptdata", "path": "/dev/mapper/cryptdata",
                            "type": "crypt", "fstype": "btrfs", "label": "DATA",
                            "mountpoint": "/data"}]},
         ]},
        {"name": "nvme0n1", "path": "/dev/nvme0n1", "type": "disk", "pttype": "gpt",
         "children": [
             {"name": "nvme0n1p1", "path": "/dev/nvme0n1p1", "type": "part",
              "fstype": "vfat", "partlabel": "EFI", "size": 512 * 1024**2,
              "parttypename": "EFI System", "mountpoint": "/boot"},
             {"name": "nvme0n1p2", "path": "/dev/nvme0n1p2", "type": "part",
              "fstype": "crypto_LUKS", "size": 400 * 1024**3,
              "parttypename": "Linux filesystem",
              "children": [{"name": "cryptroot", "path": "/dev/mapper/cryptroot",
                            "type": "crypt", "fstype": "btrfs", "mountpoint": "/"}]},
         ]},
    ]


def _no_crypt(cmd, args=None, *rest, **kw):
    return MagicMock(stdout=b"", returncode=1)


def _discover(tree, findmnt=None, crypt=_no_crypt):
    a = DiskPartitionAction({}, _ctx("/"))
    with patch.object(DiskPartitionAction, "_lsblk_tree", return_value=tree), \
         patch.object(DiskPartitionAction, "_findmnt_btrfs_rows", return_value=findmnt or []), \
         patch("dasik.lib.actions.disk_partition_action.Command.execute", side_effect=crypt):
        return a.import_state(managed=[])


def test_discovery_skips_unrepresentable_and_captures_rest():
    frag = _discover(_tree())
    disks = frag["disks"]["disks"]
    devs = {d["device"] for d in disks}
    assert devs == {"/dev/sda", "/dev/nvme0n1"}

    sda = next(d for d in disks if d["device"] == "/dev/sda")
    # sda1 (no fs) and sda2 (ntfs) skipped; only the LUKS/btrfs partition survives
    # (its label comes from the decrypted fs label "DATA")
    labels = [p["label"] for p in sda["partitions"]]
    assert labels == ["DATA"]
    assert sda["partitions"][0]["encrypt"] is True
    assert sda["partitions"][0]["luks_name"] == "cryptdata"
    assert sda["partitions"][0]["filesystem"] == "btrfs"


def test_discovery_is_non_destructive():
    frag = _discover(_tree())
    for d in frag["disks"]["disks"]:
        assert d["wipe_disk"] is False
        assert all(p["format"] is False for p in d["partitions"])


def test_discovery_maps_esp_and_mountpoint():
    frag = _discover(_tree())
    nvme = next(d for d in frag["disks"]["disks"] if d["device"] == "/dev/nvme0n1")
    esp = nvme["partitions"][0]
    assert esp["filesystem"] == "fat32"
    assert esp["partition_type"] == "esp"
    assert esp["mountpoint"] == "/boot"
    assert esp["label"] == "EFI"


def test_discovery_captures_luks_uuid_and_tokens():
    def crypt(cmd, args=None, *rest, **kw):
        if cmd == "cryptsetup" and args and args[0] == "status":
            return MagicMock(stdout=b"  device: /dev/nvme0n1p2\n", returncode=0)
        if cmd == "cryptsetup" and args and args[0] == "luksUUID":
            return MagicMock(stdout=b"aaaa-bbbb\n", returncode=0)
        if cmd == "cryptsetup" and args and args[0] == "luksDump":
            return MagicMock(stdout=b"Tokens:\n  0: systemd-fido2\n", returncode=0)
        return MagicMock(stdout=b"", returncode=0)
    frag = _discover(_tree(), crypt=crypt)
    nvme = next(d for d in frag["disks"]["disks"] if d["device"] == "/dev/nvme0n1")
    root = next(p for p in nvme["partitions"] if p.get("encrypt"))
    assert root["luks_uuid"] == "aaaa-bbbb"
    assert root["unlock_fido2"] is True


def test_discovery_captures_btrfs_subvolumes():
    findmnt = [
        ("/", "/dev/mapper/cryptroot[/@]", "rw,compress-force=zstd,subvol=/@"),
        ("/home", "/dev/mapper/cryptroot[/@home]", "rw,compress-force=zstd,subvol=/@home"),
    ]
    frag = _discover(_tree(), findmnt=findmnt)
    nvme = next(d for d in frag["disks"]["disks"] if d["device"] == "/dev/nvme0n1")
    root = next(p for p in nvme["partitions"] if p.get("encrypt"))
    names = {s["name"] for s in root["btrfs_subvolumes"]}
    assert names == {"@", "@home"}
    home = next(s for s in root["btrfs_subvolumes"] if s["name"] == "@home")
    assert home["mountpoint"] == "/home"
    assert "compress-force=zstd" in home["mount_options"]


def test_discovery_omits_disk_with_no_representable_partitions():
    tree = [{"name": "sdb", "path": "/dev/sdb", "type": "disk", "pttype": "gpt",
             "children": [
                 {"name": "sdb1", "path": "/dev/sdb1", "type": "part",
                  "fstype": "ntfs", "size": 100 * 1024**3, "parttypename": "x"}]}]
    assert _discover(tree) == {}


def test_discovery_skips_locked_luks():
    # crypto_LUKS with no open `crypt` child -> inner fs unknown -> skip
    tree = [{"name": "sdc", "path": "/dev/sdc", "type": "disk", "pttype": "gpt",
             "children": [
                 {"name": "sdc1", "path": "/dev/sdc1", "type": "part",
                  "fstype": "crypto_LUKS", "size": 100 * 1024**3,
                  "parttypename": "Linux filesystem"}]}]
    assert _discover(tree) == {}


def test_discovered_layout_validates_through_model():
    frag = _discover(_tree())
    for d in frag["disks"]["disks"]:
        DiskLayout.model_validate(d)     # must not raise


def test_discovered_layout_reapplies_as_noop():
    # feeding the captured stanza back must plan to nothing on a matching disk
    frag = _discover(_tree())
    disks = frag["disks"]["disks"]
    b = DiskPartitionAction({"disks": disks}, _ctx("/"))
    labels = {p["label"] for d in disks for p in d["partitions"]}
    with patch.object(DiskPartitionAction, "_device_labels", return_value=labels):
        assert b.plan(managed=[d["device"] for d in disks]) == []

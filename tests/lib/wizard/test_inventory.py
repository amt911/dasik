"""What the wizard shows you before you choose: the disks as they really are.

Deliberately NOT `DiskPartitionAction._discover_disks`, which answers a
different question — "what can dasik represent?" — and drops everything it
cannot (ntfs, unformatted, locked LUKS). The wizard has to show exactly those,
because "this disk has Windows on it and is mounted" is the single most
important thing a partitioning assistant can tell you.

Recorded `lsblk -J` output, so this is testable without a disk.
"""
import json

from dasik.lib.wizard.inventory import DiskInfo, human_size, parse_lsblk

# An empty NVMe: no partition table at all.
_EMPTY = {"blockdevices": [
    {"name": "nvme0n1", "path": "/dev/nvme0n1", "type": "disk", "size": 1000204886016,
     "pttype": None, "fstype": None, "mountpoint": None},
]}

# A disk with Windows on it, mounted.
_WINDOWS = {"blockdevices": [
    {"name": "sda", "path": "/dev/sda", "type": "disk", "size": 4000787030016,
     "pttype": "gpt", "children": [
         {"name": "sda1", "path": "/dev/sda1", "type": "part", "size": 104857600,
          "fstype": "vfat", "label": "SYSTEM", "mountpoint": None},
         {"name": "sda2", "path": "/dev/sda2", "type": "part", "size": 3999000000000,
          "fstype": "ntfs", "label": "Windows", "mountpoint": "/run/media/andres/Windows"},
     ]},
]}

# An installed Arch: ESP + a LUKS container holding btrfs.
_LUKS = {"blockdevices": [
    {"name": "vda", "path": "/dev/vda", "type": "disk", "size": 8589934592,
     "pttype": "gpt", "children": [
         {"name": "vda1", "path": "/dev/vda1", "type": "part", "size": 536870912,
          "fstype": "vfat", "label": "ESP", "mountpoint": "/boot"},
         {"name": "vda2", "path": "/dev/vda2", "type": "part", "size": 8000000000,
          "fstype": "crypto_LUKS", "label": None, "mountpoint": None, "children": [
              {"name": "cryptroot", "path": "/dev/mapper/cryptroot", "type": "crypt",
               "fstype": "btrfs", "label": "root", "mountpoint": "/"}]},
     ]},
]}

# Loop devices and CD-ROMs are not disks you install onto.
_NOISE = {"blockdevices": [
    {"name": "loop0", "path": "/dev/loop0", "type": "loop", "size": 123456},
    {"name": "sr0", "path": "/dev/sr0", "type": "rom", "size": 1000000000},
    {"name": "vda", "path": "/dev/vda", "type": "disk", "size": 8589934592, "pttype": None},
]}


def test_an_empty_disk_is_reported_as_empty():
    disks = parse_lsblk(_EMPTY)

    assert len(disks) == 1
    disk = disks[0]
    assert disk.path == "/dev/nvme0n1"
    assert disk.partitions == ()
    assert disk.is_empty is True
    assert disk.is_mounted is False


def test_a_disk_with_windows_reports_its_partitions_and_that_it_is_mounted():
    disk = parse_lsblk(_WINDOWS)[0]

    assert disk.is_empty is False
    assert disk.is_mounted is True
    assert [p.fstype for p in disk.partitions] == ["vfat", "ntfs"]
    assert disk.partitions[1].label == "Windows"


def test_a_locked_or_open_luks_container_is_visible():
    """`_discover_disks` skips these; the wizard must not — it is exactly the
    disk somebody is about to erase by mistake."""
    disk = parse_lsblk(_LUKS)[0]

    assert [p.fstype for p in disk.partitions] == ["vfat", "crypto_LUKS"]
    assert disk.is_mounted is True          # the ESP is at /boot


def test_loop_and_rom_devices_are_not_offered():
    assert [d.path for d in parse_lsblk(_NOISE)] == ["/dev/vda"]


def test_a_missing_or_broken_payload_is_no_disks():
    assert parse_lsblk({}) == []
    assert parse_lsblk({"blockdevices": None}) == []


def test_the_partition_table_is_reported():
    assert parse_lsblk(_WINDOWS)[0].pttype == "gpt"
    assert parse_lsblk(_EMPTY)[0].pttype == ""


def test_a_string_size_from_lsblk_without_b_is_tolerated():
    """`lsblk -J` without `-b` reports sizes as '931.5G'. The wizard asks for
    bytes, but a recorded payload from a human's terminal should not crash it."""
    data = {"blockdevices": [{"name": "sda", "path": "/dev/sda", "type": "disk",
                              "size": "931.5G", "pttype": "gpt"}]}

    disk = parse_lsblk(data)[0]

    assert disk.size == 0                   # unknown, not a crash
    assert "931.5G" in disk.size_human or disk.size_human == "?"


def test_human_size_reads_like_lsblk():
    assert human_size(536870912) == "512M"
    assert human_size(1000204886016) == "931.5G"
    assert human_size(0) == "?"


def test_describe_says_what_matters_at_a_glance():
    empty = parse_lsblk(_EMPTY)[0]
    windows = parse_lsblk(_WINDOWS)[0]

    assert "empty" in empty.describe()
    assert "931.5G" in empty.describe()
    assert "ntfs" in windows.describe()
    assert "MOUNTED" in windows.describe()


def test_disk_info_is_hashable_and_frozen():
    """The TUI keeps them in menus and compares them; a mutable row that a
    screen can edit is a bug waiting to happen."""
    disk = parse_lsblk(_EMPTY)[0]

    assert isinstance(disk, DiskInfo)
    assert hash(disk) == hash(parse_lsblk(_EMPTY)[0])


def test_round_trips_from_a_real_lsblk_dump():
    """The shape `lsblk -J -b -o NAME,PATH,TYPE,FSTYPE,LABEL,SIZE,MOUNTPOINT,PTTYPE`
    produces, which is what the inventory runs."""
    raw = json.dumps(_LUKS)

    disks = parse_lsblk(json.loads(raw))

    assert disks[0].size_human == "8G"


# --- what is not an install target ------------------------------------------ #

def test_a_floppy_is_not_offered():
    """QEMU gives every guest a /dev/fd0 of 4096 bytes, and it sorts FIRST — so
    the disk menu opened with a floppy selected and the wizard would happily
    have composed an ESP for it. Seen on a real VM run."""
    data = {"blockdevices": [
        {"name": "fd0", "path": "/dev/fd0", "type": "disk", "size": 4096},
        {"name": "vda", "path": "/dev/vda", "type": "disk", "size": 8589934592},
    ]}

    assert [d.path for d in parse_lsblk(data)] == ["/dev/vda"]


def test_a_card_reader_with_no_media_is_not_offered():
    """Size 0 means there is nothing in the slot."""
    data = {"blockdevices": [
        {"name": "mmcblk0", "path": "/dev/mmcblk0", "type": "disk", "size": 0},
        {"name": "vda", "path": "/dev/vda", "type": "disk", "size": 8589934592},
    ]}

    assert [d.path for d in parse_lsblk(data)] == ["/dev/vda"]


def test_a_small_but_real_usb_stick_is_still_offered():
    """The floor is for pseudo-devices, not for people with small disks."""
    data = {"blockdevices": [
        {"name": "sdb", "path": "/dev/sdb", "type": "disk", "size": 2 * 1024 ** 3},
    ]}

    assert [d.path for d in parse_lsblk(data)] == ["/dev/sdb"]

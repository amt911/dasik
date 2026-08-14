"""Disk declarations that cannot describe a working machine.

dasik keys its partition map by LABEL, derives `root=LABEL=…` from it, and opens
every LUKS volume at /dev/mapper/<luks_name>. None of that was checked, so these
all installed (or tried to) in silence:

  * the same label on two disks — the map keeps one, and `root=LABEL=ROOT` is
    ambiguous to the kernel too;
  * two volumes with the same `luks_name` — both want /dev/mapper/cryptroot;
  * two partitions mounted at the same path — one shadows the other;
  * no partition mounted at `/` — nothing to install onto;
  * `encrypt: true` with no passphrase and no key device — `cryptsetup
    luksFormat` has nothing to enroll, and an unattended install has nobody to
    ask.

Errors, not warnings: each one describes a machine that cannot come out right,
and preflight errors abort before the first mutation — which for this domain is
the difference between a message and a partitioned disk.
"""
from dasik.lib.validation.preflight import has_errors, preflight


def _part(label, **kw):
    return {"label": label, "size": kw.pop("size", "rest"),
            "filesystem": kw.pop("filesystem", "ext4"),
            "partition_type": kw.pop("partition_type", "linux"), **kw}


def _issues(disks, code=None):
    cfg = {"packages": ["base"], "disks": {"disks": disks}}
    out = preflight(cfg, efi_boot=True)
    return [i for i in out if code is None or i.code == code]


def _root_disk(extra=None):
    parts = [_part("ESP", size="512MiB", filesystem="fat32",
                   partition_type="esp", mountpoint="/boot"),
             _part("ROOT", mountpoint="/")]
    return [{"device": "/dev/vda", "partition_table": "gpt",
             "partitions": parts + (extra or [])}]


def test_a_sane_layout_is_quiet():
    assert _issues(_root_disk()) == []


def test_the_same_label_on_two_disks_is_an_error():
    disks = _root_disk() + [{"device": "/dev/vdb", "partition_table": "gpt",
                             "partitions": [_part("ROOT", mountpoint="/data")]}]

    issues = _issues(disks, "duplicate_partition_label")

    assert [i.level for i in issues] == ["error"]
    assert "ROOT" in issues[0].message


def test_two_volumes_cannot_share_a_luks_name():
    disks = _root_disk([_part("DATA", encrypt=True, luks_name="cryptroot",
                              luks_password="x", mountpoint="/data")])
    disks[0]["partitions"][1].update(encrypt=True, luks_name="cryptroot", luks_password="x")

    issues = _issues(disks, "duplicate_luks_name")

    assert [i.level for i in issues] == ["error"]
    assert "cryptroot" in issues[0].message


def test_two_partitions_cannot_live_at_one_path():
    disks = _root_disk([_part("OTHER", mountpoint="/")])

    issues = _issues(disks, "duplicate_mountpoint")

    assert [i.level for i in issues] == ["error"]
    assert "/" in issues[0].message


def test_a_layout_with_no_root_is_flagged_but_not_fatal():
    """A config that only describes extra disks on an existing machine is a real
    shape — config/disk-preserve-existing.json is one — so this warns."""
    disks = [{"device": "/dev/vda", "partition_table": "gpt",
              "partitions": [_part("DATA", mountpoint="/data")]}]

    assert [i.level for i in _issues(disks, "no_root_partition")] == ["warning"]


def test_a_btrfs_root_on_a_subvolume_counts_as_root():
    """The subvolume carries the mountpoint; the partition's is null."""
    disks = [{"device": "/dev/vda", "partition_table": "gpt", "partitions": [
        _part("ESP", size="512MiB", filesystem="fat32", partition_type="esp",
              mountpoint="/boot"),
        _part("ROOT", filesystem="btrfs",
              btrfs_subvolumes=[{"name": "@", "mountpoint": "/"},
                                {"name": "@home", "mountpoint": "/home"}])]}]

    assert _issues(disks, "no_root_partition") == []


def test_encryption_with_nothing_to_unlock_it_is_an_error():
    disks = _root_disk()
    disks[0]["partitions"][1].update(encrypt=True, luks_name="cryptroot")

    issues = _issues(disks, "encryption_without_a_key")

    assert [i.level for i in issues] == ["warning"]
    assert "ROOT" in issues[0].message


def test_a_key_device_counts_as_a_key():
    disks = _root_disk()
    disks[0]["partitions"][1].update(encrypt=True, luks_name="cryptroot",
                                     unlock_keydev="/dev/disk/by-uuid/1234-ABCD",
                                     unlock_keyfile="/keyfile")

    assert _issues(disks, "encryption_without_a_key") == []


def test_a_config_with_no_disks_block_is_quiet():
    assert [i for i in preflight({"packages": ["base"]}, efi_boot=True)
            if i.code in ("no_root_partition", "duplicate_partition_label")] == []


def test_a_duplicate_is_an_error_so_nothing_is_partitioned_first():
    """The unambiguous mistakes abort before the first mutation; the judgement
    calls (no root, no key) only warn."""
    disks = _root_disk() + [{"device": "/dev/vdb", "partition_table": "gpt",
                             "partitions": [_part("ROOT", mountpoint="/data")]}]

    assert has_errors(preflight({"packages": ["base"], "disks": {"disks": disks}},
                                efi_boot=True))


def test_a_btrfs_partition_and_its_own_subvolume_are_one_filesystem():
    """The shape dasik itself writes: the partition's mountpoint IS the one the
    `@` subvolume carries. Counting both flagged every btrfs layout."""
    disks = [{"device": "/dev/vda", "partition_table": "gpt", "partitions": [
        _part("ESP", size="512MiB", filesystem="fat32", partition_type="esp",
              mountpoint="/boot"),
        _part("ROOT", filesystem="btrfs", mountpoint="/",
              btrfs_subvolumes=[{"name": "@", "mountpoint": "/"},
                                {"name": "@home", "mountpoint": "/home"}])]}]

    assert _issues(disks, "duplicate_mountpoint") == []

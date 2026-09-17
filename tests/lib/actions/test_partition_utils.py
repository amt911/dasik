from dasik.lib.actions.partition_utils import (
    merge_mount_options_by_name,
    mounts_root,
    normalize_compress_value,
    option_equivalent,
    token_name,
)


def test_partition_mountpoint_root():
    assert mounts_root({"mountpoint": "/"}) is True


def test_subvolume_mounts_root():
    assert mounts_root({"mountpoint": None, "btrfs_subvolumes": [
        {"name": "@", "mountpoint": "/"}, {"name": "@home", "mountpoint": "/home"}]}) is True


def test_no_root_partition_or_subvol():
    assert mounts_root({"mountpoint": "/boot"}) is False
    assert mounts_root({"mountpoint": None, "btrfs_subvolumes": [
        {"name": "@home", "mountpoint": "/home"}]}) is False


def test_empty_or_missing_subvolumes():
    assert mounts_root({"mountpoint": None}) is False
    assert mounts_root({"mountpoint": None, "btrfs_subvolumes": []}) is False
    assert mounts_root({}) is False


# --- token_name (N4: the shared name/value splitter, not a mount-option alias) --- #

def test_token_name_splits_on_first_equals():
    assert token_name("compress-force=zstd:3") == "compress-force"
    assert token_name("rootflags=compress-force=zstd,subvol=@") == "rootflags"


def test_token_name_of_a_bare_flag_is_itself():
    assert token_name("noatime") == "noatime"
    assert token_name("rw") == "rw"


# --- normalize_compress_value ------------------------------------------------ #

def test_normalize_compress_fills_the_kernel_default_level():
    assert normalize_compress_value("zstd") == "zstd:3"
    assert normalize_compress_value("zlib") == "zlib:3"


def test_normalize_compress_leaves_an_explicit_level_untouched():
    assert normalize_compress_value("zstd:1") == "zstd:1"


def test_normalize_compress_lzo_has_no_default_to_fill():
    assert normalize_compress_value("lzo") == "lzo"


# --- option_equivalent (S4) --------------------------------------------------- #

def test_option_equivalent_bare_and_resolved_compress_force():
    assert option_equivalent("compress-force=zstd", "compress-force=zstd:3")


def test_option_equivalent_different_names_are_not_equivalent():
    assert not option_equivalent("compress=zstd", "compress-force=zstd")


def test_option_equivalent_different_explicit_levels_are_not_equivalent():
    assert not option_equivalent("compress-force=zstd:1", "compress-force=zstd:3")


def test_option_equivalent_non_compress_options_compare_as_exact_tokens():
    assert option_equivalent("noatime", "noatime")
    assert not option_equivalent("noatime", "nodiratime")


# --- merge_mount_options_by_name (S4) ----------------------------------------- #

def test_merge_by_name_override_replaces_base_in_place():
    merged = merge_mount_options_by_name(
        ["compress-force=zstd:3", "noatime"], ["compress-force=zstd"])
    assert merged == ["compress-force=zstd", "noatime"]   # in place, not appended


def test_merge_by_name_appends_a_genuinely_new_option():
    merged = merge_mount_options_by_name(["compress-force=zstd"], ["noatime"])
    assert merged == ["compress-force=zstd", "noatime"]


def test_merge_by_name_empty_overrides_keeps_base_unchanged():
    assert merge_mount_options_by_name(["compress-force=zstd"], []) == \
        ["compress-force=zstd"]

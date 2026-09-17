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


# --- SF-2 (re-review round 2): the measured clamp table (FACT-RFD-3), nothing
# more -- an unmeasured level (zstd:17+, zlib:10/11/13+, a negative level,
# lzo:N) is deliberately left untouched.

def test_normalize_compress_clamps_zstd_0_to_the_default_level():
    assert normalize_compress_value("zstd:0") == "zstd:3"


def test_normalize_compress_clamps_zstd_16_down_to_15():
    assert normalize_compress_value("zstd:16") == "zstd:15"


def test_normalize_compress_clamps_zlib_0_to_the_default_level():
    assert normalize_compress_value("zlib:0") == "zlib:3"


def test_normalize_compress_clamps_zlib_12_down_to_9():
    assert normalize_compress_value("zlib:12") == "zlib:9"


def test_normalize_compress_leaves_unmeasured_levels_untouched():
    """Not "what the kernel reports" was never measured for these -- an
    explicit zstd:17/zlib:13/negative level/lzo:N must not be normalized on
    a guess."""
    assert normalize_compress_value("zstd:17") == "zstd:17"
    assert normalize_compress_value("zlib:13") == "zlib:13"
    assert normalize_compress_value("zstd:-1") == "zstd:-1"
    assert normalize_compress_value("lzo:1") == "lzo:1"


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


def test_option_equivalent_clamped_zstd_level():
    assert option_equivalent("compress-force=zstd:16", "compress-force=zstd:15")


def test_option_equivalent_bare_compress_and_the_kernels_own_default_algorithm():
    """SF-2: a bare `compress`/`compress-force` (no `=` at all) resolves to
    the kernel's own default algorithm+level (`zlib:3`, FACT-RFD-3) -- zlib,
    not zstd."""
    assert option_equivalent("compress", "compress=zlib:3")
    assert option_equivalent("compress-force", "compress-force=zlib:3")
    assert not option_equivalent("compress", "compress=zstd:3")


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

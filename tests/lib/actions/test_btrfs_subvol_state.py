"""Pure parsing + diff for day-2 btrfs subvolumes (no IO)."""
from dasik.lib.actions.btrfs_subvol_state import (
    Live, MountRow, Wanted, diff, parse_findmnt, parse_fstab_subvols,
    parse_subvolume_list,
)
from dasik.lib.state.change import Op


def test_fstab_reads_genfstab_style_line():
    text = ("/dev/mapper/cryptroot /var/lib/ollama btrfs "
            "rw,noatime,compress-force=zstd:3,subvolid=262,subvol=/@var_lib_ollama 0 0")
    assert parse_fstab_subvols(text) == {"/var/lib/ollama": "@var_lib_ollama"}


def test_fstab_ignores_comments_and_non_btrfs():
    assert parse_fstab_subvols("# x\nUUID=a /boot vfat rw 0 2\n") == {}


def test_fstab_ignores_btrfs_line_without_subvol_and_blank_lines():
    text = "\n/dev/a /data btrfs rw,noatime 0 0\n   \n"
    assert parse_fstab_subvols(text) == {}


def test_fstab_ignores_commented_btrfs_line():
    assert parse_fstab_subvols("# /dev/a /x btrfs subvol=/@x 0 0\n") == {}
    # shaped so that, uncommented, it WOULD parse: field[2] is "btrfs"
    assert parse_fstab_subvols("# x btrfs subvol=/@x 0 0\n") == {}


def test_findmnt_strips_bracket_and_slash():
    text = "/ /dev/mapper/cryptroot[/@] 1111 rw,subvolid=256,subvol=/@\n"
    assert parse_findmnt(text) == [MountRow("/", "/dev/mapper/cryptroot", "1111", "@")]


def test_findmnt_decodes_space_escape_in_target():
    text = "/my\\x20data /dev/sda1[/@d] 2 rw,subvol=/@d\n"
    assert parse_findmnt(text) == [MountRow("/my data", "/dev/sda1", "2", "@d")]


def test_findmnt_row_without_subvol_option_is_skipped():
    assert parse_findmnt("/x /dev/sda1 2 rw,noatime\n\n") == []


def test_subvolume_list_keeps_only_top_level():
    text = ("ID 256 gen 9 top level 5 path @\n"
            "ID 300 gen 9 top level 256 path var/lib/ollama\n")
    assert parse_subvolume_list(text) == {"@"}


def test_subvolume_list_ignores_junk_lines():
    assert parse_subvolume_list("\nnot a line\nID 1 gen 2 top level 5 path @a b\n") == {"@a b"}


def _live(mounted=None, fstab=None, existing=None):
    return Live(mounted=mounted or {}, fstab=fstab or {}, existing=existing or set())


W = Wanted(key="root:@data", label="root", name="@data", mountpoint="/data")


def test_diff_converged_is_silent():
    live = _live({"@data": "/data"}, {"/data": "@data"}, {"@data"})
    assert diff([W], {"root": live}, {"root"}, []) == []


def test_diff_missing_subvolume_is_install_create():
    assert diff([W], {"root": _live()}, {"root"}, []) == [
        (Op.INSTALL, "root:@data", "create + move contents")]


def test_diff_existing_unmounted_is_adopt():
    live = _live(existing={"@data"})
    assert diff([W], {"root": live}, {"root"}, []) == [
        (Op.INSTALL, "root:@data", "adopt existing subvolume")]


def test_diff_missing_fstab_only():
    live = _live({"@data": "/data"}, {}, {"@data"})
    assert diff([W], {"root": live}, {"root"}, []) == [
        (Op.INSTALL, "root:@data", "add fstab line")]


def test_diff_fstab_names_other_subvolume_is_fstab_fix():
    live = _live({"@data": "/data"}, {"/data": "@other"}, {"@data"})
    assert diff([W], {"root": live}, {"root"}, []) == [
        (Op.INSTALL, "root:@data", "add fstab line")]


def test_diff_moved_mountpoint_is_modify():
    live = _live({"@data": "/old"}, {"/old": "@data"}, {"@data"})
    assert diff([W], {"root": live}, {"root"}, []) == [
        (Op.MODIFY, "root:@data", "move /old -> /data")]


def test_diff_owned_undeclared_is_remove():
    live = _live({}, {}, {"@gone"})
    assert diff([], {"root": live}, {"root"}, ["root:@gone"]) == [
        (Op.REMOVE, "root:@gone", "no longer declared")]


def test_diff_remove_of_mounted_nonroot_subvolume():
    live = _live({"@gone": "/gone"}, {"/gone": "@gone"}, {"@gone"})
    assert diff([], {"root": live}, {"root"}, ["root:@gone"]) == [
        (Op.REMOVE, "root:@gone", "no longer declared")]


def test_diff_unowned_undeclared_is_left_alone():
    live = _live({}, {}, {"@manual"})
    assert diff([], {"root": live}, {"root"}, []) == []


def test_diff_owned_but_still_wanted_is_not_removed():
    live = _live({"@data": "/data"}, {"/data": "@data"}, {"@data"})
    assert diff([W], {"root": live}, {"root"}, ["root:@data"]) == []


def test_diff_owned_but_already_gone_plans_nothing():
    assert diff([], {"root": _live()}, {"root"}, ["root:@gone"]) == []


def test_diff_dropped_partition_plans_no_remove():
    live = _live({}, {}, {"@gone"})
    assert diff([], {"root": live}, set(), ["root:@gone"]) == []


def test_diff_never_removes_root_subvolume():
    live = _live({"@": "/"}, {"/": "@"}, {"@"})
    assert diff([], {"root": live}, {"root"}, ["root:@"]) == []


def test_diff_unconverged_label_plans_nothing():
    assert diff([W], {}, {"root"}, ["root:@gone"]) == []


def test_diff_remove_only_for_matching_label():
    live = _live({}, {}, {"@gone"})
    assert diff([], {"other": live}, {"other"}, ["root:@gone"]) == []


def test_fstab_keeps_every_line_around_skipped_ones():
    text = ("# c\n\nUUID=a /boot vfat rw 0 2\nshort line\n"
            "/dev/a /a btrfs subvol=/@a\n"        # 4 fields only: still valid
            "/dev/a /b btrfs subvol=/@b 0 0\n")
    assert parse_fstab_subvols(text) == {"/a": "@a", "/b": "@b"}


def test_fstab_subvol_name_may_start_with_x():
    assert parse_fstab_subvols("/dev/a /x btrfs subvol=//Xdata 0 0") == {"/x": "Xdata"}


def test_findmnt_keeps_rows_around_skipped_ones():
    text = ("/a /dev/a[/@a] 1 rw,subvol=/@a\n"
            "/short /dev/a\n"
            "/nosub /dev/a 1 rw\n"
            "/b /dev/b[/@b] 2 rw,subvol=/@b\n")
    assert parse_findmnt(text) == [MountRow("/a", "/dev/a", "1", "@a"),
                                   MountRow("/b", "/dev/b", "2", "@b")]


def test_findmnt_decodes_upper_and_lower_hex_escapes():
    text = "/a\\x2Db\\x2dc /dev/a 1 rw,subvol=/@a\n"
    assert parse_findmnt(text)[0].target == "/a-b-c"


def test_findmnt_cuts_source_at_first_bracket():
    text = "/a /dev/a[/@a[x]] 1 rw,subvol=/@a\n"
    assert parse_findmnt(text)[0].device == "/dev/a"


def test_subvolume_list_keeps_lines_around_skipped_ones():
    text = "junk\nID 2 gen 1 top level 5 path @a\nID 3 gen 1 top level 2 path x\nID 4 gen 1 top level 5 path @b\n"
    assert parse_subvolume_list(text) == {"@a", "@b"}


def test_diff_keeps_going_after_unconverged_label_and_after_converged_wanted():
    w2 = Wanted("root:@b", "root", "@b", "/b")
    w3 = Wanted("other:@c", "other", "@c", "/c")
    live = _live({"@data": "/data"}, {"/data": "@data"}, {"@data"})
    assert diff([w3, W, w2], {"root": live}, {"root", "other"}, []) == [
        (Op.INSTALL, "root:@b", "create + move contents")]


def test_diff_remove_loop_keeps_going_past_skipped_entries():
    live = _live({}, {}, {"@gone"})
    managed = ["root:@data", "dropped:@x", "root:@absent", "root:@gone"]
    assert diff([W], {"root": live}, {"root"}, managed) == [
        (Op.INSTALL, "root:@data", "create + move contents"),
        (Op.REMOVE, "root:@gone", "no longer declared")]


def test_diff_remove_key_name_may_contain_colon():
    live = _live({}, {}, {"@a:b"})
    assert diff([], {"root": live}, {"root"}, ["root:@a:b"]) == [
        (Op.REMOVE, "root:@a:b", "no longer declared")]

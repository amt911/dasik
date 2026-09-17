"""`rootflags=` sync drift: `sync` -> `plan` was not silent on btrfs roots.

Two independent kernel facts collide on a btrfs root:

  - `sync` reads the machine's *live* mount options via findmnt, which reports
    the kernel's RESOLVED compression level (`compress-force=zstd` comes back
    `compress-force=zstd:3`) — so a captured config's derived `rootflags=`
    value never string-matches the boot entry it was captured FROM.
  - A changed `rootflags=`/`root=`/`rootfstype=` used to be INSTALLed
    alongside the old token instead of replacing it: two tokens with the same
    key end up on one entry, and the kernel takes the last — silently not
    what either one asked for.

Mocked throughout: no real disk, no real findmnt — a fake target root under
`tmp_path` with hand-written boot-entry text.
"""
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.kernel_cmdline_action import (
    KernelCmdlineAction,
    _rootflags_equivalent,
)
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target


_PLAIN = {"bootloader": "sd-boot", "kernel_cmdline": ["quiet"], "disks": {"disks": [{
    "device": "/dev/vda", "partition_table": "gpt",
    "partitions": [
        {"label": "root", "size": "rest", "filesystem": "btrfs",
         "partition_type": "linux", "mountpoint": None,
         "btrfs_subvolumes": [{"name": "@", "mountpoint": "/",
                               "mount_options": ["compress-force=zstd", "noatime"]}]},
    ]}]}}


def _entry(tmp_path, options):
    entries = tmp_path / "boot/loader/entries"
    entries.mkdir(parents=True, exist_ok=True)
    (entries / "arch.conf").write_text(f"title Arch\noptions {options}\n")
    (tmp_path / "boot/loader/loader.conf").write_text("default arch\n")
    return tmp_path


def _plan(tmp_path, config, managed=()):
    action = KernelCmdlineAction(config, ActionContext(target=Target(root=str(tmp_path))))
    return [(c.op.name, c.item) for c in action.plan(managed=list(managed))]


def _current_options(tmp_path):
    written = (tmp_path / "boot/loader/entries/arch.conf").read_text()
    line = next(l for l in written.splitlines() if l.startswith("options "))
    return line[len("options "):].split()


# --- the pure helper (root cause B: kernel compression-level normalization) - #

def test_bare_zstd_is_equivalent_to_the_kernel_default_level():
    assert _rootflags_equivalent("compress-force=zstd,subvol=@",
                                 "compress-force=zstd:3,subvol=@")


def test_bare_zlib_is_equivalent_to_the_kernel_default_level():
    assert _rootflags_equivalent("compress=zlib,subvol=@",
                                 "compress=zlib:3,subvol=@")


def test_an_explicit_different_level_is_not_equivalent():
    assert not _rootflags_equivalent("compress-force=zstd:1,subvol=@",
                                     "compress-force=zstd:3,subvol=@")


def test_compress_and_compress_force_are_not_equivalent():
    assert not _rootflags_equivalent("compress=zstd,subvol=@",
                                     "compress-force=zstd,subvol=@")


def test_option_order_does_not_matter():
    assert _rootflags_equivalent("noatime,compress-force=zstd:3,subvol=@",
                                 "compress-force=zstd,subvol=@,noatime")


def test_lzo_has_no_default_level_to_fill_in():
    assert _rootflags_equivalent("compress=lzo,subvol=@", "compress=lzo,subvol=@")
    assert not _rootflags_equivalent("compress=lzo,subvol=@", "compress=lzo:1,subvol=@")


def test_a_genuinely_different_mount_is_not_equivalent():
    assert not _rootflags_equivalent("compress-force=zstd:3,subvol=@",
                                     "compress-force=zstd:3,subvol=@home")


# --- plan(): the equivalence makes the drift silent -------------------------- #

def test_the_normalized_compression_level_does_not_replan(tmp_path):
    """The exact bug (evidence #1): a captured config derives `zstd:3` (what
    findmnt reports); the boot entry dasik itself wrote at install still says
    the bare `zstd` it was given. Same mount -> plan must be silent."""
    _entry(tmp_path, "root=LABEL=root rw rootflags=compress-force=zstd,noatime,subvol=@ quiet")

    planned = _plan(tmp_path, _PLAIN, managed=["root=LABEL=root", "rw", "quiet"])

    assert planned == []


def test_a_genuine_level_change_still_plans_a_replacement(tmp_path):
    config = {**_PLAIN, "disks": {"disks": [{
        **_PLAIN["disks"]["disks"][0],
        "partitions": [{
            **_PLAIN["disks"]["disks"][0]["partitions"][0],
            "btrfs_subvolumes": [{"name": "@", "mountpoint": "/",
                                  "mount_options": ["compress-force=zstd:1", "noatime"]}],
        }],
    }]}}
    _entry(tmp_path, "root=LABEL=root rw rootflags=compress-force=zstd,noatime,subvol=@ quiet")

    planned = _plan(tmp_path, config, managed=["root=LABEL=root", "rw", "quiet"])

    assert ("INSTALL", "rootflags=compress-force=zstd:1,noatime,subvol=@") in planned


def test_a_missing_rootflags_is_still_installed(tmp_path):
    """No live `rootflags=` to compare against — equivalence never kicks in
    without something on the other side."""
    _entry(tmp_path, "quiet")

    planned = _plan(tmp_path, _PLAIN, managed=["quiet"])

    assert any(op == "INSTALL" and item.startswith("rootflags=") for op, item in planned)


# --- apply(): replace, not duplicate (root cause C) -------------------------- #

def test_apply_replaces_rootflags_in_place(tmp_path):
    _entry(tmp_path, "root=LABEL=root rw rootflags=compress-force=zstd,noatime,subvol=@ quiet")
    action = KernelCmdlineAction({"bootloader": "sd-boot"},
                                 ActionContext(target=Target(root=str(tmp_path))))
    changes = [Change("kernel_cmdline", Op.INSTALL,
                      "rootflags=compress-force=zstd:1,noatime,subvol=@")]

    action.apply(changes)

    tokens = _current_options(tmp_path)
    assert [t for t in tokens if t.startswith("rootflags=")] == \
        ["rootflags=compress-force=zstd:1,noatime,subvol=@"]


def test_apply_replaces_root_in_place(tmp_path):
    _entry(tmp_path, "root=LABEL=root rw quiet")
    action = KernelCmdlineAction({"bootloader": "sd-boot"},
                                 ActionContext(target=Target(root=str(tmp_path))))
    changes = [Change("kernel_cmdline", Op.INSTALL, "root=/dev/mapper/cryptroot")]

    action.apply(changes)

    tokens = _current_options(tmp_path)
    assert [t for t in tokens if t.startswith("root=")] == ["root=/dev/mapper/cryptroot"]


def test_apply_replaces_rootfstype_in_place(tmp_path):
    _entry(tmp_path, "root=LABEL=root rw rootfstype=ext4 quiet")
    action = KernelCmdlineAction({"bootloader": "sd-boot"},
                                 ActionContext(target=Target(root=str(tmp_path))))
    changes = [Change("kernel_cmdline", Op.INSTALL, "rootfstype=btrfs")]

    action.apply(changes)

    tokens = _current_options(tmp_path)
    assert [t for t in tokens if t.startswith("rootfstype=")] == ["rootfstype=btrfs"]


def test_apply_rw_and_ro_are_mutually_exclusive(tmp_path):
    _entry(tmp_path, "root=LABEL=root rw quiet")
    action = KernelCmdlineAction({"bootloader": "sd-boot"},
                                 ActionContext(target=Target(root=str(tmp_path))))
    changes = [Change("kernel_cmdline", Op.INSTALL, "ro")]

    action.apply(changes)

    tokens = _current_options(tmp_path)
    assert "rw" not in tokens
    assert tokens.count("ro") == 1


def test_apply_still_appends_a_non_root_install_alongside_the_rest(tmp_path):
    """Non-root tokens keep today's behaviour: no supersede, just append."""
    _entry(tmp_path, "root=LABEL=root rw quiet")
    action = KernelCmdlineAction({"bootloader": "sd-boot"},
                                 ActionContext(target=Target(root=str(tmp_path))))
    changes = [Change("kernel_cmdline", Op.INSTALL, "splash")]

    action.apply(changes)

    tokens = _current_options(tmp_path)
    assert tokens.count("root=LABEL=root") == 1
    assert "splash" in tokens

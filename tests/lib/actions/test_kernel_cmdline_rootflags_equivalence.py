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


# --- B1: two live rootflags= tokens must never read as converged ------------- #
#
# Review, blocker B1: the pre-fix duplication bug (root cause C) can leave an
# entry with TWO `rootflags=` tokens — the kernel honours the LAST one. Naive
# code compared against `next(t for t in actual_SET if ...)`, which is
# hash-order dependent: on some `PYTHONHASHSEED`s the FIRST (stale) token was
# picked and the plan went silent about a genuinely divergent machine.

_TWO_ROOTFLAGS_ENTRY = (
    "root=LABEL=root rw "
    "rootflags=compress-force=zstd,noatime,subvol=@ "
    "rootflags=compress-force=zstd:1,noatime,subvol=@ quiet"
)

# A CAPTURED config (findmnt-resolved `zstd:3`, as `sync` would write) — the
# exact review scenario (probe_two_rootflags.py): the FIRST live token is bare
# `zstd` (equivalent to desired `zstd:3`), the LAST — the one the kernel
# honours — is `zstd:1` (NOT equivalent). Neither live token literally equals
# the desired string, so the only way `plan` could go silent is the
# equivalence substitution wrongly matching one of the two duplicates.
_CAPTURED = {**_PLAIN, "disks": {"disks": [{
    **_PLAIN["disks"]["disks"][0],
    "partitions": [{
        **_PLAIN["disks"]["disks"][0]["partitions"][0],
        "btrfs_subvolumes": [{"name": "@", "mountpoint": "/",
                              "mount_options": ["compress-force=zstd:3", "noatime"]}],
    }],
}]}}


def test_two_live_rootflags_tokens_never_read_as_converged(tmp_path):
    """The entry is genuinely divergent (kernel boots zstd:1; config says
    zstd:3) — plan must NOT go silent just because one of the two live tokens
    happens to be equivalent to the desired one."""
    _entry(tmp_path, _TWO_ROOTFLAGS_ENTRY)

    planned = _plan(tmp_path, _CAPTURED, managed=["root=LABEL=root", "rw", "quiet"])

    assert ("INSTALL", "rootflags=compress-force=zstd:3,noatime,subvol=@") in planned


def test_two_live_rootflags_tokens_is_deterministic_across_hash_seeds(tmp_path):
    """Directly exercises the helper the plan-time diff uses, with the two live
    tokens as `actual()` naturally produces them — a `set`. The fix counts
    matches instead of taking `next()` from it, which is a pure cardinality
    check and therefore order-independent even over a `set`; no seed loop is
    needed to prove it, but the brief asks the integration case above to be
    re-run under PYTHONHASHSEED=0..7 too (see the fix report). Never
    substituted: the literal desired `zstd:3` token comes back UNCHANGED, not
    swapped for either duplicate on the entry."""
    _entry(tmp_path, _TWO_ROOTFLAGS_ENTRY)
    action = KernelCmdlineAction(_CAPTURED, ActionContext(target=Target(root=str(tmp_path))))

    desired = action._desired_tokens_for_diff(action.actual())

    assert "rootflags=compress-force=zstd:3,noatime,subvol=@" in desired


def test_apply_collapses_two_live_rootflags_into_one(tmp_path):
    """`_new_tokens` must collapse a pre-existing duplicate down to exactly one
    when the plan installs a new rootflags= — root cause C's fix already does
    this; B1 only adds "never treat a duplicate as converged" on top."""
    _entry(tmp_path, _TWO_ROOTFLAGS_ENTRY)
    action = KernelCmdlineAction({"bootloader": "sd-boot"},
                                 ActionContext(target=Target(root=str(tmp_path))))
    changes = [Change("kernel_cmdline", Op.INSTALL,
                      "rootflags=compress-force=zstd,noatime,subvol=@")]

    action.apply(changes)

    tokens = _current_options(tmp_path)
    assert [t for t in tokens if t.startswith("rootflags=")] == \
        ["rootflags=compress-force=zstd,noatime,subvol=@"]


# --- B2: explicit ro must converge, not flip-flop forever --------------------- #

def _btrfs_ro_config():
    return {"bootloader": "sd-boot", "kernel_cmdline": ["ro"], "disks": {"disks": [{
        "device": "/dev/vda", "partition_table": "gpt",
        "partitions": [{"label": "root", "size": "rest", "filesystem": "btrfs",
                        "partition_type": "linux", "mountpoint": None,
                        "btrfs_subvolumes": [{"name": "@", "mountpoint": "/",
                                              "mount_options": ["compress-force=zstd"]}]}]}]}}


def test_explicit_ro_converges_after_one_apply(tmp_path):
    """`_derive_from_disks` always emits `root=... rw`; an explicit `ro` must
    suppress it — the same "explicit wins" rule `root=` already gets — so the
    lap plan -> apply -> plan is silent, not an endless rw<->ro rewrite."""
    _entry(tmp_path, "root=LABEL=root rw rootflags=compress-force=zstd,subvol=@")
    action = KernelCmdlineAction(_btrfs_ro_config(),
                                 ActionContext(target=Target(root=str(tmp_path))))

    managed: list = []
    first = action.plan(managed=managed)
    assert first == [("kernel_cmdline", "ro")] or \
        [(c.op.name, c.item) for c in first] == [("INSTALL", "ro")]
    action.apply(first)
    managed = action.managed_keys()["kernel_cmdline"]

    second = action.plan(managed=managed)
    assert second == []

    tokens = _current_options(tmp_path)
    assert "rw" not in tokens
    assert tokens.count("ro") == 1


def test_no_explicit_flag_keeps_deriving_rw_as_before(tmp_path):
    """Mirror case: nothing declared -> the derived `rw` still applies, once,
    and stays converged."""
    _entry(tmp_path, "root=LABEL=root rw rootflags=compress-force=zstd,subvol=@")
    config = {**_btrfs_ro_config(), "kernel_cmdline": []}
    action = KernelCmdlineAction(config, ActionContext(target=Target(root=str(tmp_path))))

    managed: list = []
    first = action.plan(managed=managed)
    assert [(c.op.name, c.item) for c in first] == []   # rw already there


# --- N1: subvol=/@ and subvol=@ describe the same subvolume ------------------- #

def test_subvol_leading_slash_is_normalized_for_equivalence():
    assert _rootflags_equivalent("compress-force=zstd,subvol=/@",
                                 "compress-force=zstd,subvol=@")


def test_subvolid_is_left_alone_and_not_equivalent_to_a_different_one():
    assert not _rootflags_equivalent("subvolid=256", "subvolid=257")

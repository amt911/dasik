"""Every encrypted partition needs an unlock parameter, not just the root one.

`_derive_from_disks` skipped anything that did not mount `/`, so a second LUKS
device (swap for hibernation, an encrypted /home) never got `rd.luks.name` and
the initramfs left it closed. Declaring it by hand was a trap: `_merge` keyed on
`rd.luks.name`, and `rd.luks.name` is a REPEATABLE kernel parameter, so one
explicit token silently dropped the derived root one — the config asked for more
and got less, and the machine stopped booting.
"""
from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction
from dasik.lib.actions.luks_uuid import luks_uuid


ROOT_UUID = luks_uuid("cryptroot")
SWAP_UUID = luks_uuid("cryptswap")


def _cfg(*, swap=True, explicit=None, tpm2_swap=False):
    parts = [
        {"label": "esp", "filesystem": "fat32", "mountpoint": "/boot"},
        {"label": "root", "filesystem": "btrfs", "mountpoint": None,
         "encrypt": True, "luks_name": "cryptroot",
         "mount_options": ["compress-force=zstd:3"],
         "btrfs_subvolumes": [{"name": "@", "mountpoint": "/"}]},
    ]
    if swap:
        part = {"label": "swap", "filesystem": "swap", "encrypt": True,
                "luks_name": "cryptswap"}
        if tpm2_swap:
            part["unlock_tpm2"] = True
        parts.insert(1, part)
    cfg = {"disks": {"disks": [{"partitions": parts}]}}
    if explicit is not None:
        cfg["kernel_cmdline"] = explicit
    return cfg


def _tokens(cfg):
    return KernelCmdlineAction(cfg)._desired_tokens()


def test_every_encrypted_partition_gets_its_own_rd_luks_name():
    tokens = _tokens(_cfg())
    assert f"rd.luks.name={ROOT_UUID}=cryptroot" in tokens
    assert f"rd.luks.name={SWAP_UUID}=cryptswap" in tokens


def test_root_params_are_still_derived_once():
    tokens = _tokens(_cfg())
    assert "root=/dev/mapper/cryptroot" in tokens
    assert "rootflags=compress-force=zstd:3,subvol=@" in tokens
    assert len([t for t in tokens if t.startswith("root=")]) == 1


def test_a_non_root_partition_contributes_no_root_parameter():
    tokens = _tokens(_cfg())
    assert "root=/dev/mapper/cryptswap" not in tokens


def test_unlock_options_follow_their_own_partition():
    tokens = _tokens(_cfg(tpm2_swap=True))
    assert f"rd.luks.options={SWAP_UUID}=tpm2-device=auto" in tokens
    assert not [t for t in tokens
                if t.startswith(f"rd.luks.options={ROOT_UUID}")]


def test_an_explicit_rd_luks_name_does_not_drop_the_derived_ones():
    """The repeatable-parameter trap: explicit must ADD, never replace."""
    extra = "rd.luks.name=11111111-1111-1111-1111-111111111111=cryptdata"
    tokens = _tokens(_cfg(explicit=[extra]))
    assert extra in tokens
    assert f"rd.luks.name={ROOT_UUID}=cryptroot" in tokens
    assert f"rd.luks.name={SWAP_UUID}=cryptswap" in tokens


def test_an_identical_explicit_token_is_not_duplicated():
    same = f"rd.luks.name={SWAP_UUID}=cryptswap"
    tokens = _tokens(_cfg(explicit=[same]))
    assert tokens.count(same) == 1


def test_single_valued_params_still_let_explicit_win():
    """`root=` is not repeatable: an explicit one must replace the derived one."""
    tokens = _tokens(_cfg(explicit=["root=/dev/mapper/other"]))
    assert "root=/dev/mapper/other" in tokens
    assert "root=/dev/mapper/cryptroot" not in tokens


def test_unencrypted_config_derives_no_luks_tokens():
    cfg = {"disks": {"disks": [{"partitions": [
        {"label": "root", "filesystem": "ext4", "mountpoint": "/"}]}]}}
    assert not [t for t in _tokens(cfg) if t.startswith("rd.luks.")]

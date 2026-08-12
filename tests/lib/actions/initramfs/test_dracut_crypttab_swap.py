"""/etc/crypttab has one owner, and with dracut that owner is DracutBackend.

A random-key swap is plain dm-crypt, so it never appears in the LUKS loop that
composes the derived entries — but it still needs a line in the same file. It is
derived here rather than captured so the entry always matches the label
DiskPartitionAction actually wrote.
"""
from dasik.lib.actions.initramfs.dracut import DracutBackend
from dasik.lib.target.target import Target


RANDOM_SWAP_LINE = ("swap LABEL=cryptswap /dev/urandom "
                    "swap,offset=2048,cipher=aes-xts-plain64,size=512,sector-size=4096")


def _cfg(**over):
    cfg = {
        "initramfs": "dracut",
        "disks": {"disks": [{"device": "/dev/vda", "partitions": [
            {"label": "root", "filesystem": "btrfs", "encrypt": True,
             "luks_name": "cryptroot", "mountpoint": "/"},
            {"label": "swap", "filesystem": "swap", "swap_encryption": "random"},
        ]}]},
    }
    cfg.update(over)
    return cfg


def _b(cfg):
    return DracutBackend(cfg, Target(root="/"))


def test_crypttab_carries_the_random_swap_line():
    assert RANDOM_SWAP_LINE in _b(_cfg()).crypttab()


def test_the_luks_root_entry_is_still_there():
    text = _b(_cfg()).crypttab()
    assert "cryptroot UUID=" in text


def test_the_derived_swap_line_is_not_duplicated_by_a_captured_one():
    # A synced config carries the verbatim crypttab in `files`. The derived
    # entry wins; the stale captured one must not be appended next to it.
    cfg = _cfg(files=[{"path": "/etc/crypttab",
                       "content": "swap LABEL=cryptswap /dev/urandom swap,offset=2048\n"}])
    assert _b(cfg).crypttab().count("swap LABEL=cryptswap") == 1


def test_a_config_without_a_random_swap_gets_no_such_line():
    cfg = _cfg()
    cfg["disks"]["disks"][0]["partitions"][1].pop("swap_encryption")
    assert "LABEL=cryptswap" not in _b(cfg).crypttab()

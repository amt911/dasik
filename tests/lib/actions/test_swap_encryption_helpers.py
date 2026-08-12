"""The strings a random-key swap is made of, derived once and shared.

Four writers need to agree on them — DiskPartitionAction (which formats the
label filesystem), DracutBackend (which composes /etc/crypttab),
EncryptedSwapAction (which writes /etc/fstab) and preflight (which validates
the pair). Deriving them in one place is what keeps the crypttab entry pointing
at the label that was actually written.
"""
from dasik.lib.actions.swap_encryption import (
    CRYPTTAB_OPTIONS,
    crypttab_line,
    fstab_line,
    random_swap_partitions,
    swap_names,
)


RANDOM_SWAP = {"label": "swap", "filesystem": "swap", "swap_encryption": "random"}


def _cfg(*parts):
    return {"disks": {"disks": [{"device": "/dev/vda", "partitions": list(parts)}]}}


def test_random_swap_partitions_finds_only_the_declared_ones():
    cfg = _cfg({"label": "root", "filesystem": "btrfs"},
               RANDOM_SWAP,
               {"label": "swap2", "filesystem": "swap"})
    assert random_swap_partitions(cfg) == [RANDOM_SWAP]


def test_random_swap_partitions_is_empty_without_disks():
    assert random_swap_partitions({}) == []


def test_random_swap_partitions_survives_a_disks_block_of_the_wrong_shape():
    assert random_swap_partitions({"disks": "nope"}) == []


def test_names_derive_from_the_partition_label():
    assert swap_names(RANDOM_SWAP) == ("swap", "cryptswap")
    assert swap_names({"label": "swap2"}) == ("swap2", "cryptswap2")


def test_crypttab_line_matches_the_wiki_procedure():
    assert crypttab_line(RANDOM_SWAP) == (
        "swap LABEL=cryptswap /dev/urandom "
        "swap,offset=2048,cipher=aes-xts-plain64,size=512,sector-size=4096")


def test_the_offset_covers_exactly_the_1MiB_label_filesystem():
    # 2048 sectors x 512 B = 1 MiB. Get this wrong and the swap either
    # overwrites the label it is addressed by, or wastes space.
    assert "offset=2048" in CRYPTTAB_OPTIONS


def test_fstab_line_names_the_mapper_device():
    assert fstab_line(RANDOM_SWAP) == "/dev/mapper/swap none swap defaults 0 0"

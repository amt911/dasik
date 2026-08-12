"""Random-key swap: the pure derivations, shared by every writer.

A swap encrypted with a key drawn from ``/dev/urandom`` is re-created on every
boot, so ``mkswap`` erases whatever UUID it had. The wiki's answer
(``Dm-crypt/Swap encryption#UUID and LABEL``) is to put a 1 MiB ext2 filesystem
in FRONT of the swap purely to carry a persistent LABEL, and to start the
encrypted area after it with ``offset=2048`` (2048 sectors x 512 B = 1 MiB).
Addressing the device by that label is what keeps crypttab from reformatting
the wrong disk after a partition renumbering — and crypttab's ``swap`` option
reformats whatever it is pointed at, on every boot, without asking.

Everything here is a pure function of the config so that DiskPartitionAction,
DracutBackend, EncryptedSwapAction and preflight all derive the SAME strings.
"""
from __future__ import annotations
from typing import Any, Dict, List, Tuple

# /dev/urandom, not /dev/random: the two are cryptographically identical on
# kernels >= 5.6 once the pool is initialised, but /dev/random BLOCKS before
# that — and this is read during boot, before entropy has accumulated.
KEY_SOURCE = "/dev/urandom"
# The 1 MiB ext2 label filesystem lives in the first 2048 sectors of 512 B.
LABEL_OFFSET_SECTORS = 2048
LABEL_FS_SIZE = "1M"
CRYPTTAB_OPTIONS = (f"swap,offset={LABEL_OFFSET_SECTORS},"
                    "cipher=aes-xts-plain64,size=512,sector-size=4096")
RANDOM = "random"


def random_swap_partitions(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every partition stanza declaring ``swap_encryption: random``, in config order."""
    out: List[Dict[str, Any]] = []
    disks = config.get("disks", {})
    if not isinstance(disks, dict):
        return out
    for disk in disks.get("disks", []) or []:
        for part in disk.get("partitions", []) or []:
            if is_random_swap(part):
                out.append(part)
    return out


def is_random_swap(part: Dict[str, Any]) -> bool:
    """True when this partition stanza declares the random-key mode.

    Accepts the enum or the raw string: the same dict reaches here straight from
    JSON (a string) and from a validated model dump (a ``SwapEncryption``).
    """
    value = part.get("swap_encryption", "none")
    return str(getattr(value, "value", value)) == RANDOM


def swap_names(part: Dict[str, Any]) -> Tuple[str, str]:
    """``(device-mapper name, ext2 label)`` derived from the partition label.

    Derived rather than configurable so two random swaps on one machine cannot
    collide, and so nothing has to be threaded through four call sites.
    """
    label = str(part.get("label") or "swap")
    return label, f"crypt{label}"


def crypttab_line(part: Dict[str, Any]) -> str:
    mapper, fs_label = swap_names(part)
    return f"{mapper} LABEL={fs_label} {KEY_SOURCE} {CRYPTTAB_OPTIONS}"


def fstab_line(part: Dict[str, Any]) -> str:
    mapper, _ = swap_names(part)
    return f"/dev/mapper/{mapper} none swap defaults 0 0"

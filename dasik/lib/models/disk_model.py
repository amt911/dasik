"""Models for disk partitioning configuration."""
import re
from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator, model_validator

# device-mapper name that is ALSO interpolated into the kernel cmdline
# (`rd.luks.name=<uuid>=<name>`, `root=/dev/mapper/<name>`). Restrict to a safe
# identifier so a config value can't inject extra kernel params or break cryptsetup.
_LUKS_NAME_RE = re.compile(r"[A-Za-z0-9_-]+")
# Partition/filesystem label: reaches `mkfs -L <label>` and lsblk LABEL matching.
# Keep it a plain token (no whitespace/newline/slash) within a length most
# filesystems accept.
_LABEL_RE = re.compile(r"[A-Za-z0-9_.-]{1,36}")
# Filesystems a LUKS key device may carry. Each name doubles as the kernel
# module the initramfs must load to read it, so the set is closed rather than
# free-form.
_KEYDEV_FILESYSTEMS = {"vfat", "exfat", "ext4", "btrfs", "xfs"}


class PartitionTableType(str, Enum):
    """Partition table types."""
    GPT = "gpt"
    MSDOS = "msdos"


class FileSystemType(str, Enum):
    """Supported filesystem types."""
    EXT4 = "ext4"
    BTRFS = "btrfs"
    FAT32 = "fat32"
    SWAP = "swap"
    XFS = "xfs"


class PartitionType(str, Enum):
    """Partition types for GPT."""
    EFI = "esp"  # EFI System Partition
    LINUX = "linux"
    SWAP = "linux-swap"
    LVM = "lvm"


class BtrfsSubvolume(BaseModel):
    """Btrfs subvolume configuration."""
    name: str = Field(..., description="Subvolume name (e.g., '@', '@home')")
    mountpoint: str = Field(..., description="Mount point (e.g., '/', '/home')")
    mount_options: List[str] = Field(
        default_factory=lambda: ["compress-force=zstd"],
        description="Mount options for this subvolume"
    )


class Partition(BaseModel):
    """Partition configuration."""
    label: str = Field(..., description="Partition label for identification")
    size: str = Field(
        ...,
        description="Size: number with unit (100MB, 512MiB, 1GB, 1GiB) or percentage (50%) or 'rest' for remaining space"
    )
    filesystem: FileSystemType = Field(..., description="Filesystem type")
    partition_type: PartitionType = Field(
        default=PartitionType.LINUX,
        description="Partition type (GPT)"
    )
    mountpoint: Optional[str] = Field(
        None,
        description="Mount point (e.g., '/', '/boot', '/home')"
    )
    encrypt: bool = Field(
        default=False,
        description="Whether to encrypt this partition with LUKS"
    )
    luks_name: Optional[str] = Field(
        None,
        description="LUKS device mapper name if encrypted"
    )
    luks_password: Optional[str] = Field(
        None,
        description="LUKS passphrase (declarative, non-interactive). Stored in "
                    "plaintext in the config — prefer luks_keyfile for secrecy."
    )
    luks_keyfile: Optional[str] = Field(
        None,
        description="Path to a LUKS key file (used instead of luks_password)."
    )
    luks_uuid: Optional[str] = Field(
        None,
        description="Explicit LUKS UUID. If unset a deterministic UUID is used, "
                    "so the disk header and the kernel cmdline always agree."
    )
    unlock_keyfile: Optional[str] = Field(
        None,
        description="Path to a key file added as an extra LUKS key for automatic "
                    "boot unlock (rd.luks.key). Password unlock still works."
    )
    unlock_keydev: Optional[str] = Field(
        None,
        description="Filesystem UUID of the device holding unlock_keyfile (e.g. a "
                    "USB pendrive); appended to rd.luks.key so the initramfs finds it."
    )
    unlock_keydev_fs: Optional[str] = Field(
        None,
        description="Filesystem of unlock_keydev (vfat, exfat, ext4, btrfs, "
                    "xfs). The initramfs needs that module to read the key "
                    "device at boot when it differs from the root filesystem."
    )
    unlock_tpm2: bool = Field(
        default=False,
        description="Enroll a TPM2 keyslot for automatic (passwordless) unlock."
    )
    unlock_fido2: bool = Field(
        default=False,
        description="Enroll a FIDO2 token keyslot (needs the physical key at enroll+boot)."
    )
    luks_options: List[str] = Field(
        default_factory=list,
        description="Extra verbatim rd.luks.options tokens appended after the "
                    "auto-derived tpm2/fido2 ones (e.g. 'token-timeout=10s').",
    )
    mount_options: List[str] = Field(
        default_factory=list,
        description="Additional mount options"
    )
    btrfs_subvolumes: List[BtrfsSubvolume] = Field(
        default_factory=list,
        description="Btrfs subvolumes (only if filesystem is btrfs)"
    )
    format: bool = Field(
        default=True,
        description="Whether to format this partition"
    )

    @field_validator('label')
    @classmethod
    def validate_label(cls, v: str) -> str:
        if not _LABEL_RE.fullmatch(v):
            raise ValueError(
                f"Invalid partition label {v!r}: must match [A-Za-z0-9_.-]{{1,36}} "
                f"(no spaces, newlines, or slashes)."
            )
        return v

    @field_validator('size')
    @classmethod
    def validate_size(cls, v: str) -> str:
        """Validate size format."""
        v = v.strip()
        if v.lower() == 'rest':
            return v
        
        # Check for percentage
        if v.endswith('%'):
            try:
                percent = int(v[:-1])
                if not 1 <= percent <= 100:
                    raise ValueError("Percentage must be between 1 and 100")
                return v
            except ValueError:
                raise ValueError(f"Invalid percentage format: {v}")
        
        # Check for size with unit
        valid_units = ['B', 'KB', 'MB', 'GB', 'TB', 'KiB', 'MiB', 'GiB', 'TiB']
        has_valid_unit = any(v.upper().endswith(unit.upper()) for unit in valid_units)
        
        if not has_valid_unit:
            raise ValueError(
                f"Size must end with a valid unit: {', '.join(valid_units)} "
                f"or be a percentage (e.g., '50%') or 'rest'"
            )
        
        return v

    @field_validator('unlock_keydev_fs')
    @classmethod
    def validate_unlock_keydev_fs(cls, v: Optional[str]) -> Optional[str]:
        """The value becomes a kernel module name inside the initramfs, so it is
        restricted to the filesystems a key device plausibly carries rather than
        passed through verbatim."""
        if v is not None and v not in _KEYDEV_FILESYSTEMS:
            raise ValueError(
                f"Invalid unlock_keydev_fs {v!r}: must be one of "
                f"{', '.join(sorted(_KEYDEV_FILESYSTEMS))} (it names the kernel "
                f"module the initramfs loads to read the key device)."
            )
        return v

    @model_validator(mode='after')
    def _validate_encryption(self) -> "Partition":
        """An encrypted partition needs a mapper name. A field_validator on
        luks_name does NOT run when luks_name is defaulted (pydantic v2 skips
        validators on defaults), so `encrypt: true` with no luks_name slipped
        through to a runtime crash — a model_validator always runs.
        """
        if self.encrypt and not self.luks_name:
            raise ValueError("luks_name must be set when encrypt is True")
        if self.luks_name is not None and not _LUKS_NAME_RE.fullmatch(self.luks_name):
            raise ValueError(
                f"luks_name {self.luks_name!r} must match [A-Za-z0-9_-]+ "
                f"(device-mapper name; no spaces, '=', '/', or shell/cmdline "
                f"metacharacters — it is interpolated into the kernel cmdline)."
            )
        return self


class DiskLayout(BaseModel):
    """Disk layout configuration."""
    device: str = Field(
        ...,
        description="Device path (e.g., '/dev/sda', '/dev/nvme0n1')",
        examples=["/dev/sda", "/dev/nvme0n1", "/dev/vda"]
    )
    partition_table: PartitionTableType = Field(
        default=PartitionTableType.GPT,
        description="Partition table type"
    )
    wipe_disk: bool = Field(
        default=False,
        description="Whether to wipe the entire disk before partitioning (DESTRUCTIVE!)"
    )
    partitions: List[Partition] = Field(
        ...,
        description="List of partitions to create",
        min_length=1
    )

    @field_validator('device')
    @classmethod
    def validate_device(cls, v: str) -> str:
        """Validate device path format."""
        if not v.startswith('/dev/'):
            raise ValueError("Device must start with '/dev/'")
        return v

    @field_validator('partitions')
    @classmethod
    def validate_partitions(cls, v: List[Partition]) -> List[Partition]:
        """Validate partitions list."""
        # Check that only one partition has 'rest' size
        rest_count = sum(1 for p in v if p.size.lower() == 'rest')
        if rest_count > 1:
            raise ValueError("Only one partition can have size 'rest'")
        
        # If there's a 'rest' partition, it should be the last one
        if rest_count == 1:
            rest_index = next(i for i, p in enumerate(v) if p.size.lower() == 'rest')
            if rest_index != len(v) - 1:
                raise ValueError("Partition with size 'rest' must be the last partition")
        
        # Check for duplicate labels
        labels = [p.label for p in v]
        if len(labels) != len(set(labels)):
            raise ValueError("Partition labels must be unique")
        
        # Validate btrfs subvolumes only for btrfs filesystems
        for partition in v:
            if partition.btrfs_subvolumes and partition.filesystem != FileSystemType.BTRFS:
                raise ValueError(
                    f"Partition '{partition.label}' has btrfs_subvolumes but filesystem is not btrfs"
                )
        
        return v


class DisksConfiguration(BaseModel):
    """Root configuration for disk layouts."""
    disks: List[DiskLayout] = Field(
        ...,
        description="List of disk layouts to configure",
        min_length=1
    )

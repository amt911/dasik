from typing import Optional, List, Union
from pydantic import BaseModel, Field

from .locale_model import LocaleModel
from .timezone_model import TimezoneModel
from .network_model import NetworkModel
from .disk_model import DisksConfiguration
from .user_model import UserModel
from .file_model import FileEntry, EtcFile
from .package_model import PackageSpec
from .pacman_model import PacmanModel
from .systemd_model import SystemdModel
from .bluetooth_model import BluetoothModel
from .hw_accel_model import HardwareAccelerationModel
from .kvm_model import KvmModel
from .cups_model import CupsModel
from .ms_fonts_model import MicrosoftFontsModel
from .firewall_model import FirewallModel
from .wireguard_model import WireguardModel
from .snapper_model import SnapperModel


class JsonModel(BaseModel):
    """Root configuration model – validated with pydantic."""

    # --- existing mandatory fields ---
    locales: LocaleModel
    timezone: TimezoneModel
    hostname: str
    enable_microcode: bool = False

    # --- existing optional fields ---
    # `network` is optional: NetworkAction no-ops on an absent block (it only
    # writes /etc/hostname + /etc/hosts, gated on `hostname`). Requiring it broke
    # truly minimal configs (e.g. config/vm-minimal.json) and contradicted the
    # "keep sections optional" design. See tests/lib/models/test_network_optional.py.
    network: Optional[NetworkModel] = None
    metadata: Optional[dict] = None
    disks: Optional[DisksConfiguration] = None
    notes: Optional[str] = None

    # --- new fields ---
    users: List[UserModel] = Field(default_factory=list)
    drivers: List[str] = Field(default_factory=list, description="GPU driver selection")
    packages: List[Union[str, PackageSpec]] = Field(
        default_factory=list,
        description="Packages (str=explicit, {name,reason} for deps; aur- prefix for AUR)")
    bootloader: str = Field(default="grub", description="grub | sd-boot")

    # Files / lines to drop on the target system
    udev_rules: List[FileEntry] = Field(default_factory=list)
    modprobe_conf: List[FileEntry] = Field(default_factory=list)
    profile_d: List[FileEntry] = Field(default_factory=list)
    etc_environment: List[str] = Field(default_factory=list)
    files: List[EtcFile] = Field(default_factory=list)
    kernel_cmdline: List[str] = Field(default_factory=list)

    # Toggles
    enable_trim: bool = False
    remove_home_on_delete: bool = False
    initramfs: str = "mkinitcpio"

    # Sub-models
    pacman: Optional[PacmanModel] = None
    systemd: Optional[SystemdModel] = None
    bluetooth: Optional[BluetoothModel] = None
    hardware_acceleration: Optional[HardwareAccelerationModel] = None
    kvm: Optional[KvmModel] = None
    cups: Optional[CupsModel] = None
    microsoft_fonts: Optional[MicrosoftFontsModel] = None
    firewall: Optional[FirewallModel] = None
    wireguard: Optional[WireguardModel] = None
    snapper: Optional[SnapperModel] = None
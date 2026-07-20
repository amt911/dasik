import re
from typing import Literal, Optional, List, Union, Dict, Any
from pydantic import BaseModel, Field, model_validator

from .locale_model import LocaleModel
from .timezone_model import TimezoneModel
from .network_model import NetworkModel
from .disk_model import DisksConfiguration
from .user_model import UserModel
from .file_model import FileEntry, EtcFile
from .package_model import PackageSpec, PackagePolicyModel, GitPackageSourceModel
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

    # --- sections that used to be mandatory, now optional ---
    # The v2 registry already registers the locale/timezone/network actions
    # `is_optional=True` and the reconciler skips an absent optional section, so
    # the actions no-op when these are omitted. Requiring them in the model was
    # the sole thing blocking truly minimal (package-only / disk-only) configs
    # and it contradicted the "keep sections optional" design. `hostname`
    # defaults to "" — NetworkAction gates all its writes on a non-empty hostname.
    locales: Optional[LocaleModel] = None
    timezone: Optional[TimezoneModel] = None
    network: Optional[NetworkModel] = None
    hostname: str = ""
    enable_microcode: bool = False

    # --- existing optional fields ---
    metadata: Optional[dict] = None
    disks: Optional[DisksConfiguration] = None
    notes: Optional[str] = None

    # --- new fields ---
    users: List[UserModel] = Field(default_factory=list)
    drivers: List[str] = Field(default_factory=list, description="GPU driver selection")
    packages: List[Union[str, PackageSpec]] = Field(
        default_factory=list,
        description="Packages by real name (str=explicit, {name,reason} for deps). "
                    "Source (repo/group/AUR/package_sources) is auto-resolved — no aur- prefix.")
    # Unknown-package policy + explicit Git PKGBUILD sources (PLAN v3).
    package_policy: PackagePolicyModel = Field(default_factory=PackagePolicyModel)
    package_sources: Dict[str, GitPackageSourceModel] = Field(
        default_factory=dict,
        description="Map of package name -> Git PKGBUILD source for packages not in "
                    "any pacman repo/group or the AUR.")
    # Restricted to the backends dasik actually implements: a typo used to fall
    # through to the default backend silently (forensic report §9.9). "systemd-boot"
    # is kept as an accepted alias of "sd-boot" (BootloaderAction treats them alike).
    bootloader: Literal["grub", "sd-boot", "systemd-boot"] = Field(
        default="grub", description="grub | sd-boot")

    # Files / lines to drop on the target system
    udev_rules: List[FileEntry] = Field(default_factory=list)
    modprobe_conf: List[FileEntry] = Field(default_factory=list)
    modules_load: List[FileEntry] = Field(default_factory=list)
    sysctl_d: List[FileEntry] = Field(default_factory=list)
    tmpfiles_d: List[FileEntry] = Field(default_factory=list)
    sddm_conf_d: List[FileEntry] = Field(default_factory=list)
    profile_d: List[FileEntry] = Field(default_factory=list)
    etc_environment: List[str] = Field(default_factory=list)
    files: List[EtcFile] = Field(default_factory=list)
    kernel_cmdline: List[str] = Field(default_factory=list)

    # Toggles
    enable_trim: bool = False
    remove_home_on_delete: bool = False
    initramfs: Literal["mkinitcpio", "dracut"] = "mkinitcpio"

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
    # zram-generator: {device: {option: value}} mirroring zram-generator.conf ini.
    zram: Optional[Dict[str, Dict[str, Any]]] = None

    @model_validator(mode="after")
    def _validate_package_sources(self) -> "JsonModel":
        """Each package_sources key must be a valid Arch name AND appear in
        ``packages`` (normalizing the ``{name, reason}`` form). A source for a
        package nobody declares would never be installed — reject it early."""
        if not self.package_sources:
            return self
        declared = {
            (p.name if isinstance(p, PackageSpec) else p) for p in self.packages
        }
        for key in self.package_sources:
            if not _JM_VALID_PKG_NAME.fullmatch(key):
                raise ValueError(
                    f"package_sources key {key!r} is not a valid package name "
                    "([A-Za-z0-9][A-Za-z0-9@._+-]*, no leading '-')."
                )
            if key not in declared:
                raise ValueError(
                    f"package_sources key {key!r} is not declared in 'packages'; "
                    "add it to packages or remove the source."
                )
        return self


# Shared with the resolver/action grammar; a leading '-' or shell metacharacter
# is refused so a source key can never reach pacman argv or a shell unsafely.
_JM_VALID_PKG_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9@._+-]*")
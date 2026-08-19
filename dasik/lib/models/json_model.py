import re
from typing import Literal, Optional, List, Union, Dict, Any
from pydantic import BaseModel, Field, field_validator, model_validator

from .locale_model import LocaleModel
from .timezone_model import TimezoneModel
from .network_model import NetworkModel
from .disk_model import DisksConfiguration, LuksTokenPolicyModel
from .user_model import UserModel
from .file_model import FileEntry, EtcFile, HomeFile, _validate_mode
from .package_model import PackageSpec, PackagePolicyModel, GitPackageSourceModel
from .pacman_model import PacmanModel
from .systemd_model import SystemdModel
from .bluetooth_model import BluetoothModel
from .hw_accel_model import HardwareAccelerationModel
from .kvm_model import KvmModel
from .cups_model import CupsModel
from .ms_fonts_model import MicrosoftFontsModel
from .firewall_model import FirewallModel
from .wireguard_model import WireguardTunnel
from .snapper_model import SnapperModel
from .sudo_model import SudoModel
from .cpu_model import CpuModel
from .reflector_model import ReflectorModel
from .plymouth_model import PlymouthModel
from .apparmor_model import ApparmorModel
from .pam_model import PamModel
from .config_saver_model import ConfigSaverModel
from .containers_model import ContainersModel
from .tailscale_model import TailscaleModel
from .systemd_conf_model import validate_ini_section


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
    luks_token_policy: LuksTokenPolicyModel = Field(
        default_factory=LuksTokenPolicyModel,
        description="What an apply does when a TPM2/FIDO2 enrolment fails: "
                    "abort (default) or warn-and-continue.")
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
    etc_tree: Optional[str] = Field(
        default=None,
        description="Directory (relative to this config) mirroring /etc. Every "
                    "file under it becomes a `files` entry at the matching path, "
                    "so a PAM snippet or a udev rule lives in a real file instead "
                    "of an escaped JSON string. The loader expands it; `sync` "
                    "extracts captured /etc files back into it.")
    etc_tree_modes: Dict[str, str] = Field(
        default_factory=dict,
        description="Tree-relative path -> octal mode, for files whose mode Git "
                    "cannot carry. An executable file already becomes 0755; this "
                    "is for the rest, e.g. '0600' on a WireGuard keyfile, which "
                    "NetworkManager ignores in silence when world-readable.")

    home_files: List[HomeFile] = Field(
        default_factory=list,
        description="Files inside a user's $HOME (dotfiles, autostart entries). "
                    "Path is relative to the home the machine declares.")
    home_tree: Optional[str] = Field(
        default=None,
        description="Directory (relative to this config) mirroring users' homes, "
                    "one subdirectory per USER: <tree>/<user>/<path>. Every file "
                    "becomes a `home_files` entry. The /etc equivalent is "
                    "`etc_tree`; this keeps a captured YAML document a readable "
                    "file instead of an escaped JSON string.")
    home_tree_modes: Dict[str, str] = Field(
        default_factory=dict,
        description="Tree-relative path (<user>/<path>) -> octal mode, for modes "
                    "Git cannot carry. An executable file already becomes 0755.")
    kernel_cmdline: List[str] = Field(default_factory=list)

    # Toggles
    enable_trim: bool = False
    # REISUB: sysrq_always_enabled=1 on the kernel cmdline (the old installer's
    # enable_reisub). The cmdline value applies from early boot, which is when
    # the magic SysRq keys actually matter.
    sysrq: bool = False
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
    wireguard: Optional[List[WireguardTunnel]] = Field(
        default=None,
        description=(
            "WireGuard tunnels. Each names a `source` file next to the config, "
            "in the format its backend reads: a wg-quick .conf or a "
            "NetworkManager .nmconnection. dasik places it verbatim at 0600 "
            "and never converts between the two."))
    snapper: Optional[SnapperModel] = None
    sudo: Optional[SudoModel] = None
    cpu: Optional[CpuModel] = None
    reflector: Optional[ReflectorModel] = None
    plymouth: Optional[PlymouthModel] = None
    apparmor: Optional[ApparmorModel] = None
    pam: Optional[PamModel] = None
    config_saver: Optional[ConfigSaverModel] = None
    containers: Optional[ContainersModel] = None
    # Preferences rendered into the tailscaled conffile. Declaring the block
    # makes that file authoritative, so `tailscale set` stops moving these keys.
    tailscale: Optional[TailscaleModel] = None
    # zram-generator: {device: {option: value}} mirroring zram-generator.conf ini.
    zram: Optional[Dict[str, Dict[str, Any]]] = None
    # The pacman-owned /etc/systemd/*.conf files, one block per file, each
    # holding that file's single section. dasik writes its values as a
    # <conf>.d/10-dasik.conf drop-in and reads the effective configuration.
    oomd: Optional[Dict[str, Any]] = Field(
        default=None, description="[OOM] section of /etc/systemd/oomd.conf")
    systemd_system_conf: Optional[Dict[str, Any]] = Field(
        default=None, description="[Manager] section of /etc/systemd/system.conf")
    systemd_user_conf: Optional[Dict[str, Any]] = Field(
        default=None, description="[Manager] section of /etc/systemd/user.conf")

    _ini_sections = field_validator(
        "oomd", "systemd_system_conf", "systemd_user_conf", mode="after"
    )(staticmethod(validate_ini_section))

    @field_validator("etc_tree_modes", "home_tree_modes")
    @classmethod
    def _valid_tree_modes(cls, v: Dict[str, str]) -> Dict[str, str]:
        """Keys address a file inside the tree, so they follow the same rule the
        include directives do: relative, no escaping."""
        for path, mode in v.items():
            if path.startswith("/") or ".." in path.split("/"):
                raise ValueError(
                    f"tree mode key {path!r} must be relative to the tree "
                    "and contain no '..' segment")
            _validate_mode(mode)
        return v

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
"""
New simplified actions handler using registry pattern.

This module provides the new architecture for executing system configuration
actions with idempotency support. It replaces the old monolithic ActionsHandler.

Key improvements:
1. Idempotency: Actions check if changes are needed before executing
2. Scalability: New actions can be added without modifying handler
3. Maintainability: Each action is self-contained
4. Flexibility: Optional actions are handled automatically
5. Auto-derivation: mkinitcpio and kernel_cmdline are computed from disk config

Usage:
    from dasik.lib.actions.actions_handler_v2 import setup_actions, execute_installation
    
    # Register all available actions
    setup_actions()
    
    # Execute installation
    success = execute_installation("config.json")
"""

from ..json_parser.json_parser import JsonParser
from .action_registry import register_action, get_default_registry
from .action_executor import ActionExecutor


def setup_actions() -> None:
    """Register all available system configuration actions.
    
    Actions are registered in execution order.  The executor walks
    them sequentially; each action decides via ``is_needed()`` whether
    it actually runs.
    """
    # Clear first so repeated calls in one process don't double-register (#64).
    get_default_registry().clear()

    # --- imports (lazy so missing files don't crash import) ---------------
    from .disk_partition_action import DiskPartitionAction
    from .base_install_action import BaseInstallAction
    from .timezone_action import TimezoneAction
    from .locale_action import LocaleAction
    from .network_action import NetworkAction
    from .pacman_action import PacmanAction
    from .users_action import UsersAction
    from .home_files_action import HomeFilesAction
    from .config_saver_action import ConfigSaverAction
    from .containers_action import ContainersAction
    from .sudo_action import SudoAction
    from .packages_action import PackagesAction
    from .systemd_action import SystemdAction
    from .firewall_action import FirewallAction
    from .snapper_action import SnapperAction
    from .drop_files_action import DropFilesAction
    from .initramfs_action import InitramfsAction
    from .kernel_cmdline_action import KernelCmdlineAction
    from .bootloader_action import BootloaderAction
    from .ms_fonts_action import MicrosoftFontsAction
    from .zram_action import ZramAction
    from .systemd_conf_action import (
        OomdAction, SystemdSystemConfAction, SystemdUserConfAction,
    )
    from .pacman_hooks_action import PacmanHooksAction
    from .cpu_action import CpuAction
    from .reflector_action import ReflectorAction
    from .wireguard_action import WireguardAction
    from .plymouth_action import PlymouthAction
    from .luks_keyfile_action import LuksKeyfileAction
    from .encrypted_swap_action import EncryptedSwapAction
    from .apparmor_action import ApparmorAction
    from .auditd_conf_action import AuditdConfAction
    from .pam_action import PamAction

    # === Phase 1: disk & base install =====================================
    register_action(
        action_class=DiskPartitionAction,
        config_key='disks',
        is_optional=True,
    )
    # The pendrive/embedded unlock keyfile. Right after the disks: the LUKS
    # volumes are open by now (so the device to add the key to is known), and it
    # must be enrolled before the initramfs and the bootloader entry are built
    # around an rd.luks.key that would otherwise open nothing.
    register_action(
        action_class=LuksKeyfileAction,
        config_key='__root__',   # reads `disks` — the unlock lives per partition
        is_optional=True,
    )
    # dasik-owned pacman hooks (today: the mkinitcpio neutralizers). MUST come
    # before pacstrap: the very first transaction installs the kernel and fires
    # mkinitcpio's hooks, which would clobber the dracut image. The target is
    # already mounted at this point (DiskPartitionAction ran).
    register_action(
        action_class=PacmanHooksAction,
        config_key='__root__',
        is_optional=True,
    )
    register_action(
        action_class=BaseInstallAction,
        config_key='__root__',
        is_optional=False,
        required_fields=['enable_microcode'],
    )

    # === Phase 2: chroot configuration ====================================
    register_action(
        action_class=TimezoneAction,
        config_key='timezone',
        is_optional=True,
        required_fields=['region', 'city'],
    )
    register_action(
        action_class=LocaleAction,
        config_key='locales',
        is_optional=True,
        required_fields=['selected_locales', 'desired_locale', 'desired_tty_layout'],
    )
    register_action(
        action_class=NetworkAction,
        # __root__: reads the root-level 'hostname' plus the optional 'network'
        # section. Only `hostname` is required: demanding both meant a config
        # that declared a hostname and no `network` block was skipped entirely
        # and /etc/hostname was never written (forensic report F-31). The action
        # already no-ops on an empty hostname and treats `network` as optional.
        config_key='__root__',
        is_optional=True,
        required_fields=['hostname'],
    )
    register_action(
        action_class=PacmanAction,
        config_key='pacman',
        is_optional=True,
    )

    # === Phase 3: package installation ====================================
    # snapper create-config runs BEFORE the package transaction: snap-pac's
    # pacman hooks snapshot each transaction, so the config must already exist
    # or the whole install happens unprotected (as it did on 2026-07-19). The
    # action installs snapper/snap-pac itself if they are not there yet; the
    # timers still come from the expand toggle.
    register_action(
        action_class=SnapperAction,
        config_key='snapper',
        is_optional=True,
    )
    register_action(
        action_class=PackagesAction,
        # __root__: reads the packages list plus the sibling package_sources /
        # package_policy maps (PLAN v3 §5).
        config_key='__root__',
        is_optional=True,
    )
    # Users MUST come after Packages: `useradd -s /bin/zsh -G docker,libvirt`
    # references shells (zsh) and groups (docker, libvirt) that the packages
    # create when installed — before Packages, useradd fails (exit 6) because
    # those don't exist yet. No action after Packages needs the user to exist.
    register_action(
        action_class=UsersAction,
        # __root__: needs the root-level remove_home_on_delete flag alongside
        # the users list (issue: per-user delete flag is unreadable at delete).
        config_key='__root__',
        is_optional=True,
    )
    # Sudo comes after Users: the wheel group has members by now, and
    # PackagesAction has installed `sudo` (so `visudo` exists in the target).
    register_action(
        action_class=SudoAction,
        config_key='__root__',   # reads `sudo` plus `users` for the implicit default
        is_optional=True,
    )

    # PAM hardening comes right after Sudo, for the same reasons: the declared
    # users exist, and PackagesAction has installed libpwquality (without it a
    # pam_pwquality.so line in /etc/pam.d/passwd breaks the `passwd` command).
    register_action(
        action_class=PamAction,
        config_key='__root__',   # reads root-level `pam`
        is_optional=True,
    )

    # Home files come after Users for the obvious reason — the home has to
    # exist, and the file is chowned to a uid that only exists once useradd has
    # run. Everything under $HOME lands here: dotfiles, and the aa-notify
    # autostart entry the AppArmor block derives.
    register_action(
        action_class=HomeFilesAction,
        config_key='__root__',   # reads root-level `home_files`
        is_optional=True,
    )

    # === Phase 4: system services & files =================================
    register_action(
        action_class=SystemdAction,
        config_key='systemd',
        is_optional=True,
    )
    # Firewalld install+enable is handled by the `firewall` expand toggle
    # (packages + systemd); this applies the zone RULES (offline-cmd). Runs after
    # packages installed firewalld.
    register_action(
        action_class=FirewallAction,
        config_key='firewall',
        is_optional=True,
    )
    register_action(
        action_class=DropFilesAction,
        config_key='__root__',  # reads udev_rules, modprobe_conf, etc. from root
        is_optional=True,
    )
    # The fstab (and, without dracut, crypttab) lines of a random-key swap.
    # After DropFilesAction — which may write a verbatim /etc/crypttab — and
    # long after BaseInstallAction ran genfstab, because both files must exist
    # before a line is merged into them. Before phase 5, which builds the
    # initramfs around that crypttab.
    register_action(
        action_class=EncryptedSwapAction,
        config_key='__root__',   # reads `disks` — the mode lives per partition
        is_optional=True,
    )
    register_action(
        action_class=MicrosoftFontsAction,
        config_key='microsoft_fonts',
        is_optional=True,
    )
    # config-saver restores an archive into a user's $HOME, so it runs after
    # Users (the home must exist) and after Packages (the binary must too).
    register_action(
        action_class=ConfigSaverAction,
        config_key='__root__',   # reads root-level `config_saver`
        is_optional=True,
    )
    # The container runtime's id maps. After Users (useradd writes a range
    # itself for a user it creates, so most of the time this converges to
    # nothing) and after Packages (podman/docker are installed by then).
    register_action(
        action_class=ContainersAction,
        config_key='__root__',   # reads root-level `containers` + `users`
        is_optional=True,
    )
    register_action(
        action_class=ZramAction,
        config_key='__root__',  # reads root-level `zram` mapping
        is_optional=True,
    )
    # The pacman-owned /etc/systemd/*.conf files. DropFilesAction cannot own
    # them (its discovery skips package-owned paths, and /etc/systemd is not one
    # of its sections), so a setting like DefaultMemoryPressureDurationSec=20s
    # was invisible to both plan and sync.
    for _conf_action in (OomdAction, SystemdSystemConfAction, SystemdUserConfAction):
        register_action(
            action_class=_conf_action,
            config_key='__root__',  # reads its own root-level mapping
            is_optional=True,
        )
    # Capture-only (plan() is empty by design): CPU scaling and the reflector
    # policy converge through the expand toggles (packages, units, files) and
    # the kernel cmdline, but nothing captured them BACK — a synced config lost
    # the `reflector` block outright and spelled `cpu` as a hand-set kernel
    # parameter. `sync` only visits registered v3 actions, hence these entries.
    register_action(
        action_class=CpuAction,
        config_key='__root__',  # reads root-level `cpu` + `bootloader`
        is_optional=True,
    )
    register_action(
        action_class=ReflectorAction,
        config_key='__root__',  # reads root-level `reflector`
        is_optional=True,
    )
    register_action(
        action_class=PlymouthAction,
        config_key='__root__',  # reads root-level `plymouth`
        is_optional=True,
    )
    # Same shape, and the reason it is not DropFilesAction's job any more: with
    # both owning /etc/wireguard, a bootstrap sync captured the same private key
    # twice — once as the block, once as a `files` entry that then kept the
    # tunnel alive after the block was turned off.
    register_action(
        action_class=WireguardAction,
        config_key='__root__',  # reads root-level `wireguard`
        is_optional=True,
    )
    # Capture-only as well: AppArmor converges through the toggle (package,
    # unit, profile files) and the derived `lsm=` parameter, and nothing read it
    # back — a synced machine lost the block and kept the parameter as if it had
    # been hand-written.
    # auditd sets the mode of /var/log/audit itself at start, so the tmpfiles
    # override alone left the log root-only. Runs after Packages (the file comes
    # from the `audit` package) and before the boot phase.
    register_action(
        action_class=AuditdConfAction,
        config_key='__root__',   # reads root-level `apparmor`
        is_optional=True,
    )
    register_action(
        action_class=ApparmorAction,
        config_key='__root__',  # reads root-level `apparmor` + `bootloader`
        is_optional=True,
    )

    # === Phase 5: boot (must come last) ===================================
    register_action(
        action_class=InitramfsAction,
        config_key='__root__',
        is_optional=True,
    )
    register_action(
        # Install the bootloader + base entry BEFORE KernelCmdlineAction, which
        # then maintains the entry's kernel params.
        action_class=BootloaderAction,
        config_key='__root__',
        is_optional=False,
    )
    register_action(
        action_class=KernelCmdlineAction,
        config_key='__root__',
        is_optional=True,
    )


def execute_installation(config_file: str) -> bool:
    """Execute system installation from configuration file.
    
    Args:
        config_file: Path to JSON configuration file
        
    Returns:
        True if installation succeeded, False otherwise
    """
    # Parse configuration
    parser = JsonParser(config_file)
    config = parser.debug()

    # Populate shared context with root-level fields that some actions need
    executor = ActionExecutor(config)
    executor.context.set("drivers", config.get("drivers", []))
    executor.context.set("bootloader", config.get("bootloader", "grub"))

    return executor.execute_all()


class ActionsHandler:
    """Legacy handler for backward compatibility.
    
    This class maintains the same interface as the old ActionsHandler
    but uses the new architecture internally.
    """
    
    def __init__(self, filename: str):
        """Initialize and execute installation.
        
        Args:
            filename: Path to JSON configuration file
        """
        setup_actions()
        
        success = execute_installation(filename)
        
        if not success:
            raise RuntimeError("Installation failed - see errors above")

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
    from .packages_action import PackagesAction
    from .systemd_action import SystemdAction
    from .drop_files_action import DropFilesAction
    from .initramfs_action import InitramfsAction
    from .kernel_cmdline_action import KernelCmdlineAction
    from .bootloader_action import BootloaderAction
    from .ms_fonts_action import MicrosoftFontsAction

    # === Phase 1: disk & base install =====================================
    register_action(
        action_class=DiskPartitionAction,
        config_key='disks',
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
        # __root__: needs both the 'network' section and the root-level
        # 'hostname' (issue #66 port reads both from the root config).
        config_key='__root__',
        is_optional=True,
        required_fields=['network', 'hostname'],
    )
    register_action(
        action_class=PacmanAction,
        config_key='pacman',
        is_optional=True,
    )
    register_action(
        action_class=UsersAction,
        # __root__: needs the root-level remove_home_on_delete flag alongside
        # the users list (issue: per-user delete flag is unreadable at delete).
        config_key='__root__',
        is_optional=True,
    )

    # === Phase 3: package installation ====================================
    register_action(
        action_class=PackagesAction,
        config_key='packages',
        is_optional=True,
    )

    # === Phase 4: system services & files =================================
    register_action(
        action_class=SystemdAction,
        config_key='systemd',
        is_optional=True,
    )
    register_action(
        action_class=DropFilesAction,
        config_key='__root__',  # reads udev_rules, modprobe_conf, etc. from root
        is_optional=True,
    )
    register_action(
        action_class=MicrosoftFontsAction,
        config_key='microsoft_fonts',
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

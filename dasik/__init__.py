"""
DASIK - Arch Linux System Installer Kit

A Python-based tool for automated Arch Linux installation and system configuration.

New in this version:
- Idempotent architecture (NixOS-like)
- Action registry pattern for extensibility
- Safe to execute multiple times with same config
"""

def _resolve_version() -> str:
    """The installed distribution's version, never a second copy of it.

    This said "0.2.0" through 0.3.0, 0.4.0, 0.5.0 and 0.6.0 — the drift
    ``_version`` in __main__ already warns about, in the one place nobody
    reads often enough to notice. Asking the metadata means pyproject.toml stays
    the single source.

    The fallback is load-bearing: dasik runs from a bare source tree in the VM
    harness (``cd /root/repo && python -m dasik``), where no distribution is
    installed at all, and an unguarded lookup there would make `import dasik`
    itself raise.
    """
    try:
        from importlib.metadata import version
        return version("dasik")
    except Exception:      # nosec B110 - running from a source tree, uninstalled
        return "0.0.0+unknown"


__version__ = _resolve_version()
__author__ = "Andres"

# Export main APIs
from .lib.actions import (
    setup_actions,
    execute_installation,
    AbstractAction,
    register_action
)

__all__ = [
    'setup_actions',
    'execute_installation',
    'AbstractAction',
    'register_action',
]

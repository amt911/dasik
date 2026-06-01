"""Pluggable initramfs generator backends."""
from typing import Any, Dict
from .base import InitramfsBackend
from .mkinitcpio import MkinitcpioBackend
from .dracut import DracutBackend

_BACKENDS = {
    "mkinitcpio": MkinitcpioBackend,
    "dracut": DracutBackend,
}


def make_backend(name: str, config: Dict[str, Any], target=None) -> InitramfsBackend:
    try:
        cls = _BACKENDS[name]
    except KeyError:
        raise ValueError(
            f"unknown initramfs generator {name!r}; "
            f"known: {', '.join(sorted(_BACKENDS))}"
        )
    return cls(config, target)


__all__ = ["InitramfsBackend", "MkinitcpioBackend", "DracutBackend", "make_backend"]

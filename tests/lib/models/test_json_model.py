"""JsonModel — root config schema.

Boot-chain fields are restricted to the backends dasik implements: they used to
be free strings with a default, so a typo silently selected the default backend
(forensic report §9.9).
"""
import pytest
from pydantic import ValidationError

from dasik.lib.models.json_model import JsonModel

# --- boot-chain enums (forensic report §9.9) ------------------------------- #

def test_unknown_initramfs_generator_is_rejected():
    """`initramfs`/`bootloader` were free strings with defaults, so a typo picked
    the default backend silently instead of failing."""
    with pytest.raises(ValidationError):
        JsonModel.model_validate({"initramfs": "dracutt"})


def test_unknown_bootloader_is_rejected():
    with pytest.raises(ValidationError):
        JsonModel.model_validate({"bootloader": "refind"})


def test_supported_boot_values_are_accepted():
    for gen in ("mkinitcpio", "dracut"):
        assert JsonModel.model_validate({"initramfs": gen}).initramfs == gen
    for boot in ("grub", "sd-boot", "systemd-boot"):
        assert JsonModel.model_validate({"bootloader": boot}).bootloader == boot

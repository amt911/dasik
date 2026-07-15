"""Models for bluetooth configuration."""
from pydantic import BaseModel, Field


class BluetoothModel(BaseModel):
    """Bluetooth configuration."""
    enable: bool = Field(default=False)
    package: str = Field(default="bluez")
    in_initramfs: bool = Field(
        default=False,
        description="Include the bluetooth stack in the initramfs so a paired BT "
                    "keyboard works at the early LUKS/FIDO2 unlock prompt (dracut).",
    )

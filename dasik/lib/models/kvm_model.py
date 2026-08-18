"""Models for KVM configuration."""
from pydantic import BaseModel, Field


class KvmModel(BaseModel):
    """KVM virtualisation."""
    install: bool = Field(default=False)
    # Deliberately ORTHOGONAL to `install`. A config that carries libvirt as
    # literal packages (which is what `sync` captures) must be able to declare
    # the autostart without switching to the toggle: turning `install` on would
    # hand all thirteen KVM packages to the expansion and subtract them from
    # the captured list. See LibvirtNetworkAction.
    default_network: bool = Field(default=False)

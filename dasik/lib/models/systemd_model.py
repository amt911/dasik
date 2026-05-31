"""Models for systemd unit enablement."""
from typing import List
from pydantic import BaseModel, Field, model_validator


class SystemdModel(BaseModel):
    """Systemd services and sockets to enable, and units to disable."""
    enable_units: List[str] = Field(default_factory=list, description="Services/timers to enable")
    enable_sockets: List[str] = Field(default_factory=list, description="Sockets to enable")
    disable_units: List[str] = Field(default_factory=list, description="Units to ensure disabled")

    @model_validator(mode="after")
    def _no_enable_disable_overlap(self) -> "SystemdModel":
        enabled = set(self.enable_units) | set(self.enable_sockets)
        overlap = enabled & set(self.disable_units)
        if overlap:
            raise ValueError(
                f"units declared both enabled and disabled: {sorted(overlap)}"
            )
        return self

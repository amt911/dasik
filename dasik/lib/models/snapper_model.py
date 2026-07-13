"""Models for snapper (btrfs snapshot) configuration."""
from typing import List

from pydantic import BaseModel, Field


class SnapperConfig(BaseModel):
    """A single snapper config (e.g. 'root' for the '/' subvolume)."""

    name: str = Field(..., description="snapper config name, e.g. 'root'")
    subvolume: str = Field(..., description="Absolute path of the subvolume, e.g. '/'")


class SnapperModel(BaseModel):
    """snapper: automatic btrfs snapshots + timeline/cleanup timers."""

    enable: bool = Field(default=False)
    configs: List[SnapperConfig] = Field(
        default_factory=lambda: [SnapperConfig(name="root", subvolume="/")],
        description="snapper configs to create (defaults to root → /)",
    )

"""Models for snapper (btrfs snapshot) configuration."""
import re
from typing import List

from pydantic import BaseModel, Field, field_validator

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class SnapperConfig(BaseModel):
    """A single snapper config (e.g. 'root' for the '/' subvolume)."""

    name: str = Field(..., description="snapper config name, e.g. 'root'")
    subvolume: str = Field(..., description="Absolute path of the subvolume, e.g. '/'")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        """The name is interpolated into a filename
        (``/etc/snapper/configs/<name>``) and into command argv
        (``snapper -c <name> ...``, `rm -f`) by ``SnapperAction`` — a name
        with `/` or `..` would be a path built from an unvalidated value.
        snapper itself likely refuses illegal names at `create-config`, so a
        traversal name can probably never become OWNED in the first place,
        but this is cheap defense in depth given the REMOVE path deletes
        real snapshot subvolumes (N6)."""
        if not _NAME_RE.fullmatch(v):
            raise ValueError(
                f"Invalid snapper config name {v!r}: must match "
                f"{_NAME_RE.pattern!r} (it becomes a filename under "
                "/etc/snapper/configs and a shell argument to snapper/btrfs)."
            )
        return v


class SnapperModel(BaseModel):
    """snapper: automatic btrfs snapshots + timeline/cleanup timers."""

    enable: bool = Field(default=False)
    configs: List[SnapperConfig] = Field(
        default_factory=lambda: [SnapperConfig(name="root", subvolume="/")],
        description="snapper configs to create (defaults to root → /)",
    )

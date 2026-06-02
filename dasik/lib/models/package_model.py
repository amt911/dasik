"""Model for a package with an install reason (pacman only)."""
from typing import Literal
from pydantic import BaseModel


class PackageSpec(BaseModel):
    """A pacman package marked with an install reason.

    Plain strings in the packages list mean "explicit"; this object form marks a
    dependency (``reason="dep"``). AUR packages stay plain ``aur-`` strings.
    """
    name: str
    reason: Literal["explicit", "dep"] = "explicit"

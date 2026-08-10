"""Model for reflector — periodic pacman mirrorlist refresh."""
import re
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

_COUNTRY_RE = re.compile(r"^[A-Za-z][A-Za-z .'-]*$")


class ReflectorModel(BaseModel):
    """Options written to /etc/xdg/reflector/reflector.conf."""

    countries: List[str] = Field(default_factory=list,
                                 description="Mirror countries, e.g. ['ES', 'France']")
    protocols: List[Literal["https", "http", "rsync", "ftp"]] = Field(
        default_factory=lambda: ["https"])
    latest: Optional[int] = Field(20, ge=1, description="Keep the N most recently synced")
    sort: Literal["rate", "age", "score", "delay", "country"] = "rate"
    save: str = Field("/etc/pacman.d/mirrorlist", description="Mirrorlist to write")

    @field_validator("countries")
    @classmethod
    def _plain_countries(cls, v: List[str]) -> List[str]:
        # Each value becomes a `--country <value>` line in a config file
        # reflector parses as arguments; a newline would smuggle a second flag.
        for country in v:
            if not _COUNTRY_RE.match(country):
                raise ValueError(f"invalid country name: {country!r}")
        return v

    @field_validator("save")
    @classmethod
    def _absolute_save(cls, v: str) -> str:
        if not v.startswith("/") or "\n" in v:
            raise ValueError("save must be an absolute single-line path")
        return v

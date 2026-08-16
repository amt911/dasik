"""A WireGuard tunnel, declared as the file its backend already reads.

dasik never converts between the two formats the Arch wiki documents: a
wg-quick ``.conf`` is served by ``wg-quick@<name>.service``, a NetworkManager
``.nmconnection`` by NM's keyfile plugin, and a declaration that disagrees with
its file is an error rather than a translation.

Writing a file is also what makes an install-time apply possible for both:
NetworkManager reads its directory at startup, so no daemon and no ``nmcli``
are needed inside the chroot (``nmcli --offline connection import`` does not
exist).
"""
import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# IFNAMSIZ leaves 15 usable characters, and wg-quick names the interface after
# the file, so a longer name fails at `ip link add` — after the config was
# written and pacman had already run.
_NAME_RE = re.compile(r"[A-Za-z0-9_=+.-]{1,15}")


class WireguardTunnel(BaseModel):
    """One tunnel: a name, and the file that defines it."""

    name: str = Field(description="Interface / connection id (IFNAMSIZ: <=15)")
    source: str = Field(
        description="Path to the tunnel file, relative to the config that "
                    "names it. A wg-quick .conf or an NM .nmconnection.")
    backend: Literal["auto", "wg-quick", "networkmanager"] = Field(
        default="auto",
        description="'auto' reads the source file's own format, which is the "
                    "only thing that can serve it.")
    enable: bool = Field(
        default=True,
        description="wg-quick backend: enable wg-quick@<name>.service.")
    # Filled by the loader from `source` — only the loader knows where the
    # config file is, and therefore where a path relative to it points.
    content: Optional[str] = Field(
        default=None,
        description="Loader-filled body of the source file; not hand-written.")

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not _NAME_RE.fullmatch(v):
            raise ValueError(
                f"wireguard tunnel name {v!r} must be 1-15 characters of "
                "[A-Za-z0-9_=+.-] (IFNAMSIZ)")
        return v

    @field_validator("source")
    @classmethod
    def _relative_source(cls, v: str) -> str:
        if not v:
            raise ValueError("wireguard tunnel source must not be empty")
        if v.startswith("/"):
            raise ValueError(
                f"wireguard tunnel source {v!r} must be relative to the config "
                "that names it, not absolute")
        if ".." in v.split("/"):
            raise ValueError(
                f"wireguard tunnel source {v!r} must not contain '..' — a "
                "config may only pull in files at or below its own directory, "
                "and a tunnel file holds a private key")
        return v

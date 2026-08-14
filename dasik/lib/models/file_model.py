"""Model for a single declarative dropped file."""
import re
from posixpath import normpath
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class FileEntry(BaseModel):
    """One managed file: a filename (no path separators) and its content."""
    name: str = Field(..., description="Filename only, no path separators")
    content: str = Field(..., description="Verbatim file content")

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not v or "/" in v:
            raise ValueError("name must be a non-empty filename without '/'")
        return v


def _validate_mode(v: "Optional[str]") -> "Optional[str]":
    if v is None:
        return v
    try:
        int(v, 8)
    except (ValueError, TypeError):
        raise ValueError("mode must be an octal string, e.g. '0600'")
    return v


class EtcFile(BaseModel):
    """An arbitrary managed file by absolute path."""
    path: str = Field(..., description="Absolute target path")
    content: str = Field(..., description="Verbatim file content")
    mode: Optional[str] = Field(
        None,
        description="Octal file mode, e.g. '0600' (for secret files such as "
                    "wireguard/NetworkManager keyfiles). Default: umask.",
    )

    @field_validator("path")
    @classmethod
    def _abs_no_traversal(cls, v: str) -> str:
        if not v.startswith("/") or ".." in v.split("/"):
            raise ValueError("path must be absolute and contain no '..' segment")
        return v

    @field_validator("mode")
    @classmethod
    def _valid_mode(cls, v: "Optional[str]") -> "Optional[str]":
        return _validate_mode(v)


# useradd's own grammar (see UsersAction._validate_username): the name reaches
# an argv and is matched against /etc/passwd lines, so a leading '-' or a ':'
# is refused rather than escaped.
_VALID_USERNAME = re.compile(r"[a-z_][a-z0-9_-]*\$?")


class HomeFile(BaseModel):
    """A managed file inside a user's ``$HOME``.

    Addressed as (user, path-relative-to-home) rather than by absolute path:
    **the machine decides where a home is**, not the config. dasik reads the
    target's own ``/etc/passwd`` to resolve it, so a config stays correct on a
    machine whose homes are not under ``/home``.

    The relative path is what keeps this safe — an absolute path or a ``..``
    segment would let a home file write anywhere, so both are refused here.
    """
    user: str = Field(..., description="Owner; its home comes from /etc/passwd")
    path: str = Field(..., description="Path relative to the user's home")
    content: str = Field(..., description="Verbatim file content")
    mode: Optional[str] = Field(
        None, description="Octal file mode, e.g. '0600'. Default: umask.")

    @field_validator("user")
    @classmethod
    def _valid_user(cls, v: str) -> str:
        if not _VALID_USERNAME.fullmatch(v or ""):
            raise ValueError(
                f"invalid username {v!r}: must match [a-z_][a-z0-9_-]*$?")
        return v

    @field_validator("path")
    @classmethod
    def _relative_no_traversal(cls, v: str) -> str:
        if v.startswith("/"):
            raise ValueError(f"home file path must be relative to $HOME: {v!r}")
        if not v.strip():
            raise ValueError("home file path must not be empty")
        normalized = normpath(v)
        if normalized in (".", "..") or normalized.startswith("../"):
            raise ValueError(f"home file path must stay inside $HOME: {v!r}")
        return v

    @field_validator("mode")
    @classmethod
    def _valid_mode(cls, v: "Optional[str]") -> "Optional[str]":
        return _validate_mode(v)

"""Models for the `apparmor` block (Mandatory Access Control)."""
import re
from typing import List
from pydantic import BaseModel, Field, field_validator

# The name becomes a file under /etc/apparmor.d/, so it must stay a plain
# filename — a path separator there would let a config write anywhere on the
# target.
_PROFILE_NAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,128}")


class ApparmorProfile(BaseModel):
    """One profile file dropped into /etc/apparmor.d/."""
    name: str = Field(
        ...,
        description="File name under /etc/apparmor.d/ (e.g. 'usr.bin.foo')",
    )
    content: str = Field(..., description="Verbatim profile text")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not _PROFILE_NAME_RE.fullmatch(v):
            raise ValueError(
                f"Invalid AppArmor profile name {v!r}: must match "
                f"[A-Za-z0-9_.-]{{1,128}} — it is a file name under "
                f"/etc/apparmor.d, not a path."
            )
        return v


class ApparmorModel(BaseModel):
    """AppArmor. An absent block means AppArmor is not managed at all."""
    enable: bool = Field(
        default=True,
        description="Install AppArmor and make it the active LSM. Declaring the "
                    "block is the declaration; false keeps it here, turned off.",
    )
    audit: bool = Field(
        default=False,
        description="Also install the audit daemon, so denials are logged and "
                    "readable: auditd, the audit kernel parameters, and the "
                    "`audit` group for the declared users.",
    )
    extra_profiles: List[ApparmorProfile] = Field(
        default_factory=list,
        description="Profiles copied verbatim into /etc/apparmor.d/. They load "
                    "at the next boot (AppArmor does not run in the chroot).",
    )

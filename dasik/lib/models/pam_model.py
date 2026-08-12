"""Models for the `pam` block (account lockout, resource limits, password policy).

Three independent sub-blocks, each optional: an absent one is not the empty one.
Only `pwquality` touches anything under /etc/pam.d, and only the file the
`passwd` command reads — the login stack is never edited, because a mistake
there is a machine nobody can log into.
"""
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class FaillockModel(BaseModel):
    """`/etc/security/faillock.conf` — pam_faillock is already in Arch's stack."""
    deny: int = Field(
        default=5,
        description="Failed attempts before the account locks. Three is easy to "
                    "burn with a long passphrase and the wrong keyboard layout.",
    )
    fail_interval: int = Field(
        default=900,
        description="Seconds within which the failures must happen to count.",
    )
    unlock_time: int = Field(
        default=600,
        description="Seconds the account stays locked.",
    )
    persistent: bool = Field(
        default=True,
        description="Keep the failure records in /var/lib/faillock instead of "
                    "/run, so a reboot does not clear the lockout. An attacker "
                    "who can power-cycle the machine can clear /run.",
    )

    @field_validator("deny")
    @classmethod
    def _validate_deny(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(
                "deny must be at least 1: pam_faillock reads deny=0 as "
                "'disable the lockout entirely', which is the opposite of "
                "declaring this block. Drop the faillock section instead."
            )
        return v

    @field_validator("fail_interval", "unlock_time")
    @classmethod
    def _validate_seconds(cls, v: int) -> int:
        if v < 0:
            raise ValueError("must be a non-negative number of seconds")
        return v


class LimitsModel(BaseModel):
    """`/etc/security/limits.d/10-dasik.conf` — a cap on runaway process counts."""
    nproc_soft: int = Field(
        default=100,
        description="Soft process limit per user (raisable with prlimit).",
    )
    nproc_hard: int = Field(
        default=200,
        description="Hard process limit per user — the ceiling a fork bomb hits.",
    )

    @field_validator("nproc_soft", "nproc_hard")
    @classmethod
    def _validate_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("process limits must be positive")
        return v


class PwqualityModel(BaseModel):
    """`/etc/security/pwquality.conf.d/10-dasik.conf` + the `passwd` PAM stack.

    The credits are pwquality's own spelling: a NEGATIVE value means "require at
    least this many characters of that class", a positive one means "credit up to
    this many towards the length". -1 is the usual intent.
    """
    enable: bool = Field(default=True, description="Enforce the policy at `passwd` time")
    minlen: int = Field(default=10, description="Minimum password length")
    difok: int = Field(default=6, description="Characters that must differ from the old one")
    retry: int = Field(default=2, description="Prompts before `passwd` gives up")
    enforce_for_root: bool = Field(
        default=False,
        description="Apply the policy to root too. Off by default: root setting a "
                    "deliberately weak temporary password for someone else is a "
                    "legitimate thing to do.",
    )
    dcredit: int = Field(default=-1, description="Digits (negative = require)")
    ucredit: int = Field(default=-1, description="Uppercase (negative = require)")
    lcredit: int = Field(default=-1, description="Lowercase (negative = require)")
    ocredit: int = Field(default=-1, description="Other characters (negative = require)")

    @field_validator("minlen")
    @classmethod
    def _validate_minlen(cls, v: int) -> int:
        if v < 6:
            raise ValueError(
                "minlen below 6 is below pwquality's own floor and would make "
                "the policy meaningless"
            )
        return v


class PamModel(BaseModel):
    """PAM hardening. Every sub-block is optional and independent."""
    faillock: Optional[FaillockModel] = None
    limits: Optional[LimitsModel] = None
    pwquality: Optional[PwqualityModel] = None

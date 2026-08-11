"""Models for user configuration."""
from typing import List
from pydantic import BaseModel, Field, field_validator


class UserModel(BaseModel):
    """A system user to create. Password is stored already hashed."""
    username: str = Field(..., description="Login name")
    hashed_password: str = Field(
        ...,
        description="Crypt hash ($y$… yescrypt, as Arch writes it, or $6$… "
                    "sha512crypt); generate one with `dasik hash-password`")
    shell: str = Field(default="/bin/bash", description="Login shell path")
    groups: List[str] = Field(default_factory=list, description="Supplementary groups")

    @field_validator("hashed_password")
    @classmethod
    def _must_be_hash(cls, v: str) -> str:
        if not v.startswith("$"):
            raise ValueError(
                "hashed_password must be a crypt hash (e.g. $6$...); "
                "plaintext passwords are not accepted"
            )
        return v

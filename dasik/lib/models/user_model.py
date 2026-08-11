"""Models for user configuration."""
from typing import List
from pydantic import BaseModel, Field, field_validator, model_validator

_ROOT = "root"
_DEFAULT_SHELL = "/bin/bash"


class UserModel(BaseModel):
    """A system user to create. Password is stored already hashed."""
    username: str = Field(..., description="Login name")
    hashed_password: str = Field(
        ...,
        description="Crypt hash ($y$… yescrypt, as Arch writes it, or $6$… "
                    "sha512crypt); generate one with `dasik hash-password`")
    shell: str = Field(default=_DEFAULT_SHELL, description="Login shell path")
    groups: List[str] = Field(default_factory=list, description="Supplementary groups")

    @model_validator(mode="after")
    def _root_is_password_only(self) -> "UserModel":
        """``root`` may declare a password and nothing else.

        ``UsersAction`` never runs ``useradd``/``usermod -s``/``usermod -G`` for
        root — only ``usermod -p`` — so a shell or a group list here would be
        accepted and then silently ignored, which is exactly the ambiguity a
        declarative config must not have.
        """
        if self.username != _ROOT:
            return self
        extras = []
        if self.shell != _DEFAULT_SHELL:
            extras.append("shell")
        if self.groups:
            extras.append("groups")
        if extras:
            raise ValueError(
                f"user 'root' may only declare a password: {', '.join(extras)} "
                f"{'are' if len(extras) > 1 else 'is'} not managed for root "
                "(dasik runs `usermod -p` and nothing else). Remove "
                f"{'them' if len(extras) > 1 else 'it'}."
            )
        return self

    @field_validator("hashed_password")
    @classmethod
    def _must_be_hash(cls, v: str) -> str:
        if not v.startswith("$"):
            raise ValueError(
                "hashed_password must be a crypt hash (e.g. $6$...); "
                "plaintext passwords are not accepted"
            )
        return v

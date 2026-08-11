"""Model for the sudoers fragment dasik owns (/etc/sudoers.d/10-dasik).

Putting a user in `wheel` does nothing on stock Arch: `%wheel` ships commented
out in /etc/sudoers, so a declared administrator could not `sudo` at all. This
block is what turns the group membership into actual sudo access.
"""
from typing import List

from pydantic import BaseModel, Field, field_validator

# An include directive would pull rules dasik neither renders nor tracks into
# the fragment it validates — the fragment must be self-contained.
_FORBIDDEN_PREFIXES = ("@include", "#include")


class SudoModel(BaseModel):
    """Declarative sudo access."""

    wheel: bool = Field(True, description="Grant sudo to the wheel group")
    nopasswd: bool = Field(False, description="wheel sudo without a password prompt")
    rules: List[str] = Field(
        default_factory=list,
        description="Extra sudoers lines, written verbatim after the wheel rule",
    )

    @field_validator("rules")
    @classmethod
    def _single_line_rules(cls, v: List[str]) -> List[str]:
        for rule in v:
            if not rule.strip():
                raise ValueError("a sudoers rule must not be empty")
            if "\n" in rule or "\r" in rule:
                raise ValueError(f"a sudoers rule must be a single line: {rule!r}")
            if rule.strip().lower().startswith(_FORBIDDEN_PREFIXES):
                raise ValueError(f"include directives are not allowed in rules: {rule!r}")
        return v

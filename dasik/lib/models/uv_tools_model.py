"""Model for the ``uv_tools`` block — Python programs installed per user by uv.

Not everything a machine needs is a pacman package. Some upstreams ship a
Python program and say, in their own documentation, to install it into an
isolated per-user environment: graphify's README recommends ``uv tool install
graphifyy`` and never mentions Arch, and the AUR build of it drags in 26
tree-sitter grammars that live in no official repository — 27 builds inside an
unattended install, for a tool that updates weekly.

So this domain declares those programs, per user, the way their authors ship
them. It is deliberately small: names, not versions (though a pin is allowed,
since uv is the only thing that would move it), and no build options.
"""
import re
from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# A distribution name as it reaches `uv tool install`: the PyPI name, optionally
# with extras and a version specifier. It is a command-line argument, so nothing
# that could be read as shell syntax is allowed through.
_TOOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*(\[[A-Za-z0-9,._-]+\])?"
                      r"([=<>!~]=?[A-Za-z0-9._*+-]+)?$")


class UvToolsModel(BaseModel):
    """The ``uv_tools`` block."""

    model_config = ConfigDict(extra="forbid")

    users: List[str] = Field(
        default_factory=list,
        description="Whose $HOME receives them. Empty = every declared user "
                    "except root. `uv tool` installs into ~/.local/share/uv, so "
                    "these are per-user by construction.")
    failure_policy: Literal["warn-and-continue", "abort"] = Field(
        "warn-and-continue",
        description="What an apply does when `uv tool install` fails (no "
                    "network, no uv, a broken sdist). The default warns, keeps "
                    "going and leaves the tool unowned, so the next plan asks "
                    "again.")
    tools: List[str] = Field(
        default_factory=list,
        description="Distribution names as `uv tool install` takes them — the "
                    "PyPI name, not the command it provides (graphifyy, whose "
                    "command is `graphify`). Extras and a version pin are "
                    "allowed: 'semgrep[all]', 'graphifyy==0.9.53'.")

    @field_validator("users")
    @classmethod
    def _no_duplicate_users(cls, value: List[str]) -> List[str]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate user in `users`")
        return value

    @field_validator("tools")
    @classmethod
    def _valid_tool_names(cls, value: List[str]) -> List[str]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate tool in `tools`")
        for tool in value:
            if not _TOOL_RE.match(tool):
                raise ValueError(
                    f"{tool!r} is not a distribution name `uv tool install` "
                    "would take. It reaches a command line, so anything that "
                    "could be read as shell syntax is refused.")
        return value

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
from typing import List, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

# A distribution name as it reaches `uv tool install`: the PyPI name, optionally
# with extras and a version specifier. It is a command-line argument, so nothing
# that could be read as shell syntax is allowed through.
_TOOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*(\[[A-Za-z0-9,._-]+\])?"
                      r"([=<>!~]=?[A-Za-z0-9._*+-]+)?$")

# `--python` takes what uv calls a version request. Only `major.minor` is
# accepted here: a bare `3.13` is what a pin means, and anything longer (a path,
# an implementation, a patch release) is either not portable across machines or
# not a request uv would honour the same way twice.
_PYTHON_RE = re.compile(r"^\d+\.\d+$")


class UvToolModel(BaseModel):
    """One tool that needs an interpreter other than uv's default.

    uv installs with whatever `python` it picks, and for one real tool that is
    the wrong one: `inkscape_mcp` depends on `inkex`, which pins `lxml 5.4.0`,
    which ships no cp314 wheel — so on a machine whose default is 3.14 the
    install tries to compile lxml and fails. That is desired state, not a note
    in a README, so it is declared.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="The distribution name, exactly as the plain "
                                  "string form takes it.")
    python: str = Field(description="The interpreter uv installs it with, as "
                                    "`major.minor` — it reaches `uv tool "
                                    "install --python <value>`.")

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        if not _TOOL_RE.match(value):
            raise ValueError(
                f"{value!r} is not a distribution name `uv tool install` would "
                "take. It reaches a command line, so anything that could be "
                "read as shell syntax is refused.")
        return value

    @field_validator("python")
    @classmethod
    def _valid_python(cls, value: str) -> str:
        if not _PYTHON_RE.match(value):
            raise ValueError(
                f"{value!r} is not a `major.minor` interpreter version "
                "(e.g. '3.13').")
        return value


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
    tools: List[Union[str, UvToolModel]] = Field(
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
    def _valid_tool_names(cls, value: List[Union[str, UvToolModel]]) \
            -> List[Union[str, UvToolModel]]:
        names = [t.name if isinstance(t, UvToolModel) else t for t in value]
        if len(set(names)) != len(names):
            raise ValueError("duplicate tool in `tools`")
        for tool in names:
            if not _TOOL_RE.match(tool):
                raise ValueError(
                    f"{tool!r} is not a distribution name `uv tool install` "
                    "would take. It reaches a command line, so anything that "
                    "could be read as shell syntax is refused.")
        return value

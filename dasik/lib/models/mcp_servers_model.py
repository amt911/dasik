"""Model for the ``mcp_servers`` block — MCP servers per agent, per user.

An MCP server is registered, never installed: ``claude mcp add`` and
``codex mcp add`` write a line into each agent's own configuration, and what
the line points at (``uvx inkscape_mcp``, a URL) is fetched by the agent when it
starts the server. So the declaration names the *command* or the *URL*, and
never a version — ``uvx`` owns that the way pacman owns a package's.

The two CLIs are not symmetric, and the model refuses to paper over it: only
Claude Code takes ``--header``, only codex takes ``--bearer-token-env-var``. An
entry naming one of them for both agents would plan a registration that arrives
without it, which is the class of bug where ``apply`` reports success forever
and the server never authenticates.

Neither registry file is dasik's to write: ``~/.claude.json`` carries account
material and per-project history, ``~/.codex/config.toml`` carries project trust
levels and hook hashes. dasik reads them and drives the official CLI.
"""
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# The agents dasik knows how to drive. Another id is not a limitation to work
# around: there is no MCP CLI to run, so a declaration naming one would be a
# promise nothing keeps.
AGENTS = ("claude-code", "codex")

# Which agent can be told what, measured from `claude mcp add --help` and
# `codex mcp add --help`.
_HEADERS_AGENT = "claude-code"
_BEARER_AGENT = "codex"


class McpServerEntry(BaseModel):
    """One MCP server, for the agents that should talk to it."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1,
                      description="Server name as the agent registers it "
                                  "(e.g. 'inkscape_mcp')")
    agents: List[str] = Field(
        ..., description="Agent ids: claude-code, codex.")
    command: Optional[str] = Field(
        None, min_length=1,
        description="stdio transport: the program that serves MCP on stdio "
                    "(e.g. 'uvx'). Mutually exclusive with `url`.")
    args: List[str] = Field(
        default_factory=list,
        description="Arguments for `command` (e.g. ['inkscape_mcp']).")
    env: Dict[str, str] = Field(
        default_factory=dict,
        description="Environment for `command`. CAPTURED VERBATIM by `sync`: a "
                    "config holding an API key here is private.")
    url: Optional[str] = Field(
        None, min_length=1,
        description="http transport: the streamable HTTP endpoint. Mutually "
                    "exclusive with `command`.")
    headers: Dict[str, str] = Field(
        default_factory=dict,
        description="`url` + claude-code only: extra HTTP headers.")
    bearer_token_env_var: Optional[str] = Field(
        None, min_length=1,
        description="`url` + codex only: the variable codex reads the bearer "
                    "token from.")
    users: List[str] = Field(
        default_factory=list,
        description="Only these users get this entry. Empty = the block's "
                    "`users`. It is what lets a machine where one person has a "
                    "server and another does not be captured exactly.")

    @field_validator("agents")
    @classmethod
    def _known_agents(cls, value: List[str]) -> List[str]:
        if not value:
            raise ValueError("at least one agent in `agents` "
                             f"({', '.join(AGENTS)})")
        if len(set(value)) != len(value):
            raise ValueError("duplicate agent in `agents`")
        unknown = [a for a in value if a not in AGENTS]
        if unknown:
            raise ValueError(
                f"unknown agent(s) {', '.join(unknown)}: dasik can only drive "
                f"{', '.join(AGENTS)} — there is no MCP CLI for the rest.")
        return value

    @field_validator("users")
    @classmethod
    def _no_duplicate_users(cls, value: List[str]) -> List[str]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate user in `users`")
        return value

    @model_validator(mode="after")
    def _coherent(self) -> "McpServerEntry":
        if bool(self.command) == bool(self.url):
            raise ValueError(
                "exactly one of `command` (stdio) or `url` (http) is required")
        if self.url:
            if self.args:
                raise ValueError("`args` belongs to `command`, not to `url`")
            if self.env:
                raise ValueError("`env` belongs to `command`, not to `url`")
        else:
            if self.headers:
                raise ValueError("`headers` belongs to `url`, not to `command`")
            if self.bearer_token_env_var:
                raise ValueError("`bearer_token_env_var` belongs to `url`, not "
                                 "to `command`")
        if self.headers and self.agents != [_HEADERS_AGENT]:
            raise ValueError(
                "`headers` is a claude-code option (`claude mcp add -H`); an "
                f"entry using it must declare agents: ['{_HEADERS_AGENT}'] "
                "alone. Split the server into one entry per agent.")
        if self.bearer_token_env_var and self.agents != [_BEARER_AGENT]:
            raise ValueError(
                "`bearer_token_env_var` is a codex option (`codex mcp add "
                f"--bearer-token-env-var`); an entry using it must declare "
                f"agents: ['{_BEARER_AGENT}'] alone. Split the server into one "
                "entry per agent.")
        return self

    @property
    def transport(self) -> str:
        """``http`` when the entry names a URL, ``stdio`` otherwise.

        Derived, never declared: a field would let a config contradict itself.
        """
        return "http" if self.url else "stdio"


class McpServersModel(BaseModel):
    """The ``mcp_servers`` block."""

    model_config = ConfigDict(extra="forbid")

    users: List[str] = Field(
        default_factory=list,
        description="Whose agents get them. Empty = every declared user with "
                    "uid >= 1000 (the humans): an MCP server is a tool a person "
                    "uses, and it lives in their $HOME.")
    failure_policy: Literal["warn-and-continue", "abort"] = Field(
        "warn-and-continue",
        description="What an apply does when the agent's CLI fails (not "
                    "installed, not logged in). The default keeps the rest of "
                    "the apply going and leaves the domain unconverged, so the "
                    "next plan asks again.")
    entries: List[McpServerEntry] = Field(default_factory=list)

    @field_validator("users")
    @classmethod
    def _no_duplicate_users(cls, value: List[str]) -> List[str]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate user in `users`")
        return value

    @model_validator(mode="after")
    def _one_registration_per_name_and_agent(self) -> "McpServersModel":
        # Deliberately coarser than `_desired()` needs: two entries with the
        # same (name, agent) and DISJOINT `users` could not collide in practice,
        # and are still refused. One name is one server, and a config where the
        # answer to "what is `inkscape_mcp`?" depends on who is reading is a
        # config nobody can reason about — split the name instead.
        seen = set()
        for entry in self.entries:
            for agent in entry.agents:
                key = (entry.name, agent)
                if key in seen:
                    raise ValueError(
                        f"server '{entry.name}' is declared twice for agent "
                        f"'{agent}': one name is one registration, so the "
                        "second would silently replace the first.")
                seen.add(key)
        return self

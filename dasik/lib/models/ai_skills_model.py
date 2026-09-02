"""Model for the ``ai_skills`` block — AI agent skills and plugins, per agent.

Every skill in this ecosystem has a different official installer, and which one
applies depends on the *pair* (skill, agent): superpowers is a plugin on both
Claude Code and Codex, caveman is a Claude plugin but a plain skill on Codex,
impeccable is a skill on both. So an entry names the artefact **and** the method
that installs it, and dasik drives that method's own CLI rather than unpacking
files itself.

**Presence, never version.** There is deliberately no ``version`` field. The
reason the official CLI is driven at all is so ``claude plugin update`` /
``npx skills update`` remain the user's; a version pinned here would turn every
one of those updates into drift the next ``plan`` reverts. This mirrors
``packages``, which names packages and lets pacman own the versions.
"""
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# The three installers dasik knows how to drive. `skills` is the cross-agent one
# (npm `skills`, vercel-labs/skills): its agent ids are strings it defines, so a
# new agent needs no code here.
Method = Literal["claude-plugin", "codex-plugin", "skills"]

# Which agent a plugin method installs for. `skills` carries its own list.
METHOD_AGENT = {"claude-plugin": "claude-code", "codex-plugin": "codex"}


class MarketplaceRef(BaseModel):
    """A plugin marketplace: its registered name, and where it came from.

    ``source`` is absent for a marketplace the agent ships with (Codex's
    ``openai-curated``), and required for one dasik has to register with
    ``... plugin marketplace add``.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1,
                      description="Marketplace name as the agent registers it "
                                  "(e.g. 'caveman', 'claude-plugins-official')")
    source: Optional[str] = Field(
        None, description="owner/repo, git URL or path to register it from. "
                          "Omit for a marketplace the agent ships with.")


class AiSkillEntry(BaseModel):
    """One artefact, installed one way, for the agents that method reaches."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1,
                      description="Skill or plugin name as the installer knows it")
    method: Method = Field(..., description="claude-plugin | codex-plugin | skills")
    plugin: Optional[str] = Field(
        None, description="Plugin name inside the marketplace, when it differs "
                          "from `name`. Defaults to `name`.")
    marketplace: Optional[MarketplaceRef] = Field(
        None, description="Required by the plugin methods; rejected by `skills`.")
    source: Optional[str] = Field(
        None, description="Required by `skills`: owner/repo, git URL or path the "
                          "`skills` CLI installs from.")
    agents: List[str] = Field(
        default_factory=list,
        description="`skills` only: agent ids of the `skills` CLI "
                    "(claude-code, codex, opencode, cursor, ...).")

    @field_validator("agents")
    @classmethod
    def _no_duplicate_agents(cls, value: List[str]) -> List[str]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate agent in `agents`")
        return value

    @model_validator(mode="after")
    def _coherent(self) -> "AiSkillEntry":
        if self.method == "skills":
            if not self.source:
                raise ValueError("method 'skills' requires `source` "
                                 "(owner/repo, git URL or path)")
            if not self.agents:
                raise ValueError("method 'skills' requires at least one agent "
                                 "in `agents`")
            if self.marketplace is not None:
                raise ValueError("method 'skills' takes no `marketplace` — "
                                 "marketplaces belong to the plugin methods")
        else:
            if self.marketplace is None:
                raise ValueError(f"method '{self.method}' requires a "
                                 "`marketplace` (at least its name)")
            if self.agents:
                # The agent is implied by the method. Accepting a list would let
                # a config claim `claude-plugin` installs into codex, which the
                # CLI cannot do, and the plan would promise it anyway.
                raise ValueError(f"method '{self.method}' takes no `agents`: it "
                                 f"installs for {METHOD_AGENT[self.method]}")
        if self.plugin is None:
            object.__setattr__(self, "plugin", self.name)
        return self

    @property
    def agent_ids(self) -> List[str]:
        """Agents this entry installs for, whichever method it uses."""
        return self.agents if self.method == "skills" else [METHOD_AGENT[self.method]]


class AiSkillsModel(BaseModel):
    """The ``ai_skills`` block."""

    model_config = ConfigDict(extra="forbid")

    users: List[str] = Field(
        default_factory=list,
        description="Whose $HOME receives them. Empty = every declared user "
                    "with uid >= 1000 (the humans), which is what 'system-wide' "
                    "means for artefacts that live in a home directory.")
    failure_policy: Literal["warn-and-continue", "abort"] = Field(
        "warn-and-continue",
        description="What an apply does when an installer fails (no network, no "
                    "node, marketplace down). The default keeps the rest of the "
                    "apply going and leaves the domain unconverged, so the next "
                    "plan asks again — a markdown file must not abort a system "
                    "installation.")
    entries: List[AiSkillEntry] = Field(default_factory=list)

    @field_validator("users")
    @classmethod
    def _no_duplicate_users(cls, value: List[str]) -> List[str]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate user in `users`")
        return value

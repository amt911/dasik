"""The `ai_skills` block — what a valid declaration looks like, and what is not.

The block declares PRESENCE, never a version: the whole point of driving each
agent's official CLI is that `claude plugin update` / `npx skills update` stay
the user's, and a pinned version here would turn every official update into
drift the next plan reverts.
"""
import pytest
from pydantic import ValidationError

from dasik.lib.models.ai_skills_model import AiSkillsModel


def _model(**over):
    base = {"entries": [{"name": "superpowers", "method": "claude-plugin",
                         "marketplace": {"name": "claude-plugins-official",
                                         "source": "anthropics/claude-plugins-official"}}]}
    base.update(over)
    return AiSkillsModel(**base)


def test_defaults_are_empty_users_and_warn_and_continue():
    model = _model()
    assert model.users == []
    assert model.failure_policy == "warn-and-continue"


def test_the_plugin_name_defaults_to_the_entry_name():
    assert _model().entries[0].plugin == "superpowers"


def test_a_marketplace_may_name_the_plugin_differently():
    model = _model(entries=[{"name": "impeccable-style", "method": "claude-plugin",
                             "plugin": "impeccable",
                             "marketplace": {"name": "impeccable",
                                             "source": "pbakaus/impeccable"}}])
    assert model.entries[0].plugin == "impeccable"


def test_skills_method_requires_a_source():
    with pytest.raises(ValidationError):
        _model(entries=[{"name": "impeccable", "method": "skills",
                         "agents": ["codex"]}])


def test_skills_method_requires_at_least_one_agent():
    with pytest.raises(ValidationError):
        _model(entries=[{"name": "impeccable", "method": "skills",
                         "source": "pbakaus/impeccable"}])


def test_plugin_methods_require_a_marketplace():
    with pytest.raises(ValidationError):
        _model(entries=[{"name": "caveman", "method": "codex-plugin"}])


def test_plugin_methods_reject_an_agents_list():
    # The agent is implied by the method; accepting a list here would let a
    # config claim `claude-plugin` installs into codex, which it cannot.
    with pytest.raises(ValidationError):
        _model(entries=[{"name": "caveman", "method": "claude-plugin",
                         "marketplace": {"name": "caveman"},
                         "agents": ["claude-code"]}])


def test_an_unknown_method_is_rejected():
    with pytest.raises(ValidationError):
        _model(entries=[{"name": "x", "method": "gemini-extension"}])


def test_a_builtin_marketplace_needs_no_source():
    model = _model(entries=[{"name": "superpowers", "method": "codex-plugin",
                             "marketplace": {"name": "openai-curated"}}])
    assert model.entries[0].marketplace.source is None


def test_a_skills_entry_rejects_a_marketplace():
    with pytest.raises(ValidationError):
        _model(entries=[{"name": "impeccable", "method": "skills",
                         "source": "pbakaus/impeccable", "agents": ["codex"],
                         "marketplace": {"name": "impeccable"}}])


def test_duplicate_users_are_rejected():
    with pytest.raises(ValidationError):
        _model(users=["andres", "andres"])


def test_duplicate_agents_are_rejected():
    with pytest.raises(ValidationError):
        _model(entries=[{"name": "impeccable", "method": "skills",
                         "source": "pbakaus/impeccable",
                         "agents": ["codex", "codex"]}])


def test_an_unknown_key_is_rejected():
    # A typo that validated would converge to nothing and never say so.
    with pytest.raises(ValidationError):
        _model(entries=[{"name": "caveman", "method": "skills",
                         "source": "JuliusBrussee/caveman", "agents": ["codex"],
                         "verison": "2.4.0"}])


def test_an_empty_name_is_rejected():
    with pytest.raises(ValidationError):
        _model(entries=[{"name": "", "method": "skills",
                         "source": "pbakaus/impeccable", "agents": ["codex"]}])


def test_a_version_key_is_rejected_on_purpose():
    """Presence, never version — see the module docstring."""
    with pytest.raises(ValidationError):
        _model(entries=[{"name": "superpowers", "method": "codex-plugin",
                         "marketplace": {"name": "openai-curated"},
                         "version": "6.3.0"}])


def test_the_block_accepts_an_explicit_user_list():
    assert _model(users=["andres", "otro"]).users == ["andres", "otro"]


def test_failure_policy_abort_is_accepted():
    assert _model(failure_policy="abort").failure_policy == "abort"


def test_an_unknown_failure_policy_is_rejected():
    with pytest.raises(ValidationError):
        _model(failure_policy="retry-forever")


# --- per-entry users ------------------------------------------------------- #
# Without this the block could only say "everyone gets everything", and a sync
# of a machine where one user has caveman and another does not would capture a
# config that re-plans changes — breaking sync -> plan silence.

def test_an_entry_may_name_its_own_users():
    model = _model(users=["andres", "otro"],
                   entries=[{"name": "caveman", "method": "skills",
                             "source": "JuliusBrussee/caveman",
                             "agents": ["codex"], "users": ["andres"]}])
    assert model.entries[0].users == ["andres"]


def test_an_entry_defaults_to_the_blocks_users():
    assert _model().entries[0].users == []


def test_duplicate_users_in_an_entry_are_rejected():
    with pytest.raises(ValidationError):
        _model(entries=[{"name": "caveman", "method": "skills",
                         "source": "JuliusBrussee/caveman",
                         "agents": ["codex"], "users": ["a", "a"]}])


# --- the `tool` method ----------------------------------------------------- #
# Some skills are shipped BY a program: `graphify` is an AUR/pip package whose
# `graphify install --platform <p>` writes the skill file that matches the
# installed version. Declaring it from a git branch instead would pin a skill to
# a tool version nobody checked.

def test_a_tool_entry_needs_a_command_and_agents():
    model = _model(entries=[{"name": "graphify", "method": "tool",
                             "command": "graphify",
                             "agents": ["claude-code", "codex"]}])
    assert model.entries[0].command == "graphify"


def test_a_tool_entry_without_a_command_is_rejected():
    with pytest.raises(ValidationError):
        _model(entries=[{"name": "graphify", "method": "tool",
                         "agents": ["codex"]}])


def test_a_tool_entry_without_agents_is_rejected():
    with pytest.raises(ValidationError):
        _model(entries=[{"name": "graphify", "method": "tool",
                         "command": "graphify"}])


def test_a_tool_command_must_be_a_bare_program_name():
    # It is executed; anything with a space, a slash or a shell metacharacter is
    # refused rather than quoted and hoped for.
    for bad in ("graphify install", "/usr/bin/graphify", "graphify;rm -rf /",
                "graph$ify", "graphify&&x"):
        with pytest.raises(ValidationError):
            _model(entries=[{"name": "graphify", "method": "tool",
                             "command": bad, "agents": ["codex"]}])


def test_a_tool_entry_rejects_a_marketplace_and_a_source():
    with pytest.raises(ValidationError):
        _model(entries=[{"name": "graphify", "method": "tool",
                         "command": "graphify", "agents": ["codex"],
                         "marketplace": {"name": "x"}}])
    with pytest.raises(ValidationError):
        _model(entries=[{"name": "graphify", "method": "tool",
                         "command": "graphify", "agents": ["codex"],
                         "source": "owner/repo"}])


def test_the_other_methods_reject_a_command():
    with pytest.raises(ValidationError):
        _model(entries=[{"name": "impeccable", "method": "skills",
                         "source": "pbakaus/impeccable", "agents": ["codex"],
                         "command": "impeccable"}])

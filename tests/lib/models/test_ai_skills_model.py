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

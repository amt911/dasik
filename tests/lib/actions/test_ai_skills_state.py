"""Reading the three agents' own state files.

These files belong to other programs — dasik reads them and never writes them,
so every reader has to survive a file that is absent, truncated, or in a shape
a future version invented. "Unreadable" always means "not installed": the worst
that costs is a redundant install command; the opposite (guessing installed)
would make a plan silent about something that was never there.
"""
import json

from dasik.lib.actions.ai_skills_state import (
    AGENT_SKILL_DIRS, UNIVERSAL_AGENTS, carries_skill, claude_state,
    codex_state, installed_agents, skills_state)


def _claude_home(tmp_path):
    (tmp_path / ".claude/plugins").mkdir(parents=True)
    return tmp_path


def _write_skill(directory, name):
    target = directory / name
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    return target


# --- claude ---------------------------------------------------------------- #

def test_claude_state_reads_installed_plugins_and_marketplaces(tmp_path):
    home = _claude_home(tmp_path)
    (home / ".claude/plugins/installed_plugins.json").write_text(json.dumps({
        "version": 2,
        "plugins": {"caveman@caveman": [{"scope": "user"}],
                    "superpowers@claude-plugins-official": [{"scope": "user"}]}}))
    (home / ".claude/plugins/known_marketplaces.json").write_text(json.dumps({
        "caveman": {"source": {"source": "github", "repo": "JuliusBrussee/caveman"},
                    "installLocation": "/home/andres/.claude/plugins/marketplaces/caveman"}}))

    plugins, markets = claude_state(str(home))

    assert plugins == {"caveman@caveman", "superpowers@claude-plugins-official"}
    assert markets == {"caveman": "JuliusBrussee/caveman"}


def test_claude_state_on_a_machine_without_claude_is_empty(tmp_path):
    assert claude_state(str(tmp_path)) == (set(), {})


def test_claude_state_survives_a_corrupt_json(tmp_path):
    home = _claude_home(tmp_path)
    (home / ".claude/plugins/installed_plugins.json").write_text("{not json")
    assert claude_state(str(home)) == (set(), {})


def test_claude_state_ignores_a_plugin_with_no_installation_left(tmp_path):
    # The file keeps the key with an empty list after an uninstall.
    home = _claude_home(tmp_path)
    (home / ".claude/plugins/installed_plugins.json").write_text(json.dumps({
        "version": 2, "plugins": {"caveman@caveman": []}}))
    plugins, _ = claude_state(str(home))
    assert plugins == set()


def test_claude_state_reads_a_marketplace_given_as_a_url(tmp_path):
    home = _claude_home(tmp_path)
    (home / ".claude/plugins/known_marketplaces.json").write_text(json.dumps({
        "local": {"source": {"source": "url",
                             "url": "https://example.com/marketplace.json"}}}))
    _plugins, markets = claude_state(str(home))
    assert markets == {"local": "https://example.com/marketplace.json"}


# --- codex ----------------------------------------------------------------- #

def test_codex_state_reads_enabled_plugins_only(tmp_path):
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex/config.toml").write_text(
        'model = "gpt-5.6-sol"\n'
        '\n[plugins."superpowers@openai-curated"]\nenabled = true\n'
        '\n[plugins."old@mkt"]\nenabled = false\n')

    plugins, _markets = codex_state(str(tmp_path))

    assert plugins == {"superpowers@openai-curated"}


def test_codex_state_reads_configured_marketplaces(tmp_path):
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex/config.toml").write_text(
        '[plugin_marketplaces.caveman]\n'
        'source = "https://github.com/JuliusBrussee/caveman"\n')
    _plugins, markets = codex_state(str(tmp_path))
    assert markets == {"caveman": "https://github.com/JuliusBrussee/caveman"}


def test_codex_state_on_a_machine_without_codex_is_empty(tmp_path):
    assert codex_state(str(tmp_path)) == (set(), {})


def test_codex_state_survives_a_broken_toml(tmp_path):
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex/config.toml").write_text('[plugins."x@y"\nenabled = true\n')
    assert codex_state(str(tmp_path)) == (set(), {})


# --- skills ---------------------------------------------------------------- #
#
# Where a skill lands depends on the agent, and getting this wrong is what made
# the first version of the domain never converge: `npx skills add -a codex`
# reports success and writes ONLY ~/.agents/skills/<n> — codex reads the
# canonical directory itself. Measured in a guest, and matching the CLI's own
# rule (src/installer.ts getAgentBaseDir -> isUniversalAgent).

def test_the_canonical_directory_is_what_universal_agents_read(tmp_path):
    _write_skill(tmp_path / ".agents/skills", "impeccable")
    canonical, per_agent, _sources = skills_state(str(tmp_path))
    assert canonical == {"impeccable"}
    assert per_agent == {}
    assert carries_skill("codex", "impeccable", canonical, per_agent)
    assert carries_skill("opencode", "impeccable", canonical, per_agent)
    assert not carries_skill("claude-code", "impeccable", canonical, per_agent)


def test_claude_code_keeps_a_directory_of_its_own(tmp_path):
    canonical_dir = _write_skill(tmp_path / ".agents/skills", "impeccable")
    (tmp_path / ".claude/skills").mkdir(parents=True)
    (tmp_path / ".claude/skills/impeccable").symlink_to(canonical_dir)
    canonical, per_agent, _sources = skills_state(str(tmp_path))
    assert per_agent == {"claude-code": {"impeccable"}}
    assert carries_skill("claude-code", "impeccable", canonical, per_agent)


def test_a_copied_directory_counts_like_a_symlink(tmp_path):
    # The CLI's copy method makes an independent directory per agent.
    _write_skill(tmp_path / ".claude/skills", "caveman")
    canonical, per_agent, _sources = skills_state(str(tmp_path))
    assert canonical == set()
    assert carries_skill("claude-code", "caveman", canonical, per_agent)


def test_an_unknown_agent_is_treated_as_universal(tmp_path):
    _write_skill(tmp_path / ".agents/skills", "impeccable")
    canonical, per_agent, _sources = skills_state(str(tmp_path))
    assert carries_skill("clyde", "impeccable", canonical, per_agent)


def test_skills_state_ignores_codex_system_skills(tmp_path):
    _write_skill(tmp_path / ".codex/skills/.system", "skill-installer")
    assert skills_state(str(tmp_path)) == (set(), {}, {})


def test_skills_state_ignores_a_directory_without_a_skill_file(tmp_path):
    (tmp_path / ".agents/skills/notaskill").mkdir(parents=True)
    assert skills_state(str(tmp_path)) == (set(), {}, {})


def test_the_source_comes_from_the_lock(tmp_path):
    _write_skill(tmp_path / ".agents/skills", "impeccable")
    (tmp_path / ".agents/.skill-lock.json").write_text(json.dumps({
        "version": 3,
        "skills": {"impeccable": {"source": "pbakaus/impeccable",
                                  "sourceType": "github"},
                   "gone": {"source": "someone/removed"}}}))
    _canonical, _per_agent, sources = skills_state(str(tmp_path))
    # `gone` is only a lock record of an install that is no longer there.
    assert sources == {"impeccable": "pbakaus/impeccable"}


def test_a_skill_with_no_lock_entry_has_no_source(tmp_path):
    _write_skill(tmp_path / ".claude/skills", "graphify")
    canonical, per_agent, sources = skills_state(str(tmp_path))
    assert per_agent == {"claude-code": {"graphify"}}
    assert sources == {}


def test_skills_state_on_an_empty_home_is_empty(tmp_path):
    assert skills_state(str(tmp_path)) == (set(), {}, {})


def test_installed_agents_detects_them_by_their_home_directory(tmp_path):
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".config/opencode").mkdir(parents=True)
    assert installed_agents(str(tmp_path)) == {"codex", "opencode"}


def test_installed_agents_on_a_bare_home_is_empty(tmp_path):
    assert installed_agents(str(tmp_path)) == set()


def test_the_agent_layout_matches_the_skills_cli(tmp_path):
    # Pinned against vercel-labs/skills: agents whose `skillsDir` is
    # '.agents/skills' are universal (codex, cursor, opencode); claude-code is
    # the one with a directory of its own.
    assert AGENT_SKILL_DIRS == {"claude-code": ".claude/skills"}
    assert UNIVERSAL_AGENTS == {"codex", "cursor", "opencode"}


# --- the 3.10 TOML fallback ------------------------------------------------ #
# tomllib arrived in 3.11, so on this interpreter the hand parser is never
# reached through codex_state. It is still the parser dasik uses on a 3.10
# target, so it is tested for itself.

def test_the_fallback_parser_reads_enabled_plugins_only():
    from dasik.lib.actions.ai_skills_state import _parse_codex_toml_lines
    plugins, _markets = _parse_codex_toml_lines(
        '[plugins."superpowers@openai-curated"]\nenabled = true\n'
        '[plugins."old@mkt"]\nenabled = false\n')
    assert plugins == {"superpowers@openai-curated"}


def test_the_fallback_parser_reads_marketplaces():
    from dasik.lib.actions.ai_skills_state import _parse_codex_toml_lines
    _plugins, markets = _parse_codex_toml_lines(
        '[plugin_marketplaces.caveman]\nsource = "JuliusBrussee/caveman"\n')
    assert markets == {"caveman": "JuliusBrussee/caveman"}


def test_the_fallback_parser_gives_up_on_a_malformed_section():
    from dasik.lib.actions.ai_skills_state import _parse_codex_toml_lines
    assert _parse_codex_toml_lines('[plugins."x@y"\nenabled = true\n') == (set(), {})


def test_the_fallback_parser_ignores_keys_outside_a_section():
    from dasik.lib.actions.ai_skills_state import _parse_codex_toml_lines
    assert _parse_codex_toml_lines('model = "gpt-5.6-sol"\n') == (set(), {})

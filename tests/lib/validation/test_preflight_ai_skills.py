"""Preflight for `ai_skills`: the installer has to exist on the target.

Warnings, never errors. The binaries may arrive by a route dasik does not
manage (an already-installed machine, a package added by hand), and refusing a
config for that would be wrong — but an apply that silently fails every install
because `npx` is missing is worse than a line saying so up front.
"""
from dasik.lib.validation.preflight import preflight


def _codes(config, level=None):
    issues = preflight(config, efi_boot=True, environment=False)
    return [i.code for i in issues if level is None or i.level == level]


def _cfg(entries, packages):
    return {"hostname": "x", "ai_skills": {"entries": entries},
            "packages": packages}


_SKILL = {"name": "impeccable", "method": "skills",
          "source": "pbakaus/impeccable", "agents": ["codex"]}
_CLAUDE = {"name": "caveman", "method": "claude-plugin",
           "marketplace": {"name": "caveman", "source": "JuliusBrussee/caveman"}}
_CODEX = {"name": "superpowers", "method": "codex-plugin",
          "marketplace": {"name": "openai-curated"}}


def test_a_skills_entry_without_npx_warns():
    assert "ai_skills_without_installer" in _codes(_cfg([_SKILL], ["base"]),
                                                   "warning")


def test_a_skills_entry_with_nodejs_is_quiet():
    assert "ai_skills_without_installer" not in _codes(
        _cfg([_SKILL], ["base", "nodejs", "npm", "codex"]))


def test_a_claude_plugin_without_the_claude_package_warns():
    assert "ai_skills_without_installer" in _codes(_cfg([_CLAUDE], ["base"]),
                                                   "warning")


def test_a_claude_plugin_with_claude_code_declared_is_quiet():
    assert "ai_skills_without_installer" not in _codes(
        _cfg([_CLAUDE], ["base", "claude-code"]))


def test_a_codex_plugin_without_codex_warns():
    assert "ai_skills_without_installer" in _codes(_cfg([_CODEX], ["base"]),
                                                   "warning")


def test_an_unknown_agent_warns():
    entry = {**_SKILL, "agents": ["clyde"]}
    assert "ai_skills_unknown_agent" in _codes(
        _cfg([entry], ["base", "nodejs", "npm"]), "warning")


def test_a_known_agent_does_not_warn():
    assert "ai_skills_unknown_agent" not in _codes(
        _cfg([_SKILL], ["base", "nodejs", "npm", "codex"]))


def test_no_block_no_findings():
    assert "ai_skills_without_installer" not in _codes({"hostname": "x"})


def test_nothing_here_is_ever_an_error():
    issues = preflight(_cfg([_SKILL, _CLAUDE, _CODEX], ["base"]),
                       efi_boot=True, environment=False)
    assert [i.level for i in issues if i.code.startswith("ai_skills")] == [
        "warning"] * len([i for i in issues if i.code.startswith("ai_skills")])
    assert [i for i in issues if i.code.startswith("ai_skills")]

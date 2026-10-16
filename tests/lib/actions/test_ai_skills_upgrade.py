"""`ai_skills` upgrade steps — which native updater runs for each declared item.

Nothing runs a real CLI: the assertions are about the exact commands a step
carries and about which declared items get a step at all.
"""
import json
from unittest.mock import MagicMock, patch

from dasik.lib.actions.ai_skills_action import _AGY_INSTALL
from dasik.lib.actions.ai_skills_state import (claude_plugin_version,
                                               skill_folder_hash)
from tests.lib.actions.test_ai_skills_antigravity_plugin import _registry
from tests.lib.actions.test_ai_skills_plan import (
    _act, _install_claude_plugin, _install_skill, _passwd)

MKT = {"name": "caveman", "source": "JuliusBrussee/caveman"}
CLAUDE = {"name": "superpowers", "method": "claude-plugin", "marketplace": MKT}
CODEX = {"name": "superpowers", "method": "codex-plugin", "marketplace": MKT}
SKILL = {"name": "impeccable", "method": "skills",
         "source": "pbakaus/impeccable", "agents": ["codex"]}
TOOL = {"name": "graphify", "method": "tool", "command": "graphify",
        "agents": ["codex", "claude-code"]}
AGY = {"name": "superpowers", "method": "antigravity-plugin",
       "source": "obra/superpowers"}


def _cfg(*entries, users=("andres",)):
    return {"users": [{"username": u} for u in users],
            "ai_skills": {"entries": list(entries)}}


def _steps(tmp_path, *entries, **kw):
    action = _act(tmp_path, _cfg(*entries, **kw))
    # codex probes (marketplace/plugin list) answer nothing, as on a bare box.
    with patch("dasik.lib.actions.ai_skills_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=1, stdout="", stderr="")
        return action.upgrade_steps()


def _by_item(steps):
    return {s.item: s for s in steps}


def _codex_plugin(root, plugin="superpowers@caveman"):
    codex = root / "home/andres/.codex"
    codex.mkdir(parents=True, exist_ok=True)
    (codex / "config.toml").write_text(
        f'[plugins."{plugin}"]\nenabled = true\n'
        '[marketplaces.caveman]\nsource = "https://github.com/JuliusBrussee/caveman.git"\n')


# --- readers ---------------------------------------------------------------- #

def test_claude_plugin_version_reads_record(tmp_path):
    _install_claude_plugin(tmp_path, version="63e797cd753b")
    assert claude_plugin_version(str(tmp_path / "home/andres"),
                                 "superpowers@caveman") == "63e797cd753b"


def test_claude_plugin_version_falls_back_to_commit_sha(tmp_path):
    path = tmp_path / ".claude/plugins"
    path.mkdir(parents=True)
    (path / "installed_plugins.json").write_text(json.dumps(
        {"plugins": {"a@b": [{"gitCommitSha": "abc123"}]}}))
    assert claude_plugin_version(str(tmp_path), "a@b") == "abc123"


def test_claude_plugin_version_none_when_absent_or_unreadable(tmp_path):
    assert claude_plugin_version(str(tmp_path), "a@b") is None
    path = tmp_path / ".claude/plugins"
    path.mkdir(parents=True)
    (path / "installed_plugins.json").write_text("{nope")
    assert claude_plugin_version(str(tmp_path), "a@b") is None
    (path / "installed_plugins.json").write_text(
        json.dumps({"plugins": {"a@b": []}}))
    assert claude_plugin_version(str(tmp_path), "a@b") is None


def test_skill_folder_hash_reads_lock(tmp_path):
    lock = tmp_path / ".agents/.skill-lock.json"
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps(
        {"skills": {"adapt": {"source": "x/y", "skillFolderHash": "3066c4"}}}))
    assert skill_folder_hash(str(tmp_path), "adapt") == "3066c4"
    assert skill_folder_hash(str(tmp_path), "other") is None


def test_skill_folder_hash_none_without_lock(tmp_path):
    assert skill_folder_hash(str(tmp_path), "adapt") is None


# --- one test per table row ------------------------------------------------- #

def test_claude_marketplace_refresh(tmp_path):
    _passwd(tmp_path)
    _install_claude_plugin(tmp_path)
    step = _by_item(_steps(tmp_path, CLAUDE))[
        "andres:claude-code:marketplace:caveman"]
    assert step.commands == (('claude plugin marketplace update "$1"',
                              ("caveman",)),)
    assert step.domain == "ai_skills" and step.user == "andres"
    assert step.skip is None and step.version() is None


def test_claude_plugin_update_and_version(tmp_path):
    _passwd(tmp_path)
    _install_claude_plugin(tmp_path, version="63e797cd753b")
    step = _by_item(_steps(tmp_path, CLAUDE))[
        "andres:claude-code:plugin:superpowers@caveman"]
    assert step.commands == (('claude plugin update "$1" -s user -y',
                              ("superpowers@caveman",)),)
    assert step.version() == "63e797cd753b"


def test_codex_marketplace_upgrade(tmp_path):
    _passwd(tmp_path)
    _codex_plugin(tmp_path)
    step = _by_item(_steps(tmp_path, CODEX))[
        "andres:codex:marketplace:caveman"]
    assert step.commands == (('codex plugin marketplace upgrade "$1"',
                              ("caveman",)),)
    assert step.skip is None and step.version() is None


def test_codex_plugin_readd(tmp_path):
    _passwd(tmp_path)
    _codex_plugin(tmp_path)
    step = _by_item(_steps(tmp_path, CODEX))[
        "andres:codex:plugin:superpowers@caveman"]
    assert step.commands == (('codex plugin add "$1"', ("superpowers@caveman",)),)
    assert step.version() is None


def test_antigravity_plugin_reinstalls_from_clone_url(tmp_path):
    _passwd(tmp_path)
    (tmp_path / "home/andres").mkdir(parents=True)
    _registry(tmp_path / "home/andres", ["superpowers"])
    step = _by_item(_steps(tmp_path, AGY))[
        "andres:antigravity:plugin:superpowers"]
    assert step.commands == ((_AGY_INSTALL,
                              ("https://github.com/obra/superpowers",)),)
    assert step.version() is None


def test_skills_method_one_npx_update_with_hash(tmp_path):
    _passwd(tmp_path)
    _install_skill(tmp_path)
    lock = tmp_path / "home/andres/.agents/.skill-lock.json"
    data = json.loads(lock.read_text())
    data["skills"]["impeccable"]["skillFolderHash"] = "3066c474"
    lock.write_text(json.dumps(data))
    step = _by_item(_steps(tmp_path, SKILL))["andres:skills:impeccable"]
    assert step.commands == (('npx -y skills update "$1" -g -y',
                              ("impeccable",)),)
    assert step.version() == "3066c474"


def test_tool_method_reruns_installer_per_agent(tmp_path):
    _passwd(tmp_path)
    _install_skill(tmp_path, name="graphify", agents=("codex", "claude-code"))
    by = _by_item(_steps(tmp_path, TOOL))
    run = ('PATH="$HOME/.local/bin:$PATH"; "$1" install --platform "$2"')
    assert by["andres:codex:skill:graphify"].commands == (
        (run, ("graphify", "codex")),)
    assert by["andres:claude-code:skill:graphify"].commands == (
        (run, ("graphify", "claude")),)
    assert by["andres:codex:skill:graphify"].version() is None


# --- shape ------------------------------------------------------------------ #

def test_one_update_per_skill_not_per_agent(tmp_path):
    _passwd(tmp_path)
    _install_skill(tmp_path, agents=("codex",))
    entry = dict(SKILL, agents=["codex", "antigravity", "antigravity-cli"])
    steps = [s for s in _steps(tmp_path, entry) if "skills update" in
             "".join(c[0] for c in s.commands)]
    assert len(steps) == 1


def test_marketplace_steps_precede_plugin_steps(tmp_path):
    _passwd(tmp_path)
    _install_claude_plugin(tmp_path)
    items = [s.item for s in _steps(tmp_path, CLAUDE)]
    assert items == ["andres:claude-code:marketplace:caveman",
                     "andres:claude-code:plugin:superpowers@caveman"]


def test_undeclared_installed_plugin_untouched(tmp_path):
    _passwd(tmp_path)
    _install_claude_plugin(tmp_path)
    _install_claude_plugin(tmp_path, plugin="stranger@elsewhere",
                           marketplace="elsewhere", source="x/y")
    items = [s.item for s in _steps(tmp_path, CLAUDE)]
    assert not any("stranger" in i or "elsewhere" in i for i in items)


def test_missing_plugin_skipped(tmp_path):
    _passwd(tmp_path)
    steps = _steps(tmp_path, CLAUDE, SKILL)
    assert steps
    for step in steps:
        assert step.skip == "not installed — run dasik apply"
        assert step.commands == ()


def test_unknown_user_does_not_raise(tmp_path):
    _passwd(tmp_path)
    steps = _steps(tmp_path, CLAUDE, SKILL, users=("ghost",))
    assert steps and all(s.skip for s in steps)
    assert all(s.version() is None for s in steps)


def test_no_target_gives_no_steps():
    from dasik.lib.actions.ai_skills_action import AiSkillsAction
    assert AiSkillsAction(_cfg(CLAUDE)).upgrade_steps() == []

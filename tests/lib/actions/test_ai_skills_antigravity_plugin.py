"""`antigravity-plugin`: an Antigravity plugin, installed with `agy plugin`.

Measured on antigravity-cli 1.2.5 under a scratch HOME (docs/FACTS.md
FACT-AGY-5): `agy plugin install` takes only a LOCAL DIRECTORY carrying
`.agents/plugins/marketplace.json` (no `owner/repo`, no `plugin@marketplace`),
copies it to `~/.gemini/config/plugins/<name>/` and records
`{"imports": [{"name": ...}]}` in `~/.gemini/config/import_manifest.json`; the
clone is not needed afterwards. Re-installing and uninstalling (even a missing
name) both exit 0. The name is the plugin's own (`obra/superpowers` →
`superpowers`, `21st-dev/magic-mcp` → `21st`).
"""
import json
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.ai_skills_action import AiSkillsAction
from dasik.lib.actions.ai_skills_state import antigravity_plugins
from dasik.lib.models.ai_skills_model import AiSkillsModel
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target
from dasik.lib.validation.preflight import preflight

SUPERPOWERS = {"name": "superpowers", "method": "antigravity-plugin",
               "source": "obra/superpowers"}
TWENTY_FIRST = {"name": "21st", "method": "antigravity-plugin",
                "source": "https://github.com/21st-dev/magic-mcp"}


def _root(tmp_path, installed=(), payload=True):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/passwd").write_text(
        "root:x:0:0::/root:/bin/bash\nandres:x:1000:1000::/home/andres:/bin/zsh\n")
    home = tmp_path / "home/andres"
    home.mkdir(parents=True, exist_ok=True)
    if installed:
        _registry(home, installed, payload)
    return tmp_path


def _registry(home, names, payload=True):
    config = home / ".gemini/config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "import_manifest.json").write_text(json.dumps({"imports": [
        {"name": n, "source": "gemini-cli", "components": ["skills"]}
        for n in names]}))
    if payload:
        for name in names:
            (config / "plugins" / name).mkdir(parents=True, exist_ok=True)


def _action(tmp_path, entries, **root):
    cfg = {"users": [{"username": "andres"}], "ai_skills": {"entries": entries}}
    return AiSkillsAction(cfg, ActionContext(
        target=Target(root=str(_root(tmp_path, **root)))))


# -- model ------------------------------------------------------------------ #

def test_the_method_is_accepted_with_a_source():
    entry = AiSkillsModel(entries=[SUPERPOWERS]).entries[0]
    assert entry.agent_ids == ["antigravity"]
    assert entry.plugin == "superpowers"


def test_the_method_requires_a_source():
    with pytest.raises(ValidationError, match="source"):
        AiSkillsModel(entries=[{"name": "superpowers",
                                "method": "antigravity-plugin"}])


def test_the_method_takes_no_marketplace_and_no_agents():
    with pytest.raises(ValidationError, match="marketplace"):
        AiSkillsModel(entries=[dict(SUPERPOWERS, marketplace={"name": "m"})])
    with pytest.raises(ValidationError, match="agents"):
        AiSkillsModel(entries=[dict(SUPERPOWERS, agents=["antigravity"])])


def test_the_source_must_be_a_git_repository_not_a_local_path():
    """agy needs a directory, which dasik makes by cloning: a path on the
    machine running dasik means nothing inside the target."""
    with pytest.raises(ValidationError, match="source"):
        AiSkillsModel(entries=[dict(SUPERPOWERS, source="/home/andres/src/sp")])
    with pytest.raises(ValidationError, match="source"):
        AiSkillsModel(entries=[dict(SUPERPOWERS, source="-oProxyCommand=x")])


# -- reader ----------------------------------------------------------------- #

def test_reads_the_plugins_agy_recorded(tmp_path):
    _registry(tmp_path, ["superpowers", "21st"])
    assert antigravity_plugins(str(tmp_path)) == {"superpowers", "21st"}


def test_a_recorded_plugin_whose_files_are_gone_is_not_installed(tmp_path):
    """The restored-$HOME ghost (see ai-skills ghost registry): the manifest is
    small and backed up, the copied payload usually is not."""
    _registry(tmp_path, ["superpowers"], payload=False)
    assert antigravity_plugins(str(tmp_path)) == set()


def test_a_missing_or_broken_manifest_is_nothing(tmp_path):
    assert antigravity_plugins(str(tmp_path)) == set()
    (tmp_path / ".gemini/config").mkdir(parents=True)
    (tmp_path / ".gemini/config/import_manifest.json").write_text("{nope")
    assert antigravity_plugins(str(tmp_path)) == set()


# -- plan ------------------------------------------------------------------- #

def _plan(action, managed=()):
    return [(c.op.name, c.item) for c in action.plan(managed=list(managed))]


def test_missing_plugins_plan_creates(tmp_path):
    assert _plan(_action(tmp_path, [SUPERPOWERS, TWENTY_FIRST])) == [
        ("CREATE", "andres:antigravity:plugin:21st"),
        ("CREATE", "andres:antigravity:plugin:superpowers")]


def test_installed_plugins_plan_nothing(tmp_path):
    assert _plan(_action(tmp_path, [SUPERPOWERS, TWENTY_FIRST],
                         installed=("superpowers", "21st"))) == []


def test_owned_but_undeclared_plugin_is_deleted(tmp_path):
    action = _action(tmp_path, [], installed=("superpowers",))
    assert _plan(action, managed=["andres:antigravity:plugin:superpowers"]) == [
        ("DELETE", "andres:antigravity:plugin:superpowers")]


def test_an_unowned_plugin_is_left_alone(tmp_path):
    assert _plan(_action(tmp_path, [], installed=("superpowers",))) == []


# -- apply ------------------------------------------------------------------ #

def _calls(action, changes, returncode=0):
    with patch("dasik.lib.actions.ai_skills_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=returncode, stdout="",
                                         stderr="boom" if returncode else "")
        action.apply(changes)
    return [call.args for call in execute.call_args_list]


def test_create_clones_to_a_temporary_directory_and_installs_it(tmp_path):
    (binary, argv), = _calls(_action(tmp_path, [SUPERPOWERS]), [
        Change("ai_skills", Op.CREATE, "andres:antigravity:plugin:superpowers")])
    assert binary == "su"
    script = argv[3]
    assert 'git clone --depth 1 --quiet -- "$1"' in script
    assert 'agy plugin install' in script
    assert 'rm -rf -- "$dir"' in script
    # the shorthand is expanded where git understands it; the value is argv
    assert argv[4:] == ["--", "sh", "https://github.com/obra/superpowers"]


def test_a_full_url_is_passed_through(tmp_path):
    (_, argv), = _calls(_action(tmp_path, [TWENTY_FIRST]), [
        Change("ai_skills", Op.CREATE, "andres:antigravity:plugin:21st")])
    assert argv[4:] == ["--", "sh", "https://github.com/21st-dev/magic-mcp"]


def test_delete_uninstalls_by_name_even_when_undeclared(tmp_path):
    (_, argv), = _calls(_action(tmp_path, []), [
        Change("ai_skills", Op.DELETE, "andres:antigravity:plugin:superpowers")])
    assert argv[3] == 'agy plugin uninstall "$1"'
    assert argv[4:] == ["--", "sh", "superpowers"]


def test_a_failed_install_is_not_owned(tmp_path):
    action = _action(tmp_path, [SUPERPOWERS])
    _calls(action, [Change("ai_skills", Op.CREATE,
                           "andres:antigravity:plugin:superpowers")], returncode=1)
    assert action.managed_keys() == {"ai_skills": []}


# -- sync ------------------------------------------------------------------- #

def test_sync_captures_a_declared_plugin_with_its_declared_source(tmp_path):
    """agy records no source, so only the config can say where a plugin came
    from — the machine confirms that it is there."""
    action = _action(tmp_path, [SUPERPOWERS], installed=("superpowers",))
    block = action.import_state()["ai_skills"]
    assert block == {"users": ["andres"], "entries": [SUPERPOWERS]}
    AiSkillsModel(**block)


def test_sync_never_invents_a_source_for_an_undeclared_plugin(tmp_path, capsys):
    action = _action(tmp_path, [], installed=("superpowers",))
    assert action.import_state()["ai_skills"] == {}
    assert "superpowers" in capsys.readouterr().out


def test_a_declared_plugin_that_is_not_installed_is_not_captured(tmp_path):
    action = _action(tmp_path, [SUPERPOWERS])
    assert action.import_state()["ai_skills"] == {}


def test_the_captured_block_replans_to_nothing(tmp_path):
    root = _root(tmp_path, installed=("superpowers", "21st"))
    cfg = {"users": [{"username": "andres"}],
           "ai_skills": {"entries": [SUPERPOWERS, TWENTY_FIRST]}}
    captured = AiSkillsAction(cfg, ActionContext(target=Target(root=str(root)))
                              ).import_state()["ai_skills"]
    replay = AiSkillsAction({"users": [{"username": "andres"}],
                             "ai_skills": captured},
                            ActionContext(target=Target(root=str(root))))
    assert replay.plan(managed=[]) == []


# -- preflight -------------------------------------------------------------- #

def _codes(config):
    return [i.code for i in preflight(config, efi_boot=True, environment=False)
            if i.code.startswith("ai_skills")]


def test_preflight_warns_without_agy_or_git():
    config = {"hostname": "x", "packages": ["base"],
              "ai_skills": {"entries": [SUPERPOWERS]}}
    assert "ai_skills_without_installer" in _codes(config)
    config["packages"] = ["base", "antigravity-cli", "git"]
    assert "ai_skills_without_installer" not in _codes(config)


# -- graphify (`tool`) for the antigravity agents --------------------------- #
#
# graphify's own `install --platform antigravity` copies the skill to
# ~/.agents/skills/graphify/SKILL.md (graphify/__main__.py _PLATFORM_CONFIG);
# it has no `antigravity-cli` platform, and both agents read that same copy.

GRAPHIFY = {"name": "graphify", "method": "tool", "command": "graphify",
            "agents": ["antigravity", "antigravity-cli"]}


def test_graphify_is_installed_with_its_antigravity_platform_for_both_agents(tmp_path):
    action = _action(tmp_path, [GRAPHIFY])
    calls = _calls(action, action.plan(managed=[]))
    assert [argv[4:] for _b, argv in calls] == [
        ["--", "sh", "graphify", "antigravity"],
        ["--", "sh", "graphify", "antigravity"]]


def test_a_dropped_antigravity_graphify_removes_the_shared_copy_and_converges(tmp_path):
    """No lock records a tool's skill, and neither antigravity agent has a
    directory of its own: without naming the canonical copy the removal did
    nothing and plan asked for the same DELETE forever."""
    root = _root(tmp_path)
    skill = root / "home/andres/.agents/skills/graphify"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: graphify\n---\n")
    managed = ["andres:antigravity:skill:graphify",
               "andres:antigravity-cli:skill:graphify"]
    action = AiSkillsAction({"users": [{"username": "andres"}],
                             "ai_skills": {"entries": []}},
                            ActionContext(target=Target(root=str(root))))
    calls = _calls(action, action.plan(managed=managed))
    assert [argv[3:] for _b, argv in calls][0] == [
        'rm -rf -- "$1"', "--", "sh", "/home/andres/.agents/skills/graphify"]


# -- review round 1 --------------------------------------------------------- #

@pytest.mark.parametrize("bad", ["../../..", "..", "a/b", "-rf", ".hidden", "x y"])
def test_b1_a_plugin_name_that_is_not_a_plain_name_is_refused(bad):
    """Measured: `agy plugin uninstall ../../..` deletes the directory tree it
    resolves to. Both fields reach agy (plugin) or the item (name)."""
    with pytest.raises(ValidationError, match="plugin"):
        AiSkillsModel(entries=[dict(SUPERPOWERS, plugin=bad)])
    with pytest.raises(ValidationError, match="name"):
        AiSkillsModel(entries=[dict(SUPERPOWERS, name=bad)])


def test_b1_a_manifest_item_with_an_unsafe_name_never_reaches_agy(tmp_path):
    """A DELETE is rebuilt from the manifest, which is a file on disk — it gets
    the same check as a declaration before anything runs."""
    action = _action(tmp_path, [])
    calls = _calls(action, [
        Change("ai_skills", Op.DELETE, "andres:antigravity:plugin:../../..")])
    assert calls == []
    assert action.failed_items == ["andres:antigravity:plugin:../../.."]


def test_s1_an_install_that_registers_another_name_is_a_failure(tmp_path):
    """agy names the plugin after its marketplace.json, not after the config.
    A success exit that installed something else must not be owned, or the
    next plan asks for it again forever."""
    action = _action(tmp_path, [dict(SUPERPOWERS, plugin="superpowers-dev")])
    _calls(action, [Change("ai_skills", Op.CREATE,
                           "andres:antigravity:plugin:superpowers-dev")])
    assert action.failed_items == ["andres:antigravity:plugin:superpowers-dev"]
    assert action.managed_keys() == {"ai_skills": []}


def test_s1_an_install_that_registers_the_declared_name_is_owned(tmp_path):
    root = _root(tmp_path)
    action = AiSkillsAction({"users": [{"username": "andres"}],
                             "ai_skills": {"entries": [SUPERPOWERS]}},
                            ActionContext(target=Target(root=str(root))))
    home = root / "home/andres"

    def installed(*_a, **_k):
        _registry(home, ["superpowers"])
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("dasik.lib.actions.ai_skills_action.Command.execute",
               side_effect=installed):
        action.apply([Change("ai_skills", Op.CREATE,
                             "andres:antigravity:plugin:superpowers")])
    assert action.failed_items == []


def _canonical_skill(root, name="graphify"):
    skill = root / "home/andres/.agents/skills" / name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\n")


def test_s2_dropping_one_antigravity_agent_keeps_the_copy_the_others_use(tmp_path):
    root = _root(tmp_path)
    _canonical_skill(root)
    action = AiSkillsAction({"users": [{"username": "andres"}], "ai_skills": {
        "entries": [dict(GRAPHIFY, agents=["codex", "antigravity-cli"])]}},
        ActionContext(target=Target(root=str(root))))
    calls = _calls(action, [
        Change("ai_skills", Op.DELETE, "andres:antigravity:skill:graphify")])
    assert calls == []
    assert action.failed_items == []


def test_s3_an_unowned_agent_link_keeps_the_skill_in_place(tmp_path):
    """A claude-code link dasik does not own (made by hand, or by another
    config) must survive: the agent-less remove would delete it. Fall back to
    removing only what this apply is deleting."""
    root = _root(tmp_path)
    home = root / "home/andres"
    _canonical_skill(root, "impeccable")
    (home / ".claude/skills").mkdir(parents=True)
    (home / ".claude/skills/impeccable").symlink_to(home / ".agents/skills/impeccable")
    (home / ".agents/.skill-lock.json").write_text(json.dumps(
        {"version": 3, "skills": {"impeccable": {"source": "pbakaus/impeccable"}}}))
    action = AiSkillsAction({"users": [{"username": "andres"}],
                             "ai_skills": {"entries": []}},
                            ActionContext(target=Target(root=str(root))))
    calls = _calls(action, [
        Change("ai_skills", Op.DELETE, "andres:codex:skill:impeccable")])
    assert [argv[3] for _b, argv in calls] == [
        'npx -y skills remove --skill "$1" --agent "$2" --global --yes']

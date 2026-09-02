"""`ai_skills` sync — the machine reported back as its own block.

The invariant that matters is the last test in this file: `sync` -> `check` ->
`plan` must end in silence. A capture the tool then refuses, or one that
re-plans the same changes forever, is a broken capture even when every field
looks plausible.
"""
from dasik.lib.actions.ai_skills_action import AiSkillsAction
from dasik.lib.models.json_model import JsonModel
from tests.lib.actions.test_ai_skills_plan import (
    _act, _home, _install_claude_plugin, _install_skill, _passwd)


def _block(action):
    return action.import_state()["ai_skills"]


def _codex_plugin(root, user="andres", plugin="superpowers@openai-curated",
                  extra=""):
    codex = _home(root, user) / ".codex"
    codex.mkdir(parents=True, exist_ok=True)
    (codex / "config.toml").write_text(
        f'[plugins."{plugin}"]\nenabled = true\n{extra}')


def _cfg(users=("andres",)):
    return {"users": [{"username": u} for u in users]}


# --- what sync captures ---------------------------------------------------- #

def test_sync_captures_a_claude_plugin_with_its_marketplace(tmp_path):
    _passwd(tmp_path)
    _install_claude_plugin(tmp_path)
    block = _block(_act(tmp_path, _cfg()))
    assert block["entries"] == [{
        "name": "superpowers", "method": "claude-plugin",
        "marketplace": {"name": "caveman", "source": "JuliusBrussee/caveman"}}]
    assert block["users"] == ["andres"]


def test_sync_captures_a_codex_plugin_from_a_builtin_marketplace(tmp_path):
    _passwd(tmp_path)
    _codex_plugin(tmp_path)
    assert _block(_act(tmp_path, _cfg()))["entries"] == [{
        "name": "superpowers", "method": "codex-plugin",
        "marketplace": {"name": "openai-curated"}}]


def test_sync_captures_a_skill_with_every_agent_that_carries_it(tmp_path):
    _passwd(tmp_path)
    _install_skill(tmp_path, "caveman", agents=("claude-code", "codex"),
                   source="JuliusBrussee/caveman")
    assert _block(_act(tmp_path, _cfg()))["entries"] == [{
        "name": "caveman", "method": "skills",
        "source": "JuliusBrussee/caveman",
        "agents": ["claude-code", "codex"]}]


def test_sync_omits_a_skill_with_no_known_source(tmp_path):
    # A hand-made skill folder: capturing it would produce a config no other
    # machine could reproduce, since nothing says where it came from.
    _passwd(tmp_path)
    _install_skill(tmp_path, "graphify", agents=("claude-code",))
    home = _home(tmp_path)
    (home / ".agents/.skill-lock.json").unlink()
    assert _block(_act(tmp_path, _cfg())) == {}


def test_sync_on_a_machine_with_no_agents_invents_nothing(tmp_path):
    _passwd(tmp_path)
    assert _act(tmp_path, _cfg()).import_state() == {"ai_skills": {}}


def test_sync_without_a_target_invents_nothing(tmp_path):
    assert AiSkillsAction(_cfg(), None).import_state() == {"ai_skills": {}}


def test_sync_discovers_the_humans_when_the_config_declares_none(tmp_path):
    # A bootstrap sync starts from {}: the users come from the machine.
    _passwd(tmp_path, users=("andres",))
    _install_claude_plugin(tmp_path)
    assert _block(_act(tmp_path, {}))["users"] == ["andres"]


def test_sync_ignores_a_system_account(tmp_path):
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/passwd").write_text(
        "root:x:0:0::/root:/bin/bash\n"
        "http:x:33:33::/srv/http:/usr/bin/nologin\n")
    (tmp_path / "srv/http/.claude/plugins").mkdir(parents=True)
    (tmp_path / "srv/http/.claude/plugins/installed_plugins.json").write_text(
        '{"version": 2, "plugins": {"x@y": [{"scope": "user"}]}}')
    assert _act(tmp_path, {}).import_state() == {"ai_skills": {}}


def test_sync_scopes_an_entry_to_the_user_that_has_it(tmp_path):
    # Two users, one skill each: without per-entry users the capture would say
    # "both users get both", and the next plan would install two things.
    _passwd(tmp_path, users=("andres", "otro"))
    _install_skill(tmp_path, "impeccable", agents=("codex",), user="andres")
    _install_skill(tmp_path, "caveman", agents=("codex",), user="otro",
                   source="JuliusBrussee/caveman")
    block = _block(_act(tmp_path, _cfg(("andres", "otro"))))
    assert block["users"] == ["andres", "otro"]
    by_name = {e["name"]: e for e in block["entries"]}
    assert by_name["impeccable"]["users"] == ["andres"]
    assert by_name["caveman"]["users"] == ["otro"]


def test_an_artefact_everyone_has_carries_no_per_entry_users(tmp_path):
    _passwd(tmp_path, users=("andres", "otro"))
    for user in ("andres", "otro"):
        _install_skill(tmp_path, "impeccable", agents=("codex",), user=user)
    entry = _block(_act(tmp_path, _cfg(("andres", "otro"))))["entries"][0]
    assert "users" not in entry


def test_sync_keeps_a_declared_abort_policy(tmp_path):
    # Policy is not something the machine can report; it is carried over.
    _passwd(tmp_path)
    _install_claude_plugin(tmp_path)
    cfg = {**_cfg(), "ai_skills": {"failure_policy": "abort", "entries": []}}
    assert _block(_act(tmp_path, cfg))["failure_policy"] == "abort"


def test_sync_does_not_write_the_default_policy(tmp_path):
    _passwd(tmp_path)
    _install_claude_plugin(tmp_path)
    assert "failure_policy" not in _block(_act(tmp_path, _cfg()))


# --- the round trip -------------------------------------------------------- #

def test_the_captured_block_validates_and_replans_to_nothing(tmp_path):
    _passwd(tmp_path, users=("andres", "otro"))
    _install_claude_plugin(tmp_path)
    _codex_plugin(tmp_path)
    _install_skill(tmp_path, "impeccable", agents=("claude-code", "codex"))
    _install_skill(tmp_path, "caveman", agents=("codex",), user="otro",
                   source="JuliusBrussee/caveman")

    captured = _act(tmp_path, _cfg(("andres", "otro"))).import_state()

    # `dasik check` accepts it...
    JsonModel(**{"hostname": "x", **captured})
    # ...and applying it would change nothing.
    assert _act(tmp_path, {**_cfg(("andres", "otro")), **captured}).plan(
        managed=[]) == []


def test_a_canonical_skill_no_agent_reads_is_reported_not_invented(tmp_path):
    """The copy is there, but this machine has no agent that reads it.

    Naming one would describe a machine that does not exist; the entry is
    skipped and said out loud instead.
    """
    _passwd(tmp_path)
    canonical = _home(tmp_path) / ".agents/skills/impeccable"
    canonical.mkdir(parents=True)
    (canonical / "SKILL.md").write_text("---\nname: impeccable\n---\n")
    (_home(tmp_path) / ".agents/.skill-lock.json").write_text(
        '{"version": 3, "skills": {"impeccable": {"source": "pbakaus/impeccable"}}}')
    assert _act(tmp_path, _cfg()).import_state() == {"ai_skills": {}}


def test_the_declared_agent_is_enough_even_before_it_is_installed(tmp_path):
    """A config that declares codex captures codex, even on a machine where the
    codex home does not exist yet — the declaration is the reason it is there."""
    _passwd(tmp_path)
    canonical = _home(tmp_path) / ".agents/skills/impeccable"
    canonical.mkdir(parents=True)
    (canonical / "SKILL.md").write_text("---\nname: impeccable\n---\n")
    (_home(tmp_path) / ".agents/.skill-lock.json").write_text(
        '{"version": 3, "skills": {"impeccable": {"source": "pbakaus/impeccable"}}}')
    cfg = {**_cfg(), "ai_skills": {"entries": [
        {"name": "impeccable", "method": "skills",
         "source": "pbakaus/impeccable", "agents": ["codex"]}]}}
    assert _block(_act(tmp_path, cfg))["entries"] == [{
        "name": "impeccable", "method": "skills",
        "source": "pbakaus/impeccable", "agents": ["codex"]}]

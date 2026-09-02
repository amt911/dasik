"""`ai_skills` — what the domain plans, and what it deliberately leaves alone.

The rule the whole domain hangs on: missing ⇒ a change is planned, present ⇒
silence, owned-but-no-longer-declared ⇒ removed, and anything a person
installed themselves ⇒ untouched.
"""
import json

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.ai_skills_action import AiSkillsAction
from dasik.lib.target.target import Target

ENTRIES = [
    {"name": "superpowers", "method": "claude-plugin",
     "marketplace": {"name": "caveman", "source": "JuliusBrussee/caveman"}},
    {"name": "impeccable", "method": "skills",
     "source": "pbakaus/impeccable", "agents": ["codex"]},
]
CFG = {"users": [{"username": "andres"}, {"username": "root"}],
       "ai_skills": {"entries": ENTRIES}}


def _act(root, cfg=None):
    return AiSkillsAction(cfg if cfg is not None else CFG,
                          ActionContext(target=Target(root=str(root))))


def _passwd(root, users=("andres",)):
    (root / "etc").mkdir(parents=True, exist_ok=True)
    lines = ["root:x:0:0::/root:/bin/bash"]
    for index, user in enumerate(users):
        uid = 1000 + index
        lines.append(f"{user}:x:{uid}:{uid}::/home/{user}:/bin/bash")
    (root / "etc/passwd").write_text("\n".join(lines) + "\n")


def _home(root, user="andres"):
    home = root / "home" / user
    home.mkdir(parents=True, exist_ok=True)
    return home


def _install_claude_plugin(root, plugin="superpowers@caveman", user="andres",
                           marketplace="caveman", source="JuliusBrussee/caveman"):
    plugins_dir = _home(root, user) / ".claude/plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    (plugins_dir / "installed_plugins.json").write_text(json.dumps({
        "version": 2, "plugins": {plugin: [{"scope": "user"}]}}))
    (plugins_dir / "known_marketplaces.json").write_text(json.dumps({
        marketplace: {"source": {"source": "github", "repo": source}}}))


def _install_skill(root, name="impeccable", agents=("codex",), user="andres",
                   source="pbakaus/impeccable"):
    home = _home(root, user)
    canonical = home / ".agents/skills" / name
    canonical.mkdir(parents=True, exist_ok=True)
    (canonical / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    dirs = {"claude-code": ".claude/skills", "codex": ".codex/skills"}
    for agent in agents:
        agent_dir = home / dirs[agent]
        agent_dir.mkdir(parents=True, exist_ok=True)
        link = agent_dir / name
        if not link.exists():
            link.symlink_to(canonical)
    lock = home / ".agents/.skill-lock.json"
    existing = json.loads(lock.read_text()) if lock.exists() else {"version": 3,
                                                                  "skills": {}}
    existing["skills"][name] = {"source": source, "sourceType": "github"}
    lock.write_text(json.dumps(existing))


def _install_all(root, marketplace_source="JuliusBrussee/caveman"):
    _install_claude_plugin(root, source=marketplace_source)
    _install_skill(root)


def _items(action, managed=()):
    return [(c.op.name, c.item) for c in action.plan(managed=list(managed))]


# --- the plan -------------------------------------------------------------- #

def test_a_missing_skill_is_planned_for_every_declared_human(tmp_path):
    _passwd(tmp_path)
    assert _items(_act(tmp_path)) == [
        ("CREATE", "andres:claude-code:marketplace:caveman"),
        ("CREATE", "andres:claude-code:plugin:superpowers@caveman"),
        ("CREATE", "andres:codex:skill:impeccable"),
    ]


def test_root_is_not_a_human(tmp_path):
    # `users` declares root too; nobody wants a plugin in /root.
    _passwd(tmp_path)
    assert not any(c.item.startswith("root:") for c in _act(tmp_path).plan(managed=[]))


def test_the_marketplace_is_planned_before_the_plugin_that_needs_it(tmp_path):
    # `claude plugin install x@mkt` fails against a marketplace nobody added.
    _passwd(tmp_path)
    items = [c.item for c in _act(tmp_path).plan(managed=[])]
    assert (items.index("andres:claude-code:marketplace:caveman")
            < items.index("andres:claude-code:plugin:superpowers@caveman"))


def test_everything_present_plans_nothing(tmp_path):
    _passwd(tmp_path)
    _install_all(tmp_path)
    assert _act(tmp_path).plan(managed=[]) == []


def test_a_builtin_marketplace_plans_no_registration(tmp_path):
    _passwd(tmp_path)
    cfg = {"users": [{"username": "andres"}], "ai_skills": {"entries": [
        {"name": "superpowers", "method": "codex-plugin",
         "marketplace": {"name": "openai-curated"}}]}}
    assert _items(_act(tmp_path, cfg)) == [
        ("CREATE", "andres:codex:plugin:superpowers@openai-curated")]


def test_an_installed_codex_plugin_plans_nothing(tmp_path):
    _passwd(tmp_path)
    codex = _home(tmp_path) / ".codex"
    codex.mkdir(parents=True)
    (codex / "config.toml").write_text(
        '[plugins."superpowers@openai-curated"]\nenabled = true\n')
    cfg = {"users": [{"username": "andres"}], "ai_skills": {"entries": [
        {"name": "superpowers", "method": "codex-plugin",
         "marketplace": {"name": "openai-curated"}}]}}
    assert _act(tmp_path, cfg).plan(managed=[]) == []


def test_undeclared_but_owned_is_removed(tmp_path):
    _passwd(tmp_path)
    _install_all(tmp_path)
    cfg = {"users": [{"username": "andres"}], "ai_skills": {"entries": []}}
    assert _items(_act(tmp_path, cfg),
                  managed=["andres:codex:skill:impeccable"]) == [
        ("DELETE", "andres:codex:skill:impeccable")]


def test_the_block_dropped_entirely_still_removes_what_it_owned(tmp_path):
    # The reconciler hands an action its EMPTY config when a previous
    # generation owned the domain. An empty config is not "no domain".
    _passwd(tmp_path)
    _install_all(tmp_path)
    cfg = {"users": [{"username": "andres"}]}
    assert _items(_act(tmp_path, cfg),
                  managed=["andres:codex:skill:impeccable"]) == [
        ("DELETE", "andres:codex:skill:impeccable")]


def test_a_skill_nobody_declared_or_owns_is_left_alone(tmp_path):
    _passwd(tmp_path)
    _install_all(tmp_path)
    _install_skill(tmp_path, "graphify", agents=("claude-code",))
    assert _act(tmp_path).plan(managed=[]) == []


def test_a_marketplace_registered_from_another_source_is_a_modify(tmp_path):
    _passwd(tmp_path)
    _install_all(tmp_path, marketplace_source="someone/else")
    assert [(c.op.name, c.item, c.reason) for c in _act(tmp_path).plan(managed=[])] == [
        ("MODIFY", "andres:claude-code:marketplace:caveman", "source drift")]


def test_the_block_users_list_wins_over_the_declared_humans(tmp_path):
    _passwd(tmp_path, users=("andres", "otro"))
    cfg = {"users": [{"username": "andres"}, {"username": "otro"}],
           "ai_skills": {"users": ["otro"], "entries": ENTRIES}}
    assert all(c.item.startswith("otro:") for c in _act(tmp_path, cfg).plan(managed=[]))


def test_a_user_the_machine_does_not_have_yet_is_planned_anyway(tmp_path):
    # The whole plan is computed before UsersAction has created anybody, so the
    # home falls back to /home/<user> exactly like home_files does.
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/passwd").write_text("root:x:0:0::/root:/bin/bash\n")
    assert _items(_act(tmp_path)) == [
        ("CREATE", "andres:claude-code:marketplace:caveman"),
        ("CREATE", "andres:claude-code:plugin:superpowers@caveman"),
        ("CREATE", "andres:codex:skill:impeccable"),
    ]


def test_the_home_the_machine_declares_wins_over_the_fallback(tmp_path):
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/passwd").write_text(
        "andres:x:1000:1000::/srv/andres:/bin/bash\n")
    elsewhere = tmp_path / "srv/andres/.codex/skills/impeccable"
    elsewhere.mkdir(parents=True)
    (elsewhere / "SKILL.md").write_text("---\nname: impeccable\n---\n")
    assert "andres:codex:skill:impeccable" not in [
        c.item for c in _act(tmp_path).plan(managed=[])]


def test_no_target_plans_nothing(tmp_path):
    assert AiSkillsAction(CFG, None).plan(managed=[]) == []


def test_an_empty_block_plans_nothing(tmp_path):
    _passwd(tmp_path)
    assert _act(tmp_path, {"users": [{"username": "andres"}]}).plan(managed=[]) == []


# --- ownership ------------------------------------------------------------- #

def test_managed_keys_are_exactly_the_declared_items(tmp_path):
    _passwd(tmp_path)
    assert _act(tmp_path).managed_keys() == {"ai_skills": [
        "andres:claude-code:marketplace:caveman",
        "andres:claude-code:plugin:superpowers@caveman",
        "andres:codex:skill:impeccable",
    ]}


def test_actual_reports_what_the_machine_carries(tmp_path):
    _passwd(tmp_path)
    _install_all(tmp_path)
    assert _act(tmp_path).actual() == {
        "andres:claude-code:marketplace:caveman",
        "andres:claude-code:plugin:superpowers@caveman",
        "andres:codex:skill:impeccable",
    }


def test_the_action_is_optional_and_named(tmp_path):
    action = _act(tmp_path)
    assert action.is_optional is True
    assert action.name == "AI Skills"
    assert AiSkillsAction.empty_config() == {}


def test_an_entry_may_be_scoped_to_one_user(tmp_path):
    _passwd(tmp_path, users=("andres", "otro"))
    cfg = {"users": [{"username": "andres"}, {"username": "otro"}],
           "ai_skills": {"entries": [
               {"name": "impeccable", "method": "skills",
                "source": "pbakaus/impeccable", "agents": ["codex"],
                "users": ["otro"]}]}}
    assert _items(_act(tmp_path, cfg)) == [
        ("CREATE", "otro:codex:skill:impeccable")]


def test_an_entry_user_the_block_does_not_list_is_ignored(tmp_path):
    # The block's `users` is the boundary of the domain: an entry cannot widen
    # it, or dropping a user from the block would stop removing their artefacts.
    _passwd(tmp_path, users=("andres", "otro"))
    cfg = {"users": [{"username": "andres"}, {"username": "otro"}],
           "ai_skills": {"users": ["andres"], "entries": [
               {"name": "impeccable", "method": "skills",
                "source": "pbakaus/impeccable", "agents": ["codex"],
                "users": ["otro"]}]}}
    assert _act(tmp_path, cfg).plan(managed=[]) == []

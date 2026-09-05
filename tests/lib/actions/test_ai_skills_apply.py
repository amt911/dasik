"""`ai_skills` apply — the official CLIs, run as the user, in the right order.

Nothing here runs a real installer: the assertions are about WHICH command dasik
would run, with which arguments, and what it does when one of them fails.
"""
import pytest
from unittest.mock import MagicMock, patch

from dasik.lib.exceptions.exceptions import CommandExecutionError
from tests.lib.actions.test_ai_skills_plan import (  # the fixtures' twin helpers
    CFG, ENTRIES, _act, _install_all, _install_claude_plugin, _install_skill,
    _passwd)


def _apply(tmp_path, cfg=None, rc=0):
    action = _act(tmp_path, cfg)
    with patch("dasik.lib.actions.ai_skills_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=rc, stdout="", stderr="boom")
        action.apply(action.plan(managed=[]))
    return action, execute


_PROBE = "codex plugin marketplace list"


def _argvs(execute):
    """The ``su`` argv of every INSTALLER call, in order.

    `plan` also runs a read-only `codex plugin marketplace list` through the
    same `su`, to warn when a curated marketplace is not in scope (a signed-out
    codex cannot resolve `plugin@marketplace`). It installs nothing, so it is
    dropped here; it has its own test file.
    """
    return [call.args[1] for call in execute.call_args_list
            if call.args[1][3] != _PROBE]


def _scripts(execute):
    """The installer scripts, in order."""
    return [argv[3] for argv in _argvs(execute)]


# --- what gets run --------------------------------------------------------- #

def test_every_command_runs_as_the_user_through_su(tmp_path):
    _passwd(tmp_path)
    _action, execute = _apply(tmp_path)
    for call in execute.call_args_list:
        assert call.args[0] == "su"
        argv = call.args[1]
        assert argv[0] == "-" and argv[1] == "andres" and argv[2] == "-c"
        # `--` terminates su's own option parsing before the shell's argv, and
        # the values arrive as $1.. so they can never be executed as code.
        assert argv[4] == "--" and argv[5] == "sh"


def test_the_three_installers_are_the_official_ones(tmp_path):
    _passwd(tmp_path)
    _action, execute = _apply(tmp_path)
    assert _scripts(execute) == [
        'claude plugin marketplace add "$1"',
        'claude plugin install "$1" -y --scope user',
        'npx -y skills add "$1" --skill "$2" -g -a "$3" -y',
    ]


def test_the_arguments_are_positional_parameters(tmp_path):
    _passwd(tmp_path)
    _action, execute = _apply(tmp_path)
    argvs = _argvs(execute)
    assert argvs[0][6:] == ["JuliusBrussee/caveman"]
    assert argvs[1][6:] == ["superpowers@caveman"]
    assert argvs[2][6:] == ["pbakaus/impeccable", "impeccable", "codex"]


def test_the_marketplace_is_added_before_the_plugin(tmp_path):
    _passwd(tmp_path)
    _action, execute = _apply(tmp_path)
    scripts = _scripts(execute)
    assert (next(i for i, s in enumerate(scripts) if "marketplace add" in s)
            < next(i for i, s in enumerate(scripts) if "plugin install" in s))


def test_a_codex_plugin_uses_codex_plugin_add(tmp_path):
    _passwd(tmp_path)
    cfg = {"users": [{"username": "andres"}], "ai_skills": {"entries": [
        {"name": "superpowers", "method": "codex-plugin",
         "marketplace": {"name": "openai-curated"}}]}}
    _action, execute = _apply(tmp_path, cfg)
    assert _scripts(execute) == ['codex plugin add "$1"']
    assert _argvs(execute)[0][6:] == ["superpowers@openai-curated"]


def test_a_codex_marketplace_is_registered_before_its_plugin(tmp_path):
    _passwd(tmp_path)
    cfg = {"users": [{"username": "andres"}], "ai_skills": {"entries": [
        {"name": "caveman", "method": "codex-plugin",
         "marketplace": {"name": "caveman",
                         "source": "https://github.com/JuliusBrussee/caveman"}}]}}
    _action, execute = _apply(tmp_path, cfg)
    assert _scripts(execute) == ['codex plugin marketplace add "$1"',
                                 'codex plugin add "$1"']


def test_nothing_runs_when_the_plan_is_empty(tmp_path):
    _passwd(tmp_path)
    _install_all(tmp_path)
    _action, execute = _apply(tmp_path)
    execute.assert_not_called()


def test_each_user_gets_their_own_commands(tmp_path):
    _passwd(tmp_path, users=("andres", "otro"))
    cfg = {"users": [{"username": "andres"}, {"username": "otro"}],
           "ai_skills": {"entries": ENTRIES}}
    _action, execute = _apply(tmp_path, cfg)
    assert sorted({argv[1] for argv in _argvs(execute)}) == ["andres", "otro"]


# --- removals -------------------------------------------------------------- #

def test_removing_a_skill_uses_the_official_remove(tmp_path):
    _passwd(tmp_path)
    _install_skill(tmp_path)
    action = _act(tmp_path, {"users": [{"username": "andres"}]})
    with patch("dasik.lib.actions.ai_skills_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0, stdout="", stderr="")
        action.apply(action.plan(managed=["andres:codex:skill:impeccable"]))
    assert _scripts(execute) == [
        'npx -y skills remove --skill "$1" --agent "$2" --global --yes']
    assert _argvs(execute)[0][6:] == ["impeccable", "codex"]


def test_removing_a_plugin_and_its_marketplace_runs_plugin_first(tmp_path):
    _passwd(tmp_path)
    _install_claude_plugin(tmp_path)
    action = _act(tmp_path, {"users": [{"username": "andres"}]})
    managed = ["andres:claude-code:marketplace:caveman",
               "andres:claude-code:plugin:superpowers@caveman"]
    with patch("dasik.lib.actions.ai_skills_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0, stdout="", stderr="")
        action.apply(action.plan(managed=managed))
    assert _scripts(execute) == ['claude plugin uninstall "$1"',
                                 'claude plugin marketplace remove "$1"']
    assert _argvs(execute)[0][6:] == ["superpowers@caveman"]
    assert _argvs(execute)[1][6:] == ["caveman"]


def test_a_marketplace_source_drift_is_re_registered(tmp_path):
    _passwd(tmp_path)
    _install_all(tmp_path, marketplace_source="someone/else")
    _action, execute = _apply(tmp_path)
    assert _scripts(execute) == ['claude plugin marketplace remove "$1"',
                                 'claude plugin marketplace add "$1"']
    assert _argvs(execute)[0][6:] == ["caveman"]
    assert _argvs(execute)[1][6:] == ["JuliusBrussee/caveman"]


# --- failure policy -------------------------------------------------------- #

def test_warn_and_continue_keeps_going_and_disowns_the_item(tmp_path):
    _passwd(tmp_path)
    action, execute = _apply(tmp_path, rc=1)
    assert len(execute.call_args_list) == 3          # did not stop at the first
    assert action.managed_keys() == {"ai_skills": []}
    assert action.failed_items == [
        "andres:claude-code:marketplace:caveman",
        "andres:claude-code:plugin:superpowers@caveman",
        "andres:codex:skill:impeccable",
    ]


def test_a_failed_marketplace_disowns_the_plugin_that_needed_it(tmp_path):
    # The plugin install is still attempted (it may work from another
    # marketplace already present), but nothing is claimed as owned.
    _passwd(tmp_path)
    action, _execute = _apply(tmp_path, rc=1)
    assert "andres:claude-code:plugin:superpowers@caveman" in action.failed_items


def test_abort_raises_on_the_first_failure(tmp_path):
    _passwd(tmp_path)
    cfg = {"users": [{"username": "andres"}],
           "ai_skills": {"failure_policy": "abort", "entries": ENTRIES}}
    with pytest.raises(CommandExecutionError):
        _apply(tmp_path, cfg, rc=1)


def test_abort_stops_at_the_first_failure(tmp_path):
    _passwd(tmp_path)
    cfg = {"users": [{"username": "andres"}],
           "ai_skills": {"failure_policy": "abort", "entries": ENTRIES}}
    action = _act(tmp_path, cfg)
    with patch("dasik.lib.actions.ai_skills_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
        with pytest.raises(CommandExecutionError):
            action.apply(action.plan(managed=[]))
    assert len(execute.call_args_list) == 1


def test_a_successful_apply_owns_everything_it_installed(tmp_path):
    _passwd(tmp_path)
    action, _execute = _apply(tmp_path)
    assert action.managed_keys()["ai_skills"] == [
        "andres:claude-code:marketplace:caveman",
        "andres:claude-code:plugin:superpowers@caveman",
        "andres:codex:skill:impeccable",
    ]


def test_apply_without_a_target_runs_nothing(tmp_path):
    from dasik.lib.actions.ai_skills_action import AiSkillsAction
    action = AiSkillsAction(CFG, None)
    with patch("dasik.lib.actions.ai_skills_action.Command.execute") as execute:
        action.apply([])
    execute.assert_not_called()


def test_removing_a_codex_plugin_passes_the_full_selector(tmp_path):
    # `codex plugin remove` takes PLUGIN@MARKETPLACE; the bare name is
    # ambiguous when two marketplaces carry the same plugin.
    _passwd(tmp_path)
    codex = tmp_path / "home/andres/.codex"
    codex.mkdir(parents=True)
    (codex / "config.toml").write_text(
        '[plugins."superpowers@openai-curated"]\nenabled = true\n')
    action = _act(tmp_path, {"users": [{"username": "andres"}]})
    with patch("dasik.lib.actions.ai_skills_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0, stdout="", stderr="")
        action.apply(action.plan(
            managed=["andres:codex:plugin:superpowers@openai-curated"]))
    assert _scripts(execute) == ['codex plugin remove "$1"']
    assert _argvs(execute)[0][6:] == ["superpowers@openai-curated"]


# --- the `tool` method ----------------------------------------------------- #

from tests.lib.actions.test_ai_skills_plan import TOOL_CFG, _install_tool_skill


def test_a_tool_skill_is_installed_by_its_own_program(tmp_path):
    _passwd(tmp_path)
    _action, execute = _apply(tmp_path, TOOL_CFG)
    assert _scripts(execute) == [
        'PATH="$HOME/.local/bin:$PATH"; "$1" install --platform "$2"'] * 2
    # The platform names are the program's, not dasik's agent ids.
    assert [argv[6:] for argv in _argvs(execute)] == [
        ["graphify", "claude"], ["graphify", "codex"]]


def test_removing_a_tool_skill_deletes_the_directory_it_owns(tmp_path):
    _passwd(tmp_path)
    _install_tool_skill(tmp_path, agents=("codex",))
    action = _act(tmp_path, {"users": [{"username": "andres"}]})
    with patch("dasik.lib.actions.ai_skills_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0, stdout="", stderr="")
        action.apply(action.plan(managed=["andres:codex:skill:graphify"]))
    # No uninstall verb exists, and the directory is one dasik's apply created.
    assert _scripts(execute) == ['rm -rf -- "$1"']
    assert _argvs(execute)[0][6:] == ["/home/andres/.codex/skills/graphify"]


def test_a_tool_removal_never_leaves_the_users_home(tmp_path):
    from dasik.lib.actions.ai_skills_action import AiSkillsAction
    action = _act(tmp_path, TOOL_CFG)
    assert action._skill_dir_for("andres", "codex", "graphify",
                                 {"andres": "/home/andres"}) == \
        "/home/andres/.codex/skills/graphify"
    assert action._skill_dir_for("andres", "clyde", "x", {}) is None


def test_a_tool_command_is_looked_for_where_uv_and_pipx_put_it(tmp_path):
    """`~/.local/bin` is NOT on the PATH of a login shell on a stock Arch box —
    /etc/profile adds only /usr/local/bin. uv and pipx both install their
    commands there, so a tool dasik itself installed through `uv_tools` would be
    'command not found' the moment ai_skills tried to run it."""
    _passwd(tmp_path)
    _action, execute = _apply(tmp_path, TOOL_CFG)
    for argv in _argvs(execute):
        assert argv[3].startswith('PATH="$HOME/.local/bin:$PATH"; ')

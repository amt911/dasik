"""`uv_tools` — plan, apply and capture the per-user Python programs.

Presence is read from uv's own tool directory rather than from a command on
PATH: `~/.local/bin` is NOT on the PATH of a login shell on a stock Arch
install (only /usr/local/bin is), so "is the command there?" would answer no on
a machine that has the tool perfectly well.
"""
import pytest
from unittest.mock import MagicMock, patch

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.uv_tools_action import UvToolsAction
from dasik.lib.exceptions.exceptions import CommandExecutionError
from dasik.lib.target.target import Target

CFG = {"users": [{"username": "andres"}, {"username": "root"}],
       "uv_tools": {"tools": ["graphifyy", "semgrep"]}}


def _act(root, cfg=None):
    return UvToolsAction(cfg if cfg is not None else CFG,
                         ActionContext(target=Target(root=str(root))))


def _passwd(root, users=("andres",)):
    (root / "etc").mkdir(parents=True, exist_ok=True)
    lines = ["root:x:0:0::/root:/bin/bash"]
    for index, user in enumerate(users):
        uid = 1000 + index
        lines.append(f"{user}:x:{uid}:{uid}::/home/{user}:/bin/bash")
    (root / "etc/passwd").write_text("\n".join(lines) + "\n")


def _install(root, tool="graphifyy", user="andres"):
    d = root / "home" / user / ".local/share/uv/tools" / tool / "bin"
    d.mkdir(parents=True, exist_ok=True)
    (d / "graphify").write_text("#!/bin/sh\n")


def _items(action, managed=()):
    return [(c.op.name, c.item) for c in action.plan(managed=list(managed))]


# --- plan ------------------------------------------------------------------ #

def test_a_missing_tool_is_planned_for_every_declared_human(tmp_path):
    _passwd(tmp_path)
    assert _items(_act(tmp_path)) == [
        ("INSTALL", "andres:graphifyy"),
        ("INSTALL", "andres:semgrep"),
    ]


def test_root_is_not_a_human(tmp_path):
    _passwd(tmp_path)
    assert not any(c.item.startswith("root:")
                   for c in _act(tmp_path).plan(managed=[]))


def test_a_tool_already_in_uvs_directory_plans_nothing(tmp_path):
    _passwd(tmp_path)
    _install(tmp_path, "graphifyy")
    _install(tmp_path, "semgrep")
    assert _act(tmp_path).plan(managed=[]) == []


def test_a_version_pin_is_matched_by_the_distribution_directory(tmp_path):
    # uv names the directory after the distribution, not the specifier.
    _passwd(tmp_path)
    _install(tmp_path, "graphifyy")
    cfg = {"users": [{"username": "andres"}],
           "uv_tools": {"tools": ["graphifyy==0.9.53"]}}
    assert _act(tmp_path, cfg).plan(managed=[]) == []


def test_extras_are_matched_by_the_distribution_directory(tmp_path):
    _passwd(tmp_path)
    _install(tmp_path, "semgrep")
    cfg = {"users": [{"username": "andres"}],
           "uv_tools": {"tools": ["semgrep[all]"]}}
    assert _act(tmp_path, cfg).plan(managed=[]) == []


def test_an_owned_tool_no_longer_declared_is_removed(tmp_path):
    _passwd(tmp_path)
    _install(tmp_path, "graphifyy")
    cfg = {"users": [{"username": "andres"}], "uv_tools": {"tools": []}}
    assert _items(_act(tmp_path, cfg), managed=["andres:graphifyy"]) == [
        ("REMOVE", "andres:graphifyy")]


def test_a_tool_the_user_installed_themselves_is_left_alone(tmp_path):
    _passwd(tmp_path)
    _install(tmp_path, "graphifyy")
    _install(tmp_path, "git-filter-repo")
    cfg = {"users": [{"username": "andres"}],
           "uv_tools": {"tools": ["graphifyy"]}}
    assert _act(tmp_path, cfg).plan(managed=[]) == []


def test_the_block_users_list_wins(tmp_path):
    _passwd(tmp_path, users=("andres", "otro"))
    cfg = {"users": [{"username": "andres"}, {"username": "otro"}],
           "uv_tools": {"users": ["otro"], "tools": ["graphifyy"]}}
    assert _items(_act(tmp_path, cfg)) == [("INSTALL", "otro:graphifyy")]


def test_a_user_the_machine_does_not_have_yet_is_planned_anyway(tmp_path):
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/passwd").write_text("root:x:0:0::/root:/bin/bash\n")
    assert _items(_act(tmp_path)) == [("INSTALL", "andres:graphifyy"),
                                      ("INSTALL", "andres:semgrep")]


def test_no_target_plans_nothing(tmp_path):
    assert UvToolsAction(CFG, None).plan(managed=[]) == []


def test_an_absent_block_plans_nothing(tmp_path):
    _passwd(tmp_path)
    assert _act(tmp_path, {"users": [{"username": "andres"}]}).plan(
        managed=[]) == []


def test_the_action_is_optional_and_named(tmp_path):
    action = _act(tmp_path)
    assert action.is_optional is True
    assert action.name == "uv tools"
    assert UvToolsAction.empty_config() == {}


# --- apply ----------------------------------------------------------------- #

def _apply(tmp_path, cfg=None, rc=0, managed=()):
    action = _act(tmp_path, cfg)
    with patch("dasik.lib.actions.uv_tools_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=rc, stdout="", stderr="boom")
        action.apply(action.plan(managed=list(managed)))
    return action, execute


def _argvs(execute):
    return [call.args[1] for call in execute.call_args_list]


def test_install_runs_uv_as_the_user(tmp_path):
    _passwd(tmp_path)
    _action, execute = _apply(tmp_path)
    for call in execute.call_args_list:
        assert call.args[0] == "su"
    argv = _argvs(execute)[0]
    assert argv[:5] == ["-", "andres", "-c", 'uv tool install "$1"', "--"]
    assert argv[6:] == ["graphifyy"]


def test_the_declaration_reaches_uv_verbatim_including_a_pin(tmp_path):
    _passwd(tmp_path)
    cfg = {"users": [{"username": "andres"}],
           "uv_tools": {"tools": ["graphifyy==0.9.53"]}}
    _action, execute = _apply(tmp_path, cfg)
    assert _argvs(execute)[0][6:] == ["graphifyy==0.9.53"]


def test_removal_uses_uv_tool_uninstall_with_the_distribution_name(tmp_path):
    _passwd(tmp_path)
    _install(tmp_path, "graphifyy")
    cfg = {"users": [{"username": "andres"}], "uv_tools": {"tools": []}}
    _action, execute = _apply(tmp_path, cfg, managed=["andres:graphifyy"])
    argv = _argvs(execute)[0]
    assert argv[3] == 'uv tool uninstall "$1"'
    assert argv[6:] == ["graphifyy"]


def test_nothing_runs_when_the_plan_is_empty(tmp_path):
    _passwd(tmp_path)
    _install(tmp_path, "graphifyy")
    _install(tmp_path, "semgrep")
    _action, execute = _apply(tmp_path)
    execute.assert_not_called()


def test_warn_and_continue_keeps_going_and_disowns_the_tool(tmp_path):
    _passwd(tmp_path)
    action, execute = _apply(tmp_path, rc=1)
    assert len(execute.call_args_list) == 2
    assert action.managed_keys() == {"uv_tools": []}


def test_abort_raises_on_the_first_failure(tmp_path):
    _passwd(tmp_path)
    cfg = {"users": [{"username": "andres"}],
           "uv_tools": {"failure_policy": "abort", "tools": ["graphifyy"]}}
    with pytest.raises(CommandExecutionError):
        _apply(tmp_path, cfg, rc=1)


def test_a_successful_apply_owns_what_it_installed(tmp_path):
    _passwd(tmp_path)
    action, _execute = _apply(tmp_path)
    assert action.managed_keys() == {
        "uv_tools": ["andres:graphifyy", "andres:semgrep"]}


# --- sync ------------------------------------------------------------------ #

def test_sync_captures_the_tools_uv_actually_has(tmp_path):
    _passwd(tmp_path)
    _install(tmp_path, "graphifyy")
    _install(tmp_path, "git-filter-repo")
    assert _act(tmp_path, {"users": [{"username": "andres"}]}).import_state() == {
        "uv_tools": {"users": ["andres"], "tools": ["git-filter-repo",
                                                    "graphifyy"]}}


def test_sync_on_a_machine_with_no_uv_tools_invents_nothing(tmp_path):
    _passwd(tmp_path)
    assert _act(tmp_path).import_state() == {"uv_tools": {}}


def test_sync_discovers_the_humans_when_the_config_declares_none(tmp_path):
    _passwd(tmp_path)
    _install(tmp_path, "graphifyy")
    assert _act(tmp_path, {}).import_state()["uv_tools"]["users"] == ["andres"]


def test_sync_ignores_a_system_account(tmp_path):
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/passwd").write_text(
        "root:x:0:0::/root:/bin/bash\n"
        "http:x:33:33::/srv/http:/usr/bin/nologin\n")
    d = tmp_path / "srv/http/.local/share/uv/tools/graphifyy"
    d.mkdir(parents=True)
    assert _act(tmp_path, {}).import_state() == {"uv_tools": {}}


def test_sync_keeps_a_declared_abort_policy(tmp_path):
    _passwd(tmp_path)
    _install(tmp_path, "graphifyy")
    cfg = {"users": [{"username": "andres"}],
           "uv_tools": {"failure_policy": "abort", "tools": []}}
    assert _act(tmp_path, cfg).import_state()["uv_tools"]["failure_policy"] == \
        "abort"


def test_the_captured_block_replans_to_nothing(tmp_path):
    from dasik.lib.models.json_model import JsonModel
    _passwd(tmp_path)
    _install(tmp_path, "graphifyy")
    _install(tmp_path, "semgrep")
    captured = _act(tmp_path).import_state()
    JsonModel(**{"hostname": "x", **captured})
    assert _act(tmp_path, {"users": [{"username": "andres"}],
                           **captured}).plan(managed=[]) == []

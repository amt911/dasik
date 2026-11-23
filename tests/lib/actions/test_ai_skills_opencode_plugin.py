"""`opencode-plugin`: an opencode plugin, installed with `opencode plugin add`.

Measured on opencode v2.3.0: the CLI writes only opencode.json (`plugins`), and
`opencode plugin remove <exact spec>` edits only opencode.json and exits 0 even
when it removed nothing. opencode loads opencode.jsonc too (legacy `plugin`
key), which the CLI never touches: dasik edits that file itself and re-reads
every removal.
"""
import json
import os
from unittest.mock import MagicMock, patch

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.ai_skills_action import AiSkillsAction
from dasik.lib.actions.opencode_config import (JSON_REL, JSONC_REL,
                                                   OpencodeConfigError,
                                                   read_plugins)
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target

PJ = "pj@1.0.0"
GIT = "foo@git+https://github.com/o/foo.git"


def _entry(source, name=None):
    return {"name": name or source.split("@")[0], "method": "opencode-plugin",
            "source": source}


def _root(tmp_path, files=None):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/passwd").write_text(
        "root:x:0:0::/root:/bin/bash\n"
        f"andres:x:{os.getuid()}:{os.getgid()}::/home/andres:/bin/zsh\n")
    home = tmp_path / "home/andres"
    home.mkdir(parents=True, exist_ok=True)
    for rel, text in (files or {}).items():
        path = home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


def _action(tmp_path, entries, files=None):
    cfg = {"users": [{"username": "andres"}], "ai_skills": {"entries": entries}}
    return AiSkillsAction(cfg, ActionContext(
        target=Target(root=str(_root(tmp_path, files)))))


def _plan(action, managed=()):
    return [(c.op.name, c.item) for c in action.plan(managed=list(managed))]


def _item(source):
    return f"andres:opencode:plugin:{source}"


def _json(**kw):
    return json.dumps(kw)


def _home(tmp_path):
    return str(tmp_path / "home/andres")


# -- plan ------------------------------------------------------------------- #

def test_declared_plugin_missing_plans_a_create(tmp_path):
    assert _plan(_action(tmp_path, [_entry(PJ)])) == [("CREATE", _item(PJ))]


def test_plugin_in_users_jsonc_legacy_key_plans_nothing(tmp_path):
    action = _action(tmp_path, [_entry(PJ)],
                     {JSONC_REL: _json(plugin=[PJ])})
    assert _plan(action) == []


def test_plugin_in_the_new_plugins_key_plans_nothing(tmp_path):
    action = _action(tmp_path, [_entry(PJ)], {JSON_REL: _json(plugins=[PJ])})
    assert _plan(action) == []


def test_changed_source_plans_delete_old_and_create_new(tmp_path):
    action = _action(tmp_path, [_entry("pj@2.0.0", "pj")],
                     {JSON_REL: _json(plugins=[PJ])})
    assert _plan(action, managed=[_item(PJ)]) == [
        ("CREATE", _item("pj@2.0.0")), ("DELETE", _item(PJ))]


# -- apply ------------------------------------------------------------------ #

def _calls(action, changes, effect=None, returncode=0):
    with patch("dasik.lib.actions.ai_skills_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=returncode, stdout="",
                                         stderr="boom" if returncode else "")
        if effect:
            execute.side_effect = lambda *a, **k: (effect(*a, **k),
                                                   execute.return_value)[1]
        action.apply(changes)
    return [call.args for call in execute.call_args_list]


def test_create_runs_opencode_plugin_add_as_user(tmp_path):
    (binary, argv), = _calls(_action(tmp_path, [_entry(GIT)]), [
        Change("ai_skills", Op.CREATE, _item(GIT))])
    assert binary == "su"
    assert argv[:2] == ["-", "andres"]
    assert argv[3] == 'opencode plugin add "$1"'
    assert argv[4:] == ["--", "sh", GIT]


def test_delete_in_json_runs_plugin_remove_with_exact_spec(tmp_path):
    action = _action(tmp_path, [], {JSON_REL: _json(plugins=[PJ])})

    def cli_removes(*_a, **_k):
        (tmp_path / "home/andres" / JSON_REL).write_text(_json(plugins=[]))

    (binary, argv), = _calls(action, [Change("ai_skills", Op.DELETE, _item(PJ))],
                             effect=cli_removes)
    assert argv[3] == 'opencode plugin remove "$1"'
    assert argv[4:] == ["--", "sh", PJ]
    assert action.failed_items == []


def test_delete_that_removed_nothing_fails_the_item(tmp_path):
    action = _action(tmp_path, [], {JSON_REL: _json(plugins=[PJ])})
    _calls(action, [Change("ai_skills", Op.DELETE, _item(PJ))])  # rc 0, no-op
    assert action.failed_items == [_item(PJ)]


def test_delete_in_jsonc_edits_the_file_and_runs_no_cli(tmp_path):
    action = _action(tmp_path, [], {
        JSONC_REL: '{\n // mine\n "plugin": ["pj@1.0.0", "keep@1"],\n}\n'})
    calls = _calls(action, [Change("ai_skills", Op.DELETE, _item(PJ))])
    assert calls == []
    assert set(read_plugins(_home(tmp_path))) == {"keep@1"}
    assert action.failed_items == []


def test_delete_of_a_spec_in_both_files_clears_both(tmp_path):
    action = _action(tmp_path, [], {JSON_REL: _json(plugins=[PJ]),
                                    JSONC_REL: _json(plugin=[PJ])})

    def cli_removes(*_a, **_k):
        (tmp_path / "home/andres" / JSON_REL).write_text(_json(plugins=[]))

    calls = _calls(action, [Change("ai_skills", Op.DELETE, _item(PJ))],
                   effect=cli_removes)
    assert len(calls) == 1
    assert read_plugins(_home(tmp_path)) == {}
    assert action.failed_items == []


def test_delete_of_undeclared_git_spec_rebuilds_from_item(tmp_path):
    action = _action(tmp_path, [], {JSON_REL: _json(plugins=[GIT])})
    (_, argv), = _calls(action, [Change("ai_skills", Op.DELETE, _item(GIT))])
    assert argv[3] == 'opencode plugin remove "$1"'
    assert argv[4:] == ["--", "sh", GIT]


def test_a_config_dasik_cannot_edit_fails_the_item_instead_of_raising(tmp_path):
    action = _action(tmp_path, [], {JSONC_REL: _json(plugin=[PJ])})
    with patch("dasik.lib.actions.ai_skills_action.opencode_edit",
               side_effect=OpencodeConfigError("nope")):
        _calls(action, [Change("ai_skills", Op.DELETE, _item(PJ))])
    assert action.failed_items == [_item(PJ)]


def test_the_item_survives_a_source_with_colons():
    spec = AiSkillsAction._spec_from_item(_item(GIT))
    assert spec["agent"] == "opencode" and spec["source"] == GIT
    assert spec["name"] == "foo" and spec["method"] == "opencode-plugin"


# -- upgrade ---------------------------------------------------------------- #

def test_upgrade_skips_an_installed_opencode_plugin_instead_of_refreshing(tmp_path):
    action = _action(tmp_path, [_entry(PJ)], {JSON_REL: _json(plugins=[PJ])})
    (step,) = action.upgrade_steps()
    assert step.skip == "opencode has no update step in dasik yet"
    assert not step.commands


def test_upgrade_of_a_missing_opencode_plugin_says_not_installed(tmp_path):
    (step,) = _action(tmp_path, [_entry(PJ)]).upgrade_steps()
    assert step.skip.startswith("not installed")


# -- a config dasik cannot trust ---------------------------------------------- #

def _create(action):
    return _calls(action, [Change("ai_skills", Op.CREATE, _item(PJ))])


def test_create_is_refused_when_opencode_json_is_a_symlink(tmp_path, capsys):
    action = _action(tmp_path, [_entry(PJ)])
    target = tmp_path / "real.json"
    target.write_text(_json(plugins=[PJ]))
    cfg = tmp_path / "home/andres" / JSON_REL
    cfg.parent.mkdir(parents=True)
    cfg.symlink_to(target)
    assert _create(action) == []
    assert action.failed_items == [_item(PJ)]
    assert str(cfg) in capsys.readouterr().out


def test_create_is_refused_when_opencode_json_does_not_parse(tmp_path, capsys):
    action = _action(tmp_path, [_entry(PJ)], {JSON_REL: "{nope"})
    assert _create(action) == []
    assert action.failed_items == [_item(PJ)]
    assert JSON_REL in capsys.readouterr().out


def test_delete_fails_when_the_file_became_a_link_after_the_cli_ran(tmp_path):
    action = _action(tmp_path, [], {JSON_REL: _json(plugins=[PJ])})
    cfg = tmp_path / "home/andres" / JSON_REL

    def cli_swaps_for_a_link(*_a, **_k):
        real = tmp_path / "real.json"
        real.write_text(_json(plugins=[PJ]))
        cfg.unlink()
        cfg.symlink_to(real)

    _calls(action, [Change("ai_skills", Op.DELETE, _item(PJ))],
           effect=cli_swaps_for_a_link)
    assert action.failed_items == [_item(PJ)]

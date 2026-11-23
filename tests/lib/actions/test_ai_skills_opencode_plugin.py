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
from dasik.lib.models.ai_skills_model import AiSkillsModel
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target

PJ = "pj@1.0.0"
GIT = "foo@git+https://github.com/o/foo.git"


def _entry(source, name=None):
    return {"name": name or source.split("@")[0], "method": "opencode-plugin",
            "source": source}


def _root(tmp_path, files=None, binary=True):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/passwd").write_text(
        "root:x:0:0::/root:/bin/bash\n"
        f"andres:x:{os.getuid()}:{os.getgid()}::/home/andres:/bin/zsh\n")
    if binary:
        (tmp_path / "usr/bin").mkdir(parents=True, exist_ok=True)
        (tmp_path / "usr/bin/opencode").write_text("")
    home = tmp_path / "home/andres"
    home.mkdir(parents=True, exist_ok=True)
    for rel, text in (files or {}).items():
        path = home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


def _action(tmp_path, entries, files=None, binary=True):
    cfg = {"users": [{"username": "andres"}], "ai_skills": {"entries": entries}}
    return AiSkillsAction(cfg, ActionContext(
        target=Target(root=str(_root(tmp_path, files, binary)))))


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


# -- sync ------------------------------------------------------------------- #

def _synced(tmp_path, files):
    return _action(tmp_path, [], files).import_state()["ai_skills"]


def test_sync_captures_each_plugin_as_an_entry(tmp_path):
    block = _synced(tmp_path, {JSON_REL: _json(plugins=[PJ]),
                               JSONC_REL: _json(plugin=[GIT])})
    assert block == {"users": ["andres"],
                     "entries": [_entry(GIT, "foo"), _entry(PJ, "pj")]}


def test_sync_skips_scoped_and_file_specs_with_a_warning(tmp_path, capsys):
    block = _synced(tmp_path, {JSON_REL: _json(
        plugins=["@scope/pkg", "file:../p", PJ])})
    assert block["entries"] == [_entry(PJ)]
    out = capsys.readouterr().out
    assert "not captured" in out
    assert "andres: opencode plugin @scope/pkg" in out
    assert "andres: opencode plugin file:../p" in out


def test_sync_captures_nothing_from_a_config_dasik_refuses(tmp_path, capsys):
    root = _root(tmp_path, {JSON_REL: _json(plugins=[PJ])})
    real = root / "home/andres" / JSON_REL
    real.rename(real.with_name("real.json"))
    real.symlink_to("real.json")
    action = AiSkillsAction(
        {"users": [{"username": "andres"}], "ai_skills": {"entries": []}},
        ActionContext(target=Target(root=str(root))))
    assert action.import_state()["ai_skills"] == {}
    assert "not captured" in capsys.readouterr().out


def test_captured_block_validates_and_replans_to_nothing(tmp_path):
    files = {JSON_REL: _json(plugins=[PJ]), JSONC_REL: _json(plugin=[GIT])}
    captured = _synced(tmp_path, files)
    assert len(captured["entries"]) == 2
    AiSkillsModel(**captured)
    replay = AiSkillsAction({"users": [{"username": "andres"}],
                             "ai_skills": captured},
                            ActionContext(target=Target(root=str(tmp_path))))
    assert replay.plan(managed=[]) == []


# -- CREATE must not let the CLI write into a hand-written opencode.jsonc ----- #

JSONC_TEXT = '{\n  // mine\n  "model": "x",\n}\n'


def test_create_makes_opencode_json_before_the_cli_runs(tmp_path):
    action = _action(tmp_path, [_entry(PJ)], {JSONC_REL: JSONC_TEXT})
    cfg = tmp_path / "home/andres" / JSON_REL
    seen = []
    _calls(action, [Change("ai_skills", Op.CREATE, _item(PJ))],
           effect=lambda *_a, **_k: seen.append(
               cfg.read_text() if cfg.exists() else None))
    assert seen and json.loads(seen[0]) == {
        "$schema": "https://opencode.ai/config.json"}
    assert cfg.stat().st_uid == os.getuid()


def test_create_leaves_an_existing_opencode_json_untouched(tmp_path):
    action = _action(tmp_path, [_entry(PJ)],
                     {JSON_REL: '{"plugins":  ["a@1"]}'})
    cfg = tmp_path / "home/andres" / JSON_REL
    os.utime(cfg, (1000, 1000))
    before = (cfg.read_bytes(), cfg.stat().st_mtime_ns)
    _calls(action, [Change("ai_skills", Op.CREATE, _item(PJ))])
    assert (cfg.read_bytes(), cfg.stat().st_mtime_ns) == before


def test_create_leaves_opencode_jsonc_byte_identical(tmp_path):
    action = _action(tmp_path, [_entry(PJ)], {JSONC_REL: JSONC_TEXT})
    cfg = tmp_path / "home/andres" / JSONC_REL
    os.utime(cfg, (1000, 1000))
    _calls(action, [Change("ai_skills", Op.CREATE, _item(PJ))])
    assert cfg.read_text() == JSONC_TEXT
    assert cfg.stat().st_mtime_ns == 1000 * 10**9


def test_a_failing_json_creation_fails_the_item_and_runs_no_cli(tmp_path):
    action = _action(tmp_path, [_entry(PJ)])
    with patch("dasik.lib.actions.ai_skills_action.opencode_edit",
               side_effect=OpencodeConfigError("nope")):
        assert _create(action) == []
    assert action.failed_items == [_item(PJ)]


# -- review fixes ------------------------------------------------------------- #

def test_plan_line_of_a_jsonc_delete_names_the_file(tmp_path):
    action = _action(tmp_path, [], {JSONC_REL: _json(plugin=[PJ])})
    (change,) = action.plan(managed=[_item(PJ)])
    assert change.op is Op.DELETE
    assert "in opencode.jsonc — rewritten, comments dropped" in change.render()


def test_plan_line_of_a_json_delete_says_nothing_of_comments(tmp_path):
    action = _action(tmp_path, [], {JSON_REL: _json(plugins=[PJ])})
    (change,) = action.plan(managed=[_item(PJ)])
    assert "comments" not in change.render()


def test_create_without_the_opencode_binary_fails_and_creates_nothing(
        tmp_path, capsys):
    action = _action(tmp_path, [_entry(PJ)], {JSONC_REL: JSONC_TEXT},
                     binary=False)
    assert _create(action) == []
    assert action.failed_items == [_item(PJ)]
    assert not (tmp_path / "home/andres" / JSON_REL).exists()
    assert "/usr/bin/opencode" in capsys.readouterr().out


def test_delete_from_the_legacy_plugin_key_in_json_is_an_edit_not_the_cli(
        tmp_path):
    action = _action(tmp_path, [], {JSON_REL: _json(plugin=[PJ, "keep@1"])})
    assert _calls(action, [Change("ai_skills", Op.DELETE, _item(PJ))]) == []
    assert set(read_plugins(_home(tmp_path))) == {"keep@1"}
    assert action.failed_items == []


def test_delete_in_both_keys_of_json_clears_both(tmp_path):
    action = _action(tmp_path, [], {JSON_REL: _json(plugin=[PJ], plugins=[PJ])})

    def cli_removes_plugins(*_a, **_k):
        data = json.loads((tmp_path / "home/andres" / JSON_REL).read_text())
        data["plugins"] = []
        (tmp_path / "home/andres" / JSON_REL).write_text(json.dumps(data))

    _calls(action, [Change("ai_skills", Op.DELETE, _item(PJ))],
           effect=cli_removes_plugins)
    assert read_plugins(_home(tmp_path)) == {}
    assert action.failed_items == []


def test_a_delete_rebuilt_from_the_manifest_is_revalidated(tmp_path, capsys):
    evil = "x@1.0.0; touch /pwned"
    action = _action(tmp_path, [], {JSON_REL: _json(plugins=[evil])})
    assert _calls(action, [Change("ai_skills", Op.DELETE, _item(evil))]) == []
    assert action.failed_items == [_item(evil)]
    assert "refused" in capsys.readouterr().out

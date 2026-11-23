"""opencode's global config: read both files, write one, never through a link."""

import json
import os
import stat

import pytest

from dasik.lib.actions import opencode_config as oc
from dasik.lib.actions.opencode_config import (
    JSON_REL,
    JSONC_REL,
    SCHEMA_URL,
    OpencodeConfigError,
    edit,
    parse_jsonc,
    read_mcp,
    read_plugins,
)


def _put(home, rel, text):
    path = home / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_parse_jsonc_strips_comments_but_not_inside_strings():
    text = '{\n // c\n "u": "http://x//y", /* b */ "a": [1,2,],\n}'
    assert parse_jsonc(text) == {"u": "http://x//y", "a": [1, 2]}


def test_parse_jsonc_keeps_escaped_quote_and_comment_lookalikes():
    text = r'{"a": "q\" // not /* a comment */", "b": "x,]"}'
    assert parse_jsonc(text) == {"a": 'q" // not /* a comment */', "b": "x,]"}


def test_parse_jsonc_rejects_garbage():
    with pytest.raises(ValueError):
        parse_jsonc("{nope")


def test_read_mcp_unions_both_files_and_both_shapes(tmp_path):
    home = tmp_path
    _put(home, JSON_REL, json.dumps({"mcp": {"a": {"type": "local"}, "servers": {"b": {"x": 1}}}}))
    _put(home, JSONC_REL, '{\n // note\n "mcp": {"c": {"type": "remote"}}\n}')
    found = read_mcp(str(home))
    assert set(found) == {"a", "b", "c"}
    assert found["b"].nested and found["b"].rel == JSON_REL
    assert not found["a"].nested and found["a"].rel == JSON_REL
    assert found["c"].rel == JSONC_REL
    assert found["b"].entry == {"x": 1}


def test_jsonc_wins_a_name_clash(tmp_path):
    _put(tmp_path, JSON_REL, json.dumps({"mcp": {"a": {"v": 1}}}))
    _put(tmp_path, JSONC_REL, json.dumps({"mcp": {"servers": {"a": {"v": 2}}}}))
    found = read_mcp(str(tmp_path))
    assert found["a"].rel == JSONC_REL and found["a"].entry == {"v": 2}


def test_missing_or_broken_files_read_as_nothing(tmp_path):
    empty = tmp_path / "e"
    empty.mkdir()
    broken = tmp_path / "b"
    _put(broken, JSON_REL, "{nope")
    _put(broken, JSONC_REL, "[1]")
    assert read_mcp(str(empty)) == {}
    assert read_plugins(str(broken)) == {}
    assert read_mcp(str(broken)) == {}


def test_read_plugins_takes_both_keys_and_skips_non_strings(tmp_path):
    spec = "superpowers@git+https://github.com/obra/superpowers.git"
    _put(tmp_path, JSONC_REL, json.dumps({"plugin": [spec, ["x", {}]]}))
    _put(tmp_path, JSON_REL, json.dumps({"plugins": ["pj@1.0.0"]}))
    found = read_plugins(str(tmp_path))
    assert set(found) == {spec, "pj@1.0.0"}
    assert found["pj@1.0.0"].nested and found["pj@1.0.0"].rel == JSON_REL
    assert not found[spec].nested and found[spec].rel == JSONC_REL


@pytest.fixture
def chowns(monkeypatch):
    calls = []
    monkeypatch.setattr(oc.os, "chown", lambda p, u, g: calls.append((str(p), u, g)))
    return calls


def test_edit_creates_json_with_schema_owned_by_user(tmp_path, chowns):
    edit(str(tmp_path), JSON_REL, lambda d: d.setdefault("mcp", {}), 1000, 1001)
    data = json.loads((tmp_path / JSON_REL).read_text())
    assert data == {"$schema": SCHEMA_URL, "mcp": {}}
    paths = {p for p, _, _ in chowns}
    assert paths == {
        str(tmp_path / ".config"),
        str(tmp_path / ".config/opencode"),
        str(tmp_path / JSON_REL),
    }
    assert all((u, g) == (1000, 1001) for _, u, g in chowns)
    assert stat.S_IMODE((tmp_path / JSON_REL).stat().st_mode) == 0o644


def test_edit_preserves_mode_and_other_keys(tmp_path, chowns):
    path = _put(tmp_path, JSON_REL, json.dumps({"provider": {"p": 1}}))
    path.chmod(0o600)
    edit(str(tmp_path), JSON_REL, lambda d: d.update(mcp={}), 1, 1)
    assert json.loads(path.read_text()) == {"provider": {"p": 1}, "mcp": {}}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_edit_reads_jsonc_input(tmp_path, chowns):
    _put(tmp_path, JSONC_REL, '{\n // keep\n "a": 1,\n}')
    edit(str(tmp_path), JSONC_REL, lambda d: d.update(b=2), 1, 1)
    assert json.loads((tmp_path / JSONC_REL).read_text()) == {"a": 1, "b": 2}


def test_edit_refuses_a_symlinked_file(tmp_path, chowns):
    target = tmp_path / "victim.json"
    target.write_text('{"keep": 1}')
    (tmp_path / ".config/opencode").mkdir(parents=True)
    (tmp_path / JSON_REL).symlink_to(target)
    with pytest.raises(OpencodeConfigError):
        edit(str(tmp_path), JSON_REL, lambda d: d.update(x=1), 1, 1)
    assert target.read_text() == '{"keep": 1}'
    assert os.path.islink(tmp_path / JSON_REL)


def test_edit_refuses_a_symlinked_parent(tmp_path, chowns):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / ".config").mkdir()
    (tmp_path / ".config/opencode").symlink_to(elsewhere)
    with pytest.raises(OpencodeConfigError):
        edit(str(tmp_path), JSON_REL, lambda d: d.update(x=1), 1, 1)
    assert list(elsewhere.iterdir()) == []


def test_edit_refuses_a_symlinked_grandparent(tmp_path, chowns):
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "opencode").mkdir(parents=True)
    (tmp_path / ".config").symlink_to(elsewhere)
    with pytest.raises(OpencodeConfigError):
        edit(str(tmp_path), JSON_REL, lambda d: d.update(x=1), 1, 1)
    assert list((elsewhere / "opencode").iterdir()) == []


def test_edit_refuses_a_non_directory_parent(tmp_path, chowns):
    (tmp_path / ".config").write_text("file")
    with pytest.raises(OpencodeConfigError):
        edit(str(tmp_path), JSON_REL, lambda d: None, 1, 1)


def test_edit_refuses_an_unparseable_file_and_leaves_it(tmp_path, chowns):
    path = _put(tmp_path, JSON_REL, "{nope")
    with pytest.raises(OpencodeConfigError):
        edit(str(tmp_path), JSON_REL, lambda d: None, 1, 1)
    assert path.read_text() == "{nope"


def test_edit_refuses_a_non_dict_top_level(tmp_path, chowns):
    path = _put(tmp_path, JSON_REL, "[1]")
    with pytest.raises(OpencodeConfigError):
        edit(str(tmp_path), JSON_REL, lambda d: None, 1, 1)
    assert path.read_text() == "[1]"

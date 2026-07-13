"""Tests for JsonParser (CLAUDE.md § Tests — json_parser/ was "Pending: no tests").

JsonParser is the untrusted-input boundary: it loads the user's JSON, validates
it against the pydantic JsonModel, and exits with a clear code on failure
(1 = file missing, 2 = invalid). These tests pin the happy path and each failure
mode so the boundary can't regress into an ugly traceback.

Note: JsonModel *requires* locales/timezone/network/hostname (the legacy
JsonParser path is stricter than the v3 CLI, which loads raw dicts).
"""
import json

import pytest

from dasik.lib.json_parser.json_parser import JsonParser


def _valid() -> dict:
    return {
        "hostname": "box",
        "enable_microcode": True,
        "locales": {
            "selected_locales": ["en_US.UTF-8 UTF-8"],
            "desired_locale": "en_US.UTF-8",
            "desired_tty_layout": "us",
        },
        "timezone": {"region": "Etc", "city": "UTC"},
        "network": {"type": "systemd-networkd"},
    }


def _write(tmp_path, data):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    return str(p)


def test_valid_config_parses_and_exposes_fields(tmp_path):
    jp = JsonParser(_write(tmp_path, _valid()))
    data = jp.debug()
    assert data["hostname"] == "box"
    assert data["enable_microcode"] is True
    assert data["timezone"]["city"] == "UTC"
    assert jp.get_attr("hostname") == "box"


def test_get_attr_returns_none_for_unknown_key(tmp_path):
    jp = JsonParser(_write(tmp_path, _valid()))
    assert jp.get_attr("does-not-exist") is None


def test_unknown_top_level_keys_are_ignored(tmp_path):
    cfg = _valid()
    cfg["totally_unknown_section"] = {"x": 1}
    jp = JsonParser(_write(tmp_path, cfg))          # must not raise
    assert jp.get_attr("totally_unknown_section") is None


def test_missing_required_section_exits_with_code_2(tmp_path):
    cfg = _valid()
    del cfg["network"]                               # required section removed
    with pytest.raises(SystemExit) as exc:
        JsonParser(_write(tmp_path, cfg))
    assert exc.value.code == 2


def test_bad_enum_value_exits_with_code_2(tmp_path):
    cfg = _valid()
    cfg["network"]["type"] = "bogus-manager"         # not in the Literal
    with pytest.raises(SystemExit) as exc:
        JsonParser(_write(tmp_path, cfg))
    assert exc.value.code == 2


def test_missing_file_exits_with_code_1(tmp_path):
    with pytest.raises(SystemExit) as exc:
        JsonParser(str(tmp_path / "nope.json"))
    assert exc.value.code == 1


def test_malformed_json_exits_cleanly(tmp_path):
    """A syntactically broken file must exit cleanly (code 2), not crash with an
    uncaught JSONDecodeError traceback."""
    p = tmp_path / "config.json"
    p.write_text("{ this is : not json ")
    with pytest.raises(SystemExit) as exc:
        JsonParser(str(p))
    assert exc.value.code == 2

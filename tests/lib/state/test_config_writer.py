import json

from dasik.lib.state.config_writer import ConfigWriter


def test_merge_overrides_existing_domain():
    existing = {"packages": ["git"], "metadata": {"name": "demo"}}
    result = ConfigWriter.merge(existing, {"packages": ["git", "htop"]})
    assert result["packages"] == ["git", "htop"]


def test_merge_adds_new_domain_for_bootstrap():
    existing = {"metadata": {"name": "fresh"}}
    result = ConfigWriter.merge(existing, {"packages": ["git", "htop"]})
    assert result["packages"] == ["git", "htop"]


def test_merge_passes_through_unknown_keys_and_metadata():
    existing = {"packages": ["git"], "metadata": {"k": "v"}, "kvm": {"enabled": True}}
    result = ConfigWriter.merge(existing, {"packages": ["git", "vlc"]})
    assert result["metadata"] == {"k": "v"}
    assert result["kvm"] == {"enabled": True}


def test_merge_does_not_mutate_inputs():
    existing = {"packages": ["git"]}
    fragments = {"packages": ["git", "htop"]}
    ConfigWriter.merge(existing, fragments)
    assert existing == {"packages": ["git"]}  # untouched
    assert fragments == {"packages": ["git", "htop"]}


def test_merge_preserves_existing_key_order():
    existing = {"metadata": {}, "packages": ["git"], "kvm": {}}
    result = ConfigWriter.merge(existing, {"packages": ["git", "x"]})
    assert list(result.keys()) == ["metadata", "packages", "kvm"]


def test_merge_empty_fragments_returns_equal_copy():
    existing = {"packages": ["git"], "metadata": {}}
    result = ConfigWriter.merge(existing, {})
    assert result == existing
    assert result is not existing


def test_write_round_trips_through_json(tmp_path):
    path = tmp_path / "config.json"
    config = {"packages": ["git", "htop"], "metadata": {"name": "demo"}}
    ConfigWriter.write(config, path)
    assert json.loads(path.read_text()) == config


def test_write_accepts_str_path(tmp_path):
    path = tmp_path / "config.json"
    ConfigWriter.write({"packages": []}, str(path))
    assert json.loads(path.read_text()) == {"packages": []}


def test_write_produces_trailing_newline(tmp_path):
    path = tmp_path / "config.json"
    ConfigWriter.write({"k": "v"}, path)
    assert path.read_bytes().endswith(b"\n")


def test_merge_result_does_not_alias_fragment_values():
    fragments = {"packages": ["git"]}
    result = ConfigWriter.merge({}, fragments)
    result["packages"].append("htop")
    assert fragments == {"packages": ["git"]}  # unchanged

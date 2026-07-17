"""Manifest.action_state — per-action free-form state (PLAN v3 §10).

Holds e.g. packages.source_refs {name: applied_sha} so a changed Git ref is
detected even when the package name is already installed. Old manifests without
the field load cleanly (schema back-compat)."""
from dasik.lib.state.state_store import Manifest, STATE_VERSION


def test_action_state_defaults_empty():
    assert Manifest().action_state == {}


def test_action_state_round_trips():
    m = Manifest(action_state={"packages": {"source_refs": {"config-saver": "a" * 40}}})
    d = m.to_dict()
    assert d["action_state"]["packages"]["source_refs"]["config-saver"] == "a" * 40
    back = Manifest.from_dict(d)
    assert back.action_state == m.action_state


def test_old_manifest_without_action_state_loads():
    old = {"version": 1, "generation": 3, "managed": {"packages": ["git"]}}
    m = Manifest.from_dict(old)
    assert m.action_state == {}
    assert m.managed == {"packages": ["git"]}


def test_schema_version_bumped():
    # action_state is a new schema feature — version must be >= 2.
    assert STATE_VERSION >= 2

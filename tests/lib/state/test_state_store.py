from dasik.lib.state.state_store import Manifest, StateStore, STATE_VERSION


def test_load_missing_returns_default(tmp_target):
    store = StateStore(tmp_target)
    m = store.load()
    assert m.version == STATE_VERSION
    assert m.generation == 0
    assert m.managed == {}


def test_save_then_load_round_trips(tmp_target):
    store = StateStore(tmp_target)
    m = Manifest(
        generation=2,
        applied_at="2026-05-27T21:00:00Z",
        config_hash="sha256:abc",
        managed={"packages": ["git", "htop"], "users": ["alice"]},
    )
    store.save(m)

    loaded = StateStore(tmp_target).load()
    assert loaded.generation == 2
    assert loaded.applied_at == "2026-05-27T21:00:00Z"
    assert loaded.config_hash == "sha256:abc"
    assert loaded.managed == {"packages": ["git", "htop"], "users": ["alice"]}


def test_save_creates_state_under_var_lib_dasik(tmp_target):
    store = StateStore(tmp_target)
    store.save(Manifest())
    assert store.state_path.name == "state.json"
    assert store.state_path.parent.name == "dasik"
    assert store.state_path.exists()

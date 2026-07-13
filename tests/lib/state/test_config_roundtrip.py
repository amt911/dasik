"""Property-based tests for ConfigWriter (CLAUDE.md § Quality: config round-trips).

`sync` reads reality back into the config file via ConfigWriter. Two invariants
matter: writing a config then reading it back must round-trip exactly (no silent
data loss on rewrite), and `merge` must splice fragments over an existing config
without mutating either input. These properties assert both across generated
JSON-shaped configs.
"""
import copy
import json
import tempfile
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from dasik.lib.state.config_writer import ConfigWriter

# JSON-safe values: the config file is JSON, so anything that survives a
# json.dumps/loads round-trip. Surrogates are excluded (not valid in a JSON
# document); NaN/±inf are excluded (not valid JSON numbers).
_text = st.text(st.characters(blacklist_categories=("Cs",)), max_size=12)
_scalars = (
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | _text
)
_json = st.recursive(
    _scalars,
    lambda children: st.lists(children, max_size=4)
    | st.dictionaries(_text, children, max_size=4),
    max_leaves=15,
)
# Top-level config is always an object with string keys.
_configs = st.dictionaries(st.text(min_size=1, max_size=8), _json, max_size=5)


@given(config=_configs)
def test_write_then_read_roundtrips(config):
    """ConfigWriter.write(cfg) then json.load reproduces cfg exactly.

    This is the file-level idempotency of `sync`: rewriting the config must not
    perturb its content (a re-sync of already-matching reality is a no-op).
    """
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "config.json"
        ConfigWriter.write(config, path)
        reloaded = json.loads(path.read_text())
    assert reloaded == config


@given(config=_configs)
def test_write_is_valid_json_with_trailing_newline(config):
    """The written file is valid JSON and ends in exactly one newline."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "config.json"
        ConfigWriter.write(config, path)
        text = path.read_text()
    json.loads(text)  # parses without error
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


@given(existing=_configs, fragments=_configs)
def test_merge_overrides_fragments_and_preserves_the_rest(existing, fragments):
    """merge(existing, fragments): fragment keys win, other existing keys pass through."""
    merged = ConfigWriter.merge(existing, fragments)
    assert set(merged) == set(existing) | set(fragments)
    for key, value in fragments.items():
        assert merged[key] == value
    for key, value in existing.items():
        if key not in fragments:
            assert merged[key] == value


@given(existing=_configs, fragments=_configs)
def test_merge_does_not_mutate_its_inputs(existing, fragments):
    """merge is pure — neither argument is mutated, and fragment values are
    deep-copied into the result (mutating the result cannot alias back).
    """
    existing_before = copy.deepcopy(existing)
    fragments_before = copy.deepcopy(fragments)
    merged = ConfigWriter.merge(existing, fragments)

    assert existing == existing_before
    assert fragments == fragments_before

    # Deep-copy contract: spliced values are not the same objects as fragments'.
    for key, value in fragments.items():
        if isinstance(value, (list, dict)) and value:
            assert merged[key] is not value

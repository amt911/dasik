"""The writeback's one invariant: it loses nothing.

Whatever `sync` captured, reading the config back must produce exactly that —
otherwise the next `plan` sees a difference nobody made, and `sync` → `plan`
stops being silent. Everything else the writeback does (which file a value
lands in, which directives survive) is a nicety; this is the correctness
property, so it is the one Hypothesis hammers.
"""
import json

from hypothesis import given, settings
from hypothesis import strategies as st

from dasik.lib.json_parser.includes import resolve_includes
from dasik.lib.json_parser.writeback import write_back

# Values a config actually holds: strings (hostnames, hashes, file bodies),
# lists of strings (packages, groups) and small objects.
_scalars = st.text(min_size=0, max_size=40)
_lists = st.lists(_scalars, max_size=5)
_objects = st.dictionaries(st.sampled_from(["a", "b", "path", "name"]),
                           st.one_of(_scalars, _lists), max_size=3)
_values = st.one_of(_scalars, _lists, _objects)


def _roundtrip(tmp_path, raw_root, files, captured):
    root = tmp_path / "c.json"
    root.write_text(json.dumps(raw_root, indent=2) + "\n")
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    write_back(root, captured)
    return resolve_includes(json.loads(root.read_text()), tmp_path)


@settings(max_examples=200, deadline=None)
@given(before=_values, after=_values)
def test_include_roundtrips_whatever_was_captured(tmp_path_factory, before, after):
    tmp_path = tmp_path_factory.mktemp("inc")
    assert _roundtrip(
        tmp_path,
        {"k": {"$include": "v.json"}},
        {"v.json": json.dumps(before)},
        {"k": after},
    ) == {"k": after}


@settings(max_examples=200, deadline=None)
@given(before=_scalars, after=_scalars)
def test_include_text_roundtrips_whatever_was_captured(tmp_path_factory, before, after):
    tmp_path = tmp_path_factory.mktemp("txt")
    assert _roundtrip(
        tmp_path,
        {"k": {"$include_text": "v.conf"}},
        {"v.conf": before},
        {"k": after},
    ) == {"k": after}


@settings(max_examples=300, deadline=None)
@given(before=_scalars, after=_scalars)
def test_include_line_roundtrips_whatever_was_captured(tmp_path_factory, before, after):
    """A secret is one line, but the writeback must not silently mangle a value
    that is not: writing it and reading it back has to yield the same string."""
    tmp_path = tmp_path_factory.mktemp("line")
    assert _roundtrip(
        tmp_path,
        {"k": {"$include_line": "v.txt"}},
        {"v.txt": before + "\n" if before else "seed\n"},
        {"k": after},
    ) == {"k": after}


@settings(max_examples=200, deadline=None)
@given(base=_lists, dev=_lists, after=_lists)
def test_concat_roundtrips_whatever_was_captured(tmp_path_factory, base, dev, after):
    tmp_path = tmp_path_factory.mktemp("cat")
    assert _roundtrip(
        tmp_path,
        {"k": {"$concat": [{"$include": "base.json"}, {"$include": "dev.json"}]}},
        {"base.json": json.dumps(base), "dev.json": json.dumps(dev)},
        {"k": after},
    ) == {"k": after}

"""The one TOML reader dasik has.

Two agents keep state in ``~/.codex/config.toml`` — the plugins ``ai_skills``
reads and the ``[mcp_servers]`` table ``mcp_servers`` reads — so there is
exactly one parser for it. Every test runs twice: once through ``tomllib``
(3.11+, what actually runs on Arch) and once through the hand parser that is
the 3.10 fallback, because two parsers that disagree would make ``plan`` and
``sync`` disagree about the same machine.
"""
import pytest

from dasik.lib.actions import toml_reader
from dasik.lib.actions.toml_reader import load_toml


@pytest.fixture(params=["tomllib", "fallback"])
def parser(request, monkeypatch):
    """Force each code path in turn."""
    if request.param == "fallback":
        monkeypatch.setattr(toml_reader, "_TOMLLIB", None)
    elif toml_reader._TOMLLIB is None:          # pragma: no cover - 3.10 only
        pytest.skip("tomllib not available on this interpreter")
    return request.param


def test_a_plain_table_of_strings(parser):
    assert load_toml('[mcp_servers.ink]\ncommand = "uvx"\n') == {
        "mcp_servers": {"ink": {"command": "uvx"}}}


def test_an_array_of_strings(parser):
    assert load_toml('[a]\nargs = ["x", "y"]\n') == {"a": {"args": ["x", "y"]}}


def test_an_empty_array(parser):
    assert load_toml('[a]\nargs = []\n') == {"a": {"args": []}}


def test_an_inline_table(parser):
    assert load_toml('[a]\nenv = { K = "v", J = "w" }\n') == {
        "a": {"env": {"K": "v", "J": "w"}}}


def test_an_empty_inline_table(parser):
    assert load_toml('[a]\nenv = {}\n') == {"a": {"env": {}}}


def test_booleans_and_integers(parser):
    assert load_toml('[a]\nenabled = true\noff = false\nn = 3\n') == {
        "a": {"enabled": True, "off": False, "n": 3}}


def test_a_quoted_key_segment_keeps_its_dots_and_slashes(parser):
    """`[projects."/home/andres"]` is ONE key, not a nested tree."""
    assert load_toml('[projects."/home/andres"]\ntrust_level = "trusted"\n') == {
        "projects": {"/home/andres": {"trust_level": "trusted"}}}


def test_top_level_keys_and_comments(parser):
    assert load_toml('# hi\nmodel = "gpt-5.6-sol"\n\n[a]\nb = "c"\n') == {
        "model": "gpt-5.6-sol", "a": {"b": "c"}}


def test_nested_sections_merge(parser):
    assert load_toml('[a.b]\nx = "1"\n\n[a.c]\ny = "2"\n') == {
        "a": {"b": {"x": "1"}, "c": {"y": "2"}}}


def test_a_malformed_section_gives_up_on_the_whole_file(parser):
    """tomllib raises; the fallback must refuse the same file, not half of it.

    Half a config is worse than none: it would report a machine that carries
    fewer servers than it really does, and the next apply would re-register
    something that is already there.
    """
    assert load_toml('[plugins."x@y"\nenabled = true\n') == {}


def test_an_empty_string_is_an_empty_document(parser):
    assert load_toml("") == {}


def test_escaped_quotes_inside_a_value(parser):
    assert load_toml('[a]\nb = "say \\"hi\\""\n') == {"a": {"b": 'say "hi"'}}

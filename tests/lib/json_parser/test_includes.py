"""Splitting one config across files.

A real config is ~430 lines, most of it two lists (172 packages) and a handful of
verbatim file bodies. Three directives keep it readable without inventing a
config language:

* ``{"$include": "path.json"}``      -> the parsed JSON of that file
* ``{"$include_text": "path.conf"}`` -> that file's bytes, as a string
* ``{"$concat": [ ... ]}``           -> the lists inside, flattened into one

Paths are relative to the file that names them, so a subtree can be moved
wholesale. Anything else — absolute paths, `..`, a directive next to other keys,
a cycle — is an error, because each is a way to load something the reader of the
config did not expect.
"""
import json

import pytest

from dasik.lib.json_parser.includes import ConfigIncludeError, resolve_includes


def _write(tmp_path, name, data):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    return path


def _resolve(tmp_path, data):
    return resolve_includes(data, tmp_path)


# --- $include ---------------------------------------------------------------

def test_include_replaces_the_object_with_the_parsed_file(tmp_path):
    _write(tmp_path, "packages.json", ["git", "vim"])
    out = _resolve(tmp_path, {"packages": {"$include": "packages.json"}})
    assert out == {"packages": ["git", "vim"]}


def test_include_works_inside_a_list(tmp_path):
    _write(tmp_path, "user.json", {"username": "andres"})
    out = _resolve(tmp_path, {"users": [{"$include": "user.json"}]})
    assert out == {"users": [{"username": "andres"}]}


def test_include_is_resolved_relative_to_the_file_that_names_it(tmp_path):
    _write(tmp_path, "disks/layout.json", {"disks": {"$include": "parts.json"}})
    _write(tmp_path, "disks/parts.json", [{"label": "root"}])
    out = _resolve(tmp_path, {"$include": "disks/layout.json"})
    assert out == {"disks": [{"label": "root"}]}


def test_nested_includes_resolve_all_the_way_down(tmp_path):
    _write(tmp_path, "a.json", {"b": {"$include": "b.json"}})
    _write(tmp_path, "b.json", {"c": {"$include": "c.json"}})
    _write(tmp_path, "c.json", '"deep"')          # $include always parses JSON
    assert _resolve(tmp_path, {"$include": "a.json"}) == {"b": {"c": "deep"}}


# --- $include_text ----------------------------------------------------------

def test_include_text_returns_the_file_verbatim(tmp_path):
    _write(tmp_path, "pam/sudo", "#%PAM-1.0\nauth sufficient pam_fprintd.so\n")
    out = _resolve(tmp_path, {"files": [
        {"path": "/etc/pam.d/sudo", "content": {"$include_text": "pam/sudo"}}]})
    assert out["files"][0]["content"] == "#%PAM-1.0\nauth sufficient pam_fprintd.so\n"


def test_include_text_does_not_parse_json(tmp_path):
    _write(tmp_path, "raw.conf", "{not json at all")
    assert _resolve(tmp_path, {"$include_text": "raw.conf"}) == "{not json at all"


# --- $concat ----------------------------------------------------------------

def test_concat_flattens_lists(tmp_path):
    _write(tmp_path, "base.json", ["base", "linux"])
    _write(tmp_path, "kde.json", ["plasma-meta"])
    out = _resolve(tmp_path, {"packages": {"$concat": [
        {"$include": "base.json"}, {"$include": "kde.json"}, ["extra"]]}})
    assert out == {"packages": ["base", "linux", "plasma-meta", "extra"]}


def test_concat_rejects_a_non_list_member(tmp_path):
    with pytest.raises(ConfigIncludeError, match="list"):
        _resolve(tmp_path, {"$concat": [{"a": 1}]})


# --- the ways it must refuse ------------------------------------------------

def test_a_directive_may_not_share_its_object(tmp_path):
    _write(tmp_path, "x.json", [])
    with pytest.raises(ConfigIncludeError, match="only key"):
        _resolve(tmp_path, {"packages": {"$include": "x.json", "extra": 1}})


def test_absolute_paths_are_refused(tmp_path):
    with pytest.raises(ConfigIncludeError, match="relative"):
        _resolve(tmp_path, {"$include": "/etc/shadow"})


def test_parent_traversal_is_refused(tmp_path):
    with pytest.raises(ConfigIncludeError, match=r"\.\."):
        _resolve(tmp_path, {"$include": "../secrets.json"})


def test_a_missing_file_names_itself(tmp_path):
    with pytest.raises(ConfigIncludeError, match="nope.json"):
        _resolve(tmp_path, {"$include": "nope.json"})


def test_broken_json_in_an_included_file_names_the_file(tmp_path):
    _write(tmp_path, "bad.json", "{oops")
    with pytest.raises(ConfigIncludeError, match="bad.json"):
        _resolve(tmp_path, {"$include": "bad.json"})


def test_a_cycle_is_reported_instead_of_recursing_forever(tmp_path):
    _write(tmp_path, "a.json", {"$include": "b.json"})
    _write(tmp_path, "b.json", {"$include": "a.json"})
    with pytest.raises(ConfigIncludeError, match="cycle"):
        _resolve(tmp_path, {"$include": "a.json"})


def test_the_same_file_may_be_included_twice_in_different_places(tmp_path):
    """Not a cycle: a shared fragment is a feature."""
    _write(tmp_path, "shared.json", ["x"])
    out = _resolve(tmp_path, {"a": {"$include": "shared.json"},
                              "b": {"$include": "shared.json"}})
    assert out == {"a": ["x"], "b": ["x"]}


# --- configs that use none of this are untouched ----------------------------

def test_a_plain_config_is_returned_unchanged(tmp_path):
    cfg = {"hostname": "x", "packages": ["git"], "n": 1, "b": True, "z": None}
    assert _resolve(tmp_path, cfg) == cfg


def test_a_dollar_key_that_is_not_a_directive_is_left_alone(tmp_path):
    cfg = {"content": "PS1='$PWD'", "$weird": 1}
    assert _resolve(tmp_path, cfg) == cfg


# --- $include_line: secrets, without the trailing newline -------------------
#
# `$include_text` is verbatim on purpose (a PAM file needs its final newline),
# which makes it the WRONG tool for a password hash: `usermod -p '$y$…\n'` sets
# a hash nobody can log in with, and nothing complains. So secrets get their own
# directive that reads one line and strips it.

def test_include_line_strips_the_trailing_newline(tmp_path):
    _write(tmp_path, "secrets/hash", "$y$j9T$abc\n")
    out = _resolve(tmp_path, {"hashed_password": {"$include_line": "secrets/hash"}})
    assert out == {"hashed_password": "$y$j9T$abc"}


def test_include_line_ignores_everything_after_the_first_line(tmp_path):
    _write(tmp_path, "secrets/pass", "hunter2\n# a comment about it\n")
    assert _resolve(tmp_path, {"$include_line": "secrets/pass"}) == "hunter2"


def test_include_line_strips_surrounding_whitespace(tmp_path):
    _write(tmp_path, "secrets/pass", "  hunter2  \n")
    assert _resolve(tmp_path, {"$include_line": "secrets/pass"}) == "hunter2"


def test_include_line_on_an_empty_file_is_an_error(tmp_path):
    _write(tmp_path, "secrets/empty", "\n")
    with pytest.raises(ConfigIncludeError, match="empty"):
        _resolve(tmp_path, {"$include_line": "secrets/empty"})


def test_include_line_obeys_the_same_path_rules(tmp_path):
    with pytest.raises(ConfigIncludeError, match=r"\.\."):
        _resolve(tmp_path, {"$include_line": "../secret"})


def test_the_same_secret_can_be_referenced_twice(tmp_path):
    """Both LUKS partitions want the SAME passphrase, from one file."""
    _write(tmp_path, "secrets/luks", "passphrase\n")
    out = _resolve(tmp_path, {"a": {"$include_line": "secrets/luks"},
                              "b": {"$include_line": "secrets/luks"}})
    assert out == {"a": "passphrase", "b": "passphrase"}

"""Tests for the config_saver.source model — the same control-character
injection class fixed for pacman_model.py and package_model.py: `urlsplit`
strips \\t\\r\\n from its internal parsing copy, but a validator that checks
the parsed result and then returns the ORIGINAL string unchanged (or, for
`ref`/`subdir`, a validator with no control-character check at all) lets
those bytes straight through into a value dasik writes to disk or shells out
with.
"""
import pytest
from pydantic import ValidationError

from dasik.lib.models.config_saver_model import ConfigSaverSource

_SHA = "a520605367e13ec25db4c3c7e1c4bf46175ba8cd"
_CONTROL_CHARS = ["\n", "\r", "\t", "\x7f"]


def _source(**over):
    data = {
        "url": "https://github.com/amt911/config-saver-aur.git",
        "ref": _SHA,
    }
    data.update(over)
    return ConfigSaverSource(**data)


# --- currently-valid examples keep validating -----------------------------

def test_source_valid():
    s = _source()
    assert s.url == "https://github.com/amt911/config-saver-aur.git"
    assert s.ref == _SHA
    assert s.subdir == "."


def test_source_subdir_kept():
    assert _source(subdir="pkg/sub").subdir == "pkg/sub"


def test_source_rejects_http():
    with pytest.raises(ValidationError):
        _source(url="http://github.com/amt911/config-saver-aur.git")


def test_source_rejects_missing_git_suffix():
    with pytest.raises(ValidationError):
        _source(url="https://github.com/amt911/config-saver-aur")


def test_source_rejects_short_sha():
    with pytest.raises(ValidationError):
        _source(ref="a520605")


# --- control-character injection ------------------------------------------

@pytest.mark.parametrize("ctrl", _CONTROL_CHARS)
def test_source_url_rejects_control_chars(ctrl):
    with pytest.raises(ValidationError):
        _source(url=f"https://github.com/a{ctrl}/b.git")


@pytest.mark.parametrize("ctrl", _CONTROL_CHARS)
def test_source_url_rejects_control_chars_before_git_suffix(ctrl):
    with pytest.raises(ValidationError):
        _source(url=f"https://github.com/a/b{ctrl}.git")


@pytest.mark.parametrize("ctrl", _CONTROL_CHARS)
def test_source_ref_rejects_control_chars(ctrl):
    bad_ref = ("a" * 39) + ctrl
    with pytest.raises(ValidationError):
        _source(ref=bad_ref)


@pytest.mark.parametrize("ctrl", _CONTROL_CHARS)
def test_source_subdir_rejects_control_chars(ctrl):
    with pytest.raises(ValidationError):
        _source(subdir=f"pkg{ctrl}sub")

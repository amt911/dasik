"""The `home_files` entry model.

A file under `$HOME` is addressed as (user, relative path). Absolute paths and
traversal are refused at the boundary: the machine's own /etc/passwd decides
where a home is, and a config must not be able to point outside it.
"""
import pytest
from pydantic import ValidationError

from dasik.lib.models.file_model import HomeFile


def _hf(**over):
    data = {"user": "andres", "path": ".config/autostart/x.desktop",
            "content": "[Desktop Entry]\n"}
    data.update(over)
    return HomeFile(**data)


def test_valid_entry():
    f = _hf()
    assert (f.user, f.path) == ("andres", ".config/autostart/x.desktop")
    assert f.mode is None


def test_mode_is_kept():
    assert _hf(mode="0600").mode == "0600"


def test_rejects_non_octal_mode():
    with pytest.raises(ValidationError):
        _hf(mode="rw-------")


@pytest.mark.parametrize("bad", ["/etc/passwd", "/.bashrc"])
def test_rejects_an_absolute_path(bad):
    with pytest.raises(ValidationError):
        _hf(path=bad)


@pytest.mark.parametrize("bad", ["../../etc/passwd", ".config/../../x", ".."])
def test_rejects_traversal_out_of_the_home(bad):
    with pytest.raises(ValidationError):
        _hf(path=bad)


@pytest.mark.parametrize("bad", ["", "   ", "./"])
def test_rejects_an_empty_path(bad):
    with pytest.raises(ValidationError):
        _hf(path=bad)


@pytest.mark.parametrize("bad", ["-rf", "a:b", "a b", "root/x", "1abc", ""])
def test_rejects_an_unsafe_username(bad):
    with pytest.raises(ValidationError):
        _hf(user=bad)


@pytest.mark.parametrize("good", ["andres", "_svc", "user1", "a-b_c", "root"])
def test_accepts_a_valid_username(good):
    assert _hf(user=good).user == good


def test_json_model_accepts_home_files():
    from dasik.lib.models.json_model import JsonModel

    m = JsonModel(
        locales={"selected_locales": [], "desired_locale": "en_US.UTF-8",
                 "desired_tty_layout": "us"},
        timezone={"region": "Europe", "city": "Madrid"},
        network={"type": "NetworkManager", "add_default_hosts": True},
        hostname="arch",
        home_files=[{"user": "andres", "path": ".gitconfig", "content": "[user]\n"}],
    )
    assert m.home_files[0].path == ".gitconfig"

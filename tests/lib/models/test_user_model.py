import pytest

from dasik.lib.models.user_model import UserModel
from dasik.lib.models.json_model import JsonModel


_HASH = "$6$abc$" + "x" * 86


def test_accepts_hashed_password():
    m = UserModel(username="alice", hashed_password=_HASH)
    assert m.hashed_password == _HASH
    assert m.shell == "/bin/bash"
    assert m.groups == []


def test_rejects_plaintext_password():
    with pytest.raises(ValueError):
        UserModel(username="alice", hashed_password="hunter2")


def test_json_model_remove_home_on_delete_defaults_false():
    m = JsonModel(
        locales={"selected_locales": ["en_US.UTF-8 UTF-8"],
                 "desired_locale": "en_US.UTF-8", "desired_tty_layout": "us"},
        timezone={"region": "Europe", "city": "Madrid"},
        network={"type": "NetworkManager", "add_default_hosts": True},
        hostname="arch",
    )
    assert m.remove_home_on_delete is False

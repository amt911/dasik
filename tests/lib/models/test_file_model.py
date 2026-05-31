import pytest

from dasik.lib.models.file_model import FileEntry
from dasik.lib.models.json_model import JsonModel


def test_accepts_name_and_content():
    e = FileEntry(name="99-razer.rules", content="SUBSYSTEM==...")
    assert e.name == "99-razer.rules"
    assert e.content == "SUBSYSTEM==..."


def test_rejects_name_with_slash():
    with pytest.raises(ValueError):
        FileEntry(name="sub/dir.rules", content="x")


def test_rejects_empty_name():
    with pytest.raises(ValueError):
        FileEntry(name="", content="x")


def test_json_model_accepts_file_entry_sections():
    m = JsonModel(
        locales={"selected_locales": ["en_US.UTF-8 UTF-8"],
                 "desired_locale": "en_US.UTF-8", "desired_tty_layout": "us"},
        timezone={"region": "Europe", "city": "Madrid"},
        network={"type": "NetworkManager", "add_default_hosts": True},
        hostname="arch",
        udev_rules=[{"name": "99-x.rules", "content": "RULE"}],
        modprobe_conf=[{"name": "x.conf", "content": "options x"}],
        profile_d=[{"name": "x.sh", "content": "export A=1"}],
        etc_environment=["EDITOR=vim"],
    )
    assert m.udev_rules[0].name == "99-x.rules"
    assert m.etc_environment == ["EDITOR=vim"]

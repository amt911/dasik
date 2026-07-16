import pytest

from dasik.lib.models.file_model import EtcFile
from dasik.lib.models.json_model import JsonModel


def _base(**extra):
    return JsonModel(
        locales={"selected_locales": ["en_US.UTF-8 UTF-8"],
                 "desired_locale": "en_US.UTF-8", "desired_tty_layout": "us"},
        timezone={"region": "Europe", "city": "Madrid"},
        network={"type": "NetworkManager", "add_default_hosts": True},
        hostname="arch",
        **extra,
    )


def test_accepts_absolute_path():
    e = EtcFile(path="/etc/ssh/sshd_config.d/99-dasik.conf", content="X11Forwarding no")
    assert e.path == "/etc/ssh/sshd_config.d/99-dasik.conf"
    assert e.content == "X11Forwarding no"


def test_rejects_relative_path():
    with pytest.raises(ValueError):
        EtcFile(path="etc/x.conf", content="c")


def test_rejects_traversal():
    with pytest.raises(ValueError):
        EtcFile(path="/etc/../root/x", content="c")


def test_json_model_files_defaults_empty_and_accepts_entries():
    assert _base().files == []
    m = _base(files=[{"path": "/etc/samba/smb.conf", "content": "[global]\n"}])
    assert m.files[0].path == "/etc/samba/smb.conf"


# --- optional mode -------------------------------------------------------- #

def test_etc_file_mode_octal_accepted():
    from dasik.lib.models.file_model import EtcFile
    assert EtcFile(path="/etc/wireguard/wg0.conf", content="x", mode="0600").mode == "0600"


def test_etc_file_mode_optional_defaults_none():
    from dasik.lib.models.file_model import EtcFile
    assert EtcFile(path="/etc/x", content="x").mode is None


def test_etc_file_mode_invalid_rejected():
    import pytest
    from pydantic import ValidationError
    from dasik.lib.models.file_model import EtcFile
    with pytest.raises(ValidationError):
        EtcFile(path="/etc/x", content="x", mode="notoctal")

from unittest.mock import MagicMock, patch

from dasik.lib.actions.trim_action import TrimAction


def _run(stdout=b"", returncode=0):
    return MagicMock(return_value=MagicMock(stdout=stdout, returncode=returncode))


def test_disabled_is_never_needed():
    a = TrimAction({"enable_trim": False})
    assert a.is_needed() is False


def test_needed_when_timer_not_enabled():
    a = TrimAction({"enable_trim": True})
    with patch("dasik.lib.actions.trim_action.subprocess.run", _run(stdout=b"disabled\n")):
        assert a.is_needed() is True


def test_not_needed_when_timer_already_enabled():
    a = TrimAction({"enable_trim": True})
    with patch("dasik.lib.actions.trim_action.subprocess.run", _run(stdout=b"enabled\n")):
        assert a.is_needed() is False


def test_verify_reflects_timer_state():
    a = TrimAction({"enable_trim": True})
    with patch("dasik.lib.actions.trim_action.subprocess.run", _run(stdout=b"enabled\n")):
        assert a.verify() is True
    with patch("dasik.lib.actions.trim_action.subprocess.run", _run(stdout=b"disabled\n")):
        assert a.verify() is False


def test_detects_encryption_and_luks_name_from_disks():
    cfg = {
        "enable_trim": True,
        "disks": {"disks": [{"partitions": [{"encrypt": True, "luks_name": "myroot"}]}]},
    }
    a = TrimAction(cfg)
    assert a.has_encryption is True
    assert a.dm_name == "myroot"


def test_encryption_defaults_to_cryptroot_when_unencrypted():
    a = TrimAction({"enable_trim": True})
    assert a.has_encryption is False
    assert a.dm_name == "cryptroot"


def test_name_and_optional():
    a = TrimAction({"enable_trim": True})
    assert a.name == "Enable TRIM"
    assert a.is_optional is True

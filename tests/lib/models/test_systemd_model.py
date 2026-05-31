import pytest

from dasik.lib.models.systemd_model import SystemdModel


def test_defaults_are_empty_lists():
    m = SystemdModel()
    assert m.enable_units == []
    assert m.enable_sockets == []
    assert m.disable_units == []


def test_accepts_disjoint_enable_and_disable():
    m = SystemdModel(
        enable_units=["sshd.service"],
        enable_sockets=["cups.socket"],
        disable_units=["bluetooth.service"],
    )
    assert m.disable_units == ["bluetooth.service"]


def test_rejects_unit_in_both_enable_and_disable():
    with pytest.raises(ValueError):
        SystemdModel(
            enable_units=["sshd.service"],
            disable_units=["sshd.service"],
        )


def test_rejects_socket_in_both_enable_and_disable():
    with pytest.raises(ValueError):
        SystemdModel(
            enable_sockets=["cups.socket"],
            disable_units=["cups.socket"],
        )

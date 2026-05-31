from unittest.mock import mock_open, patch

from dasik.lib.actions.network_action import NetworkAction


def _cfg(hostname="arch", add_hosts=True, ntype="NetworkManager"):
    return {
        "hostname": hostname,
        "network": {"type": ntype, "add_default_hosts": add_hosts},
    }


def test_reads_root_hostname_and_network_section():
    a = NetworkAction(_cfg(hostname="box", ntype="systemd-networkd"))
    assert a.hostname == "box"
    assert a.type == "systemd-networkd"
    assert a.add_default_hosts is True


def test_needed_when_hostname_file_absent():
    a = NetworkAction(_cfg())
    with patch("dasik.lib.actions.network_action.os.path.exists", return_value=False):
        assert a.is_needed() is True


def test_needed_when_hostname_differs():
    a = NetworkAction(_cfg(hostname="arch"))
    with patch("dasik.lib.actions.network_action.os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data="oldname\n")):
        assert a._hostname_needs_write() is True
        assert a.is_needed() is True


def test_needed_when_default_hosts_missing():
    a = NetworkAction(_cfg(hostname="arch", add_hosts=True))

    def opener(path, *a_, **k):
        data = "arch\n" if "hostname" in str(path) else "# empty hosts\n"
        return mock_open(read_data=data)()

    with patch("dasik.lib.actions.network_action.os.path.exists", return_value=True), \
         patch("builtins.open", side_effect=opener):
        assert a._hostname_needs_write() is False
        assert a._hosts_needs_write() is True
        assert a.is_needed() is True


def test_not_needed_when_hostname_and_hosts_ok():
    a = NetworkAction(_cfg(hostname="arch", add_hosts=True))
    hosts = "127.0.0.1 localhost\n::1 localhost\n127.0.1.1 arch\n"

    def opener(path, *a_, **k):
        data = "arch\n" if "hostname" in str(path) else hosts
        return mock_open(read_data=data)()

    with patch("dasik.lib.actions.network_action.os.path.exists", return_value=True), \
         patch("builtins.open", side_effect=opener):
        assert a.is_needed() is False
        assert a.verify() is True


def test_hosts_check_skipped_when_add_default_hosts_false():
    a = NetworkAction(_cfg(add_hosts=False))
    assert a._hosts_needs_write() is False


def test_name_and_optional():
    a = NetworkAction(_cfg())
    assert a.name == "Network Configuration"
    assert a.is_optional is True

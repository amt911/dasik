"""Preflight: stop a tunnel that cannot work before anything is written.

All three are cheap to get wrong and expensive to meet on a machine whose disk
is already partitioned: two tunnels fighting over one interface name, a file in
a format its declared backend cannot read, and a NetworkManager keyfile left on
a machine that does not run NetworkManager.
"""
from dasik.lib.validation.preflight import preflight, has_errors

WGQ = "[Interface]\nAddress = 10.0.0.2/24\nPrivateKey = S\n"
NMC = "[connection]\nid=work\ntype=wireguard\n\n[wireguard]\nprivate-key=S\n"


def _cfg(*tunnels, **root):
    config = {"hostname": "box", "wireguard": list(tunnels)}
    config.update(root)
    return config


def _t(name="eu-mad", content=WGQ, **kw):
    tunnel = {"name": name, "source": f"wg/{name}.conf", "content": content}
    tunnel.update(kw)
    return tunnel


def _text(issues):
    return "\n".join(str(i) for i in issues)


def test_a_valid_tunnel_raises_nothing():
    assert not has_errors(preflight(_cfg(_t())))


def test_no_block_raises_nothing():
    assert not has_errors(preflight({"hostname": "box"}))


def test_two_tunnels_with_the_same_name_is_an_error():
    issues = preflight(_cfg(_t(), _t()))
    assert has_errors(issues) and "eu-mad" in _text(issues)


def test_a_backend_that_contradicts_the_file_is_an_error_naming_the_import():
    issues = preflight(_cfg(_t(backend="networkmanager")))
    assert has_errors(issues)
    assert "nmcli connection import" in _text(issues)


def test_a_file_in_neither_format_is_an_error():
    assert has_errors(preflight(_cfg(_t(content="hello\n"))))


def test_an_nm_keyfile_on_a_networkd_machine_warns_but_does_not_stop():
    issues = preflight(_cfg(_t(name="work", content=NMC),
                            network={"type": "systemd-networkd"}))
    assert issues and not has_errors(issues)
    assert "work" in _text(issues)


def test_a_wg_quick_tunnel_on_a_networkd_machine_is_fine():
    # wg-quick is independent of the network manager — that is the whole
    # reason it is the portable backend.
    issues = preflight(_cfg(_t(), network={"type": "systemd-networkd"}))
    assert not has_errors(issues)


def test_an_nm_keyfile_on_a_networkmanager_machine_is_fine():
    issues = preflight(_cfg(_t(name="work", content=NMC),
                            network={"type": "NetworkManager"}))
    assert not has_errors(issues)

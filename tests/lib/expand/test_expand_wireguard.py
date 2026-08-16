"""The tunnel toggle: one file per tunnel, at 0600, for the backend that reads it.

The mode assertions are not style. `DropFilesAction._write_content` falls back
to a plain ``open(path, "w")`` when no mode is declared — 0644 — and the body
of a tunnel file is an interface's private key. wg-quick warns and carries on,
NetworkManager ignores the file in silence, so nothing ever failed loudly.
"""
import pytest

from dasik.lib.expand.toggles import expand_wireguard, resolve_backend

WGQ = ("[Interface]\nAddress = 10.0.0.2/24\nPrivateKey = SECRET\n\n"
       "[Peer]\nPublicKey = PUB\nEndpoint = vpn.example:51820\n"
       "AllowedIPs = 0.0.0.0/0\n")
NMC = ("[connection]\nid=work\ntype=wireguard\ninterface-name=work\n\n"
       "[wireguard]\nprivate-key=SECRET\n")


def _cfg(*tunnels):
    return {"hostname": "box", "wireguard": list(tunnels)}


def _t(name="eu-mad", content=WGQ, **kw):
    tunnel = {"name": name, "source": f"wg/{name}.conf", "content": content}
    tunnel.update(kw)
    return tunnel


def test_wg_quick_contributes_package_unit_and_a_0600_file():
    out = expand_wireguard(_cfg(_t()))
    assert out["packages"] == ["wireguard-tools"]
    assert out["units"] == ["wg-quick@eu-mad.service"]
    assert out["files"] == [{"path": "/etc/wireguard/eu-mad.conf",
                             "content": WGQ, "mode": "0600"}]


def test_the_mode_is_declared_for_both_backends_because_the_body_is_a_key():
    for tunnel in (_t(), _t(name="work", content=NMC)):
        files = expand_wireguard(_cfg(tunnel))["files"]
        assert all(f["mode"] == "0600" for f in files)


def test_networkmanager_writes_the_keyfile_and_needs_no_unit():
    out = expand_wireguard(_cfg(_t(name="work", content=NMC)))
    assert out["files"] == [{
        "path": "/etc/NetworkManager/system-connections/work.nmconnection",
        "content": NMC, "mode": "0600"}]
    assert out["packages"] == ["networkmanager"]
    assert out.get("units", []) == []


def test_enable_false_places_the_file_but_starts_nothing():
    out = expand_wireguard(_cfg(_t(enable=False)))
    assert out["files"][0]["path"] == "/etc/wireguard/eu-mad.conf"
    assert out.get("units", []) == []


def test_two_tunnels_two_files_one_of_each_backend():
    out = expand_wireguard(_cfg(_t(), _t(name="work", content=NMC)))
    assert [f["path"] for f in out["files"]] == [
        "/etc/wireguard/eu-mad.conf",
        "/etc/NetworkManager/system-connections/work.nmconnection"]
    assert out["units"] == ["wg-quick@eu-mad.service"]


def test_no_block_contributes_nothing():
    assert expand_wireguard({"hostname": "box"}) == {}


def test_a_tunnel_the_loader_never_filled_contributes_nothing():
    assert expand_wireguard(_cfg({"name": "eu-mad", "source": "wg/x.conf"})) == {}


def test_resolve_backend_reads_the_format_not_the_config():
    assert resolve_backend(WGQ, "auto", "x") == "wg-quick"
    assert resolve_backend(NMC, "auto", "x") == "networkmanager"


def test_resolve_backend_tolerates_spaces_around_the_type():
    assert resolve_backend("[connection]\ntype = wireguard\n",
                           "auto", "x") == "networkmanager"


def test_an_explicit_backend_that_contradicts_the_file_is_an_error():
    with pytest.raises(ValueError) as e:
        resolve_backend(WGQ, "networkmanager", "work")
    assert "nmcli connection import" in str(e.value)


def test_the_same_refusal_the_other_way_round():
    with pytest.raises(ValueError):
        resolve_backend(NMC, "wg-quick", "work")


def test_an_explicit_backend_that_agrees_is_accepted():
    assert resolve_backend(NMC, "networkmanager", "work") == "networkmanager"


def test_a_non_wireguard_nm_keyfile_is_not_a_tunnel():
    with pytest.raises(ValueError):
        resolve_backend("[connection]\nid=wifi\ntype=wifi\n", "auto", "x")


def test_a_file_in_neither_format_is_an_error():
    with pytest.raises(ValueError):
        resolve_backend("hello\n", "auto", "x")

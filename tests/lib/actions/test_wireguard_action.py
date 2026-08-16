"""Capture: the tunnels a machine holds, as the block that declares them.

The double-capture assertion is the one with history. Discovery used to live in
DropFilesAction, which reported the conf as a `files` entry with mode 0600
while the toggle contributed the same path *without* a mode — two unequal
dicts, so `subtract_contributions` stripped nothing and a sync wrote the same
private key into the config twice. The orphan `files` entry then kept the
tunnel alive after the block was turned off.
"""
import os

from dasik.lib.actions.wireguard_action import WireguardAction

WGQ = "[Interface]\nAddress = 10.0.0.2/24\nPrivateKey = SECRET\n"
NMC = "[connection]\nid=work\ntype=wireguard\n\n[wireguard]\nprivate-key=S\n"


class _Target:
    def __init__(self, root):
        self.root = str(root)

    def path(self, canonical):
        return os.path.join(self.root, canonical.lstrip("/"))


class _Ctx:
    def __init__(self, target):
        self.target = target


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _action(root, config=None, enabled=()):
    action = WireguardAction(config if config is not None else {}, _Ctx(_Target(root)))
    action._unit_enabled = lambda unit: unit in enabled     # no systemctl here
    return action


def test_plan_is_empty_capture_only(tmp_path):
    assert _action(tmp_path).plan(managed=[]) == []


def test_it_owns_no_manifest_domain(tmp_path):
    assert _action(tmp_path).managed_keys() == {}


def test_a_wg_quick_conf_comes_back_as_a_tunnel(tmp_path):
    _write(tmp_path, "etc/wireguard/eu-mad.conf", WGQ)
    out = _action(tmp_path, enabled=("wg-quick@eu-mad.service",)).import_state()
    assert out["wireguard"] == [{"name": "eu-mad", "source": "wg/eu-mad.conf",
                                 "backend": "wg-quick", "enable": True,
                                 "content": WGQ}]


def test_a_disabled_unit_captures_enable_false(tmp_path):
    _write(tmp_path, "etc/wireguard/eu-mad.conf", WGQ)
    assert _action(tmp_path).import_state()["wireguard"][0]["enable"] is False


def test_an_nm_keyfile_comes_back_as_a_networkmanager_tunnel(tmp_path):
    _write(tmp_path, "etc/NetworkManager/system-connections/work.nmconnection", NMC)
    out = _action(tmp_path).import_state()
    assert out["wireguard"] == [{"name": "work", "source": "wg/work.nmconnection",
                                 "backend": "networkmanager", "enable": True,
                                 "content": NMC}]


def test_a_non_wireguard_nm_connection_is_ignored(tmp_path):
    _write(tmp_path, "etc/NetworkManager/system-connections/wifi.nmconnection",
           "[connection]\nid=wifi\ntype=wifi\n")
    assert _action(tmp_path).import_state() == {}


def test_a_symlinked_conf_is_skipped(tmp_path):
    real = _write(tmp_path, "elsewhere.conf", WGQ)
    (tmp_path / "etc" / "wireguard").mkdir(parents=True)
    (tmp_path / "etc" / "wireguard" / "eu-mad.conf").symlink_to(real)
    assert _action(tmp_path).import_state() == {}


def test_a_file_that_is_not_a_conf_is_ignored(tmp_path):
    _write(tmp_path, "etc/wireguard/README", WGQ)
    assert _action(tmp_path).import_state() == {}


def test_a_machine_with_no_tunnel_invents_nothing(tmp_path):
    assert _action(tmp_path).import_state() == {}


def test_a_declared_source_path_is_kept_instead_of_the_default(tmp_path):
    _write(tmp_path, "etc/wireguard/eu-mad.conf", WGQ)
    declared = {"wireguard": [{"name": "eu-mad", "source": "tunnels/mad.conf"}]}
    out = _action(tmp_path, declared).import_state()
    assert out["wireguard"][0]["source"] == "tunnels/mad.conf"


def test_both_backends_on_one_machine(tmp_path):
    _write(tmp_path, "etc/wireguard/eu-mad.conf", WGQ)
    _write(tmp_path, "etc/NetworkManager/system-connections/work.nmconnection", NMC)
    out = _action(tmp_path, enabled=("wg-quick@eu-mad.service",)).import_state()
    assert [(t["name"], t["backend"]) for t in out["wireguard"]] == [
        ("eu-mad", "wg-quick"), ("work", "networkmanager")]

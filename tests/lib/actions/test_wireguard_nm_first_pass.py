"""A tunnel NetworkManager has to serve must survive a one-pass install.

Found by driving the ThinkPad's real config in a VM. `apply` finished rc=0, the
machine booted, and the ProtonVPN connection was not there — no error, no
warning, nothing in the plan. The tunnel only appeared when a SECOND `apply`
was run.

The keyfile is built by asking the TARGET's `nmcli --offline connection add`,
and `plan` runs before the packages do: on a fresh install `networkmanager` is
not installed yet, nmcli cannot be reached, `_desired_keyfile` returns "" — and
the action skipped the tunnel, silently, "rather than plan one it could not
carry out". But it CAN carry it out: `apply` runs long after PackagesAction, by
which time nmcli exists.

So the plan is made from the fact that IS knowable before the transaction —
the keyfile is not on the machine — and the content comparison is kept for when
nmcli can answer. An apply that then cannot build it fails loudly; what must
never happen again is a config that declares a VPN, an apply that reports
success, and a machine with no VPN.
"""
import os

from dasik.lib.actions.wireguard_action import WireguardAction

CONF = ("[Interface]\nPrivateKey = SECRET\nAddress = 10.2.0.2/32\n\n"
        "[Peer]\nPublicKey = PUB\nAllowedIPs = 0.0.0.0/0\nEndpoint = 1.2.3.4:51820\n")
KEYFILE = "[connection]\nid=vpn\ntype=wireguard\n"
_NM_DIR = "/etc/NetworkManager/system-connections"


class _Target:
    def __init__(self, root):
        self.root = str(root)

    def path(self, canonical):
        return os.path.join(self.root, canonical.lstrip("/"))


class _Ctx:
    def __init__(self, target):
        self.target = target


def _action(root, nmcli_output, keyfile=None):
    """A tunnel declared for the NM backend, on a machine that may lack nmcli.

    `nmcli_output=""` is the fresh install: nmcli is not there yet.
    """
    config = {"wireguard": [{"name": "vpn", "content": CONF,
                             "backend": "networkmanager", "enable": True}]}
    action = WireguardAction(config, _Ctx(_Target(root)))
    action._desired_keyfile = lambda name, conf, auto: nmcli_output
    if keyfile is not None:
        path = root / _NM_DIR.lstrip("/") / "vpn.nmconnection"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(keyfile)
    return action


def _items(action, managed=()):
    return [(c.op.name, c.item) for c in action.plan(managed=list(managed))]


def test_a_declared_tunnel_missing_from_the_machine_is_planned(tmp_path):
    """Even with no nmcli to build it yet: this is the fresh-install case."""
    assert _items(_action(tmp_path, nmcli_output="")) == [("MODIFY", "vpn")]


def test_the_reason_says_the_keyfile_is_missing(tmp_path):
    change = _action(tmp_path, nmcli_output="").plan(managed=[])[0]
    assert "keyfile" in change.reason


def test_a_tunnel_already_on_the_machine_is_not_churned(tmp_path):
    """nmcli cannot answer, so there is nothing to compare against — and a
    keyfile that is already there is not evidence of drift."""
    action = _action(tmp_path, nmcli_output="", keyfile=KEYFILE)
    assert _items(action) == []


def test_a_matching_keyfile_plans_nothing(tmp_path):
    action = _action(tmp_path, nmcli_output=KEYFILE, keyfile=KEYFILE)
    assert _items(action) == []


def test_a_drifted_keyfile_is_planned(tmp_path):
    action = _action(tmp_path, nmcli_output=KEYFILE, keyfile="[connection]\nid=old\n")
    assert _items(action) == [("MODIFY", "vpn")]


def test_apply_then_plan_is_silent(tmp_path):
    """The round trip the VM broke: plan -> apply -> plan must end quiet."""
    action = _action(tmp_path, nmcli_output=KEYFILE)
    changes = action.plan(managed=[])
    assert changes

    action.apply(changes)

    written = tmp_path / _NM_DIR.lstrip("/") / "vpn.nmconnection"
    assert written.read_text() == KEYFILE
    assert oct(written.stat().st_mode)[-3:] == "600", "it holds a private key"
    assert _items(action) == []


def test_a_wg_quick_tunnel_is_still_none_of_this_action_s_business(tmp_path):
    """Only the NM conversion is applied here; wg-quick confs ride `files`."""
    config = {"wireguard": [{"name": "vpn", "content": CONF,
                             "backend": "wg-quick", "enable": True}]}
    action = WireguardAction(config, _Ctx(_Target(tmp_path)))
    assert action.plan(managed=[]) == []

import pytest

from dasik.lib.actions.network_action import NetworkAction
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Op
from dasik.lib.exceptions.exceptions import NetworkTypeNotFoundException

_BLOCK = "127.0.0.1 localhost\n::1 localhost\n127.0.1.1 arch\n"


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


def _cfg(hostname="arch", add_hosts=True, ntype="NetworkManager"):
    return {"hostname": hostname, "network": {"type": ntype, "add_default_hosts": add_hosts}}


def _write(tmp_path, hostname=None, hosts=None):
    etc = tmp_path / "etc"
    etc.mkdir(parents=True, exist_ok=True)
    if hostname is not None:
        (etc / "hostname").write_text(hostname)
    if hosts is not None:
        (etc / "hosts").write_text(hosts)


def test_is_v3_true():
    assert NetworkAction.is_v3() is True


def test_reads_root_hostname_and_network_section():
    a = NetworkAction(_cfg(hostname="box", ntype="systemd-networkd"))
    assert a.hostname == "box" and a.type == "systemd-networkd" and a.add_default_hosts is True


def test_desired_state_excludes_type():
    a = NetworkAction(_cfg(hostname="arch", add_hosts=True))
    assert a._desired_state() == {"hostname": "arch", "default_hosts": True}


def test_actual_state_none_when_hostname_missing(tmp_path):
    a = NetworkAction(_cfg(), _ctx(tmp_path))  # no /etc/hostname
    assert a._actual_state() is None


def test_actual_state_reads_hostname_and_block(tmp_path):
    _write(tmp_path, hostname="arch\n", hosts=_BLOCK)
    a = NetworkAction(_cfg(hostname="arch"), _ctx(tmp_path))
    assert a._actual_state() == {"hostname": "arch", "default_hosts": True}


def test_plan_empty_when_converged(tmp_path):
    _write(tmp_path, hostname="arch\n", hosts=_BLOCK)
    a = NetworkAction(_cfg(hostname="arch", add_hosts=True), _ctx(tmp_path))
    assert a.plan(managed=[]) == []


def test_plan_modify_when_hostname_differs(tmp_path):
    _write(tmp_path, hostname="oldname\n", hosts=_BLOCK)
    a = NetworkAction(_cfg(hostname="arch"), _ctx(tmp_path))
    changes = a.plan(managed=[])
    assert changes and changes[0].op is Op.MODIFY and "hostname" in changes[0].item


def test_plan_modify_when_default_hosts_absent(tmp_path):
    _write(tmp_path, hostname="arch\n", hosts="# empty\n")
    a = NetworkAction(_cfg(hostname="arch", add_hosts=True), _ctx(tmp_path))
    changes = a.plan(managed=[])
    assert changes and changes[0].op is Op.MODIFY and "default_hosts" in changes[0].item


def test_import_fragment_two_keys_with_type_passthrough(tmp_path):
    _write(tmp_path, hostname="arch\n", hosts=_BLOCK)
    a = NetworkAction(_cfg(hostname="arch", ntype="systemd-networkd"), _ctx(tmp_path))
    frag = a.import_state(managed=[])
    assert frag == {
        "hostname": "arch",
        "network": {"type": "systemd-networkd", "add_default_hosts": True},
    }


def test_nothing_declared_guard_empty_plan(tmp_path):
    a = NetworkAction({"packages": ["git"]}, _ctx(tmp_path))  # no hostname
    assert a.hostname == ""
    assert a.plan(managed=[]) == []
    assert a.import_state(managed=[]) == {}


def test_nothing_declared_guard_set_value_noop_no_raise(tmp_path):
    a = NetworkAction({"packages": ["git"]}, _ctx(tmp_path))  # type == "" would raise
    a._set_value()  # must NOT raise NetworkTypeNotFoundException
    assert not (tmp_path / "etc" / "hostname").exists()


def test_set_value_writes_hostname_and_block(tmp_path):
    _write(tmp_path, hosts="192.168.0.1 router\n")
    a = NetworkAction(_cfg(hostname="arch", add_hosts=True), _ctx(tmp_path))
    a._set_value()
    assert (tmp_path / "etc" / "hostname").read_text() == "arch"
    hosts_text = (tmp_path / "etc" / "hosts").read_text()
    assert "127.0.1.1 arch" in hosts_text and "192.168.0.1 router" in hosts_text


def test_set_value_idempotent(tmp_path):
    _write(tmp_path, hosts="")
    a = NetworkAction(_cfg(hostname="arch", add_hosts=True), _ctx(tmp_path))
    a._set_value()
    a._set_value()
    assert a.plan(managed=[]) == []


def test_invalid_type_raises_on_set_value(tmp_path):
    _write(tmp_path, hosts="")
    a = NetworkAction(_cfg(hostname="arch", ntype="bogus"), _ctx(tmp_path))
    with pytest.raises(NetworkTypeNotFoundException):
        a._set_value()


def test_hostname_without_network_type_writes_hostname_no_raise(tmp_path):
    """A minimal config that sets a hostname but declares no `network` section
    (type == "") must WRITE the hostname, not raise — the module docstring
    promises minimal configs "do not ... raise on an absent type". Only a
    non-empty, unrecognised type (a typo) should raise.

    Regression: found by the QEMU install harness — `dasik apply` on a
    hostname-only config pacstrapped a full base system, then aborted at the
    network step with "Network type not recognized.", blocking initramfs +
    bootloader.
    """
    _write(tmp_path, hosts="")
    a = NetworkAction({"hostname": "dasik-vm"}, _ctx(tmp_path))  # no network section
    assert a.type == ""
    a._set_value()  # must NOT raise NetworkTypeNotFoundException
    assert (tmp_path / "etc" / "hostname").read_text() == "dasik-vm"


def test_name_and_optional():
    a = NetworkAction(_cfg())
    assert a.name == "Network Configuration"
    assert a.is_optional is True


# --- hostname alone is enough (F-31) --------------------------------------- #
#
# The registry required BOTH 'network' and 'hostname', so a config that declared
# only a hostname was skipped entirely and /etc/hostname was never written — the
# installed system kept the ISO's name with no error anywhere.

def test_registered_requiring_only_hostname():
    from dasik.lib.actions.action_registry import get_default_registry
    from dasik.lib.actions.actions_handler_v2 import setup_actions
    setup_actions()
    meta = next(m for m in get_default_registry().get_all_actions()
                if m["class"].__name__ == "NetworkAction")
    assert meta["required_fields"] == ["hostname"]


def test_hostname_without_network_section_still_plans(tmp_path):
    from dasik.lib.actions.action_context import ActionContext
    from dasik.lib.target.target import Target
    (tmp_path / "etc").mkdir()
    a = NetworkAction({"hostname": "archlinux-torre-amd"},
                      ActionContext(target=Target(root=str(tmp_path))))
    assert a.plan(managed=[]), "a declared hostname must be applied on its own"
    a.apply(a.plan(managed=[]))
    assert (tmp_path / "etc" / "hostname").read_text().strip() == "archlinux-torre-amd"
    assert a.plan(managed=["network"]) == []          # converged -> no-op

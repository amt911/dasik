"""The libvirt `default` NAT network, and whether it autostarts.

libvirt ships ``/etc/libvirt/qemu/networks/default.xml`` but NOT the symlink in
``autostart/`` that makes the network come up with the daemon. Without it the
first guest fails with ``Requested operation is not valid: network 'default' is
not active`` — the Arch wiki has a troubleshooting entry for exactly this. On
the machines this repo installs the symlink had always been made by hand, so a
reinstall from a captured config produced a host whose VMs could not reach the
network.

The symlink IS the mechanism: ``virsh net-autostart default`` writes nothing
else, which is what makes the domain applicable from a chroot where no libvirtd
is running.
"""
import os
from unittest.mock import MagicMock

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.libvirt_network_action import (
    AUTOSTART_DIR, NETWORKS_DIR, LibvirtNetworkAction,
)
from dasik.lib.expand.toggles import expand_kvm
from dasik.lib.models.json_model import JsonModel
from dasik.lib.target.target import Target


@pytest.fixture(autouse=True)
def _quiet_run_logger(monkeypatch):
    """The warn paths here would otherwise reach the process-wide run logger,
    whose stream a previous test has already closed — a failure that only
    surfaces under a full-suite (or mutmut) run, never on the file alone."""
    monkeypatch.setattr(
        "dasik.lib.actions.libvirt_network_action.run_logger.get",
        lambda: MagicMock())


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


def _networks(root, *, defined=True):
    """A scratch root with libvirt's shipped network definition (or without)."""
    (root / NETWORKS_DIR.lstrip("/")).mkdir(parents=True, exist_ok=True)
    (root / AUTOSTART_DIR.lstrip("/")).mkdir(parents=True, exist_ok=True)
    if defined:
        (root / NETWORKS_DIR.lstrip("/") / "default.xml").write_text(
            "<network><name>default</name></network>\n")


def _autostart(root):
    return root / AUTOSTART_DIR.lstrip("/") / "default.xml"


def _link(root):
    """The symlink `virsh net-autostart default` would have made — ABSOLUTE,
    the way libvirt writes it."""
    _autostart(root).symlink_to(f"{NETWORKS_DIR}/default.xml")


def _plan(root, config, managed=()):
    action = LibvirtNetworkAction(config, _ctx(root))
    return [(c.op.name, c.item) for c in action.plan(managed=list(managed))]


# --- the model ------------------------------------------------------------- #

def test_default_network_is_off_unless_declared():
    assert JsonModel(**{"kvm": {}}).kvm.default_network is False


def test_default_network_is_orthogonal_to_install():
    """The two machines this repo installs carry libvirt as literal packages
    captured by `sync`; turning the toggle on would move all 13 of them into a
    derived contribution and rewrite the capture. Autostart must be declarable
    on its own."""
    model = JsonModel(**{"kvm": {"default_network": True}})

    assert model.kvm.default_network is True
    assert model.kvm.install is False
    assert expand_kvm({"kvm": {"default_network": True}}) == {}


# --- actual() -------------------------------------------------------------- #

def test_the_autostart_symlink_is_seen(tmp_path):
    _networks(tmp_path)
    _link(tmp_path)

    assert LibvirtNetworkAction({}, _ctx(tmp_path)).actual() == {"default"}


def test_no_symlink_reads_as_no_autostart(tmp_path):
    _networks(tmp_path)

    assert LibvirtNetworkAction({}, _ctx(tmp_path)).actual() == set()


def test_an_absolute_symlink_is_not_resolved_against_the_installer_host(tmp_path):
    """The trap this domain is built around.

    libvirt writes an ABSOLUTE symlink. Inside /mnt it points at
    `/etc/libvirt/...`, which during an install resolves against the LIVE ISO,
    not the target — so `os.path.exists` answers about the wrong machine.
    Here the link dangles inside the scratch root, and the domain must still
    read it as present.
    """
    _networks(tmp_path, defined=False)
    _autostart(tmp_path).symlink_to("/nonexistent/default.xml")

    assert not os.path.exists(_autostart(tmp_path))       # the trap, made explicit
    assert LibvirtNetworkAction({}, _ctx(tmp_path)).actual() == {"default"}


# --- plan: all four directions --------------------------------------------- #

def test_declared_and_missing_is_planned(tmp_path):
    _networks(tmp_path)

    assert _plan(tmp_path, {"kvm": {"default_network": True}}) == [
        ("INSTALL", "default")]


def test_declared_and_present_plans_nothing(tmp_path):
    _networks(tmp_path)
    _link(tmp_path)

    assert _plan(tmp_path, {"kvm": {"default_network": True}}) == []


def test_dropping_the_flag_removes_the_autostart_dasik_owns(tmp_path):
    _networks(tmp_path)
    _link(tmp_path)

    assert _plan(tmp_path, {"kvm": {}}, managed=["default"]) == [("REMOVE", "default")]


def test_an_autostart_dasik_never_made_is_left_alone(tmp_path):
    """Somebody's hand-made symlink is drift, not dasik's to delete."""
    _networks(tmp_path)
    _link(tmp_path)

    assert _plan(tmp_path, {"kvm": {}}) == []


def test_the_domain_is_quiet_with_the_kvm_block_removed_entirely(tmp_path):
    """The reconciler hands an action its EMPTY config when a previous
    generation owned the domain; an empty config is not the empty value."""
    _networks(tmp_path)

    assert _plan(tmp_path, {}) == []


# --- apply ------------------------------------------------------------------ #

def test_apply_writes_the_symlink_libvirt_reads(tmp_path):
    _networks(tmp_path)
    action = LibvirtNetworkAction({"kvm": {"default_network": True}}, _ctx(tmp_path))

    action.apply(action.plan(managed=[]))

    link = _autostart(tmp_path)
    assert link.is_symlink()
    assert os.readlink(link) == f"{NETWORKS_DIR}/default.xml"


def test_apply_creates_the_autostart_directory_when_libvirt_has_not(tmp_path):
    _networks(tmp_path)
    (tmp_path / AUTOSTART_DIR.lstrip("/")).rmdir()
    action = LibvirtNetworkAction({"kvm": {"default_network": True}}, _ctx(tmp_path))

    action.apply(action.plan(managed=[]))

    assert _autostart(tmp_path).is_symlink()


def test_apply_then_plan_is_silent(tmp_path):
    _networks(tmp_path)
    action = LibvirtNetworkAction({"kvm": {"default_network": True}}, _ctx(tmp_path))

    action.apply(action.plan(managed=[]))

    assert action.plan(managed=["default"]) == []


def test_no_dangling_link_when_the_network_is_undefined(tmp_path):
    """Someone ran `virsh net-undefine default`. Pointing autostart at a file
    that is not there would make libvirtd log an error every start; recreating
    the network is not this domain's business either."""
    _networks(tmp_path, defined=False)
    action = LibvirtNetworkAction({"kvm": {"default_network": True}}, _ctx(tmp_path))

    action.apply(action.plan(managed=[]))

    assert not _autostart(tmp_path).is_symlink()


def test_removal_takes_the_symlink_and_leaves_the_network_defined(tmp_path):
    """REMOVE un-autostarts. It never `net-undefine`s — dasik does not destroy
    a network it only ever linked."""
    _networks(tmp_path)
    _link(tmp_path)
    action = LibvirtNetworkAction({"kvm": {}}, _ctx(tmp_path))

    action.apply(action.plan(managed=["default"]))

    assert not _autostart(tmp_path).is_symlink()
    assert (tmp_path / NETWORKS_DIR.lstrip("/") / "default.xml").exists()


# --- sync ------------------------------------------------------------------- #

def test_sync_captures_the_autostart_as_its_own_flag(tmp_path):
    _networks(tmp_path)
    _link(tmp_path)

    captured = LibvirtNetworkAction({}, _ctx(tmp_path)).import_state()

    assert captured == {"kvm": {"default_network": True}}


def test_sync_invents_nothing_on_a_machine_without_it(tmp_path):
    _networks(tmp_path)

    assert LibvirtNetworkAction({}, _ctx(tmp_path)).import_state() == {}


def test_sync_clears_a_declared_flag_the_machine_does_not_have(tmp_path):
    """`sync` reports reality. Silence would leave the stale declaration
    standing, because ConfigWriter.merge only overwrites a key, never drops
    one."""
    _networks(tmp_path)
    action = LibvirtNetworkAction({"kvm": {"default_network": True}}, _ctx(tmp_path))

    assert action.import_state() == {"kvm": {"default_network": False}}


def test_sync_never_speaks_for_the_install_toggle(tmp_path):
    """Emitting `install: true` would hand the 13 KVM packages to the toggle
    and subtract them from the captured package list."""
    _networks(tmp_path)
    _link(tmp_path)

    captured = LibvirtNetworkAction({}, _ctx(tmp_path)).import_state()

    assert "install" not in captured["kvm"]


def test_the_captured_flag_re_plans_to_nothing(tmp_path):
    """sync → check → plan must end in silence."""
    _networks(tmp_path)
    _link(tmp_path)
    captured = LibvirtNetworkAction({}, _ctx(tmp_path)).import_state()

    JsonModel(**captured)                                  # `dasik check`

    assert _plan(tmp_path, captured, managed=["default"]) == []


# --- manifest ownership ----------------------------------------------------- #

def test_the_domain_is_owned_only_while_declared(tmp_path):
    """Memory of issue #238: an action that does not report its keys is
    dispossessed on the next sync, and turning the block off then removes
    nothing."""
    _networks(tmp_path)
    on = LibvirtNetworkAction({"kvm": {"default_network": True}}, _ctx(tmp_path))
    off = LibvirtNetworkAction({"kvm": {}}, _ctx(tmp_path))

    assert on.managed_keys() == {"libvirt_networks": ["default"]}
    assert off.managed_keys() == {"libvirt_networks": []}


# --- the package list ------------------------------------------------------- #

@pytest.mark.parametrize("package", ["qemu-guest-agent", "qemu-block-iscsi"])
def test_the_kvm_toggle_no_longer_names_redundant_packages(package):
    """qemu-guest-agent belongs INSIDE a guest; qemu-block-iscsi is already a
    hard dependency of qemu-full."""
    assert package not in expand_kvm({"kvm": {"install": True}})["packages"]


@pytest.mark.parametrize("package", ["samba", "qemu-user-static"])
def test_the_kvm_toggle_keeps_what_qemu_full_only_suggests(package):
    """Both are OPTIONAL dependencies of qemu-full, so pacman does not pull
    them: dropping them from the list would uninstall them. samba backs
    qemu's SMB sharing, qemu-user-static the foreign-architecture emulation."""
    assert package in expand_kvm({"kvm": {"install": True}})["packages"]

"""firewalld has more than one zone, and dasik only ever saw `public`.

A machine can carry a customised `home` (or `work`, or `internal`) zone — the
one that found this had `home` allowing ssh, mdns, samba, samba-client and
samba-dc — and `sync` reported none of it. Capture the machine, re-apply the
capture, and every zone but `public` is silently gone: the same one-way street
the project's own rules warn about.

An **extra** zone's `allowed_services` is its complete service list, not a diff.
`remove_services` exists only because firewalld's upstream `public` allows
`ssh` and `dhcpv6-client` out of the box; naming a zone explicitly is already
the whole statement, so there is nothing to subtract and the schema refuses the
field there rather than let it mean something different per zone.
"""
import os
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.firewall_action import FirewallAction
from dasik.lib.models.firewall_model import FirewallModel
from dasik.lib.target.target import Target


def _action(cfg, root):
    return FirewallAction(cfg, ActionContext(target=Target(root=str(root))))


def _zone_file(root, zone):
    return os.path.join(str(root), "etc/firewalld/zones", f"{zone}.xml")


HOME = {"enable": True, "zones": {"home": {"allowed_services": ["ssh", "samba"]}}}


# --------------------------------------------------------------------------- #
#  the schema
# --------------------------------------------------------------------------- #

def test_an_extra_zone_is_accepted():
    model = FirewallModel(**HOME)

    assert model.zones["home"].allowed_services == ["ssh", "samba"]


def test_remove_services_is_refused_inside_a_zone():
    with pytest.raises(ValueError, match="remove_services"):
        FirewallModel(enable=True, zones={"home": {"allowed_services": ["ssh"],
                                                   "remove_services": ["dhcpv6-client"]}})


def test_public_is_refused_as_an_extra_zone():
    """The top-level fields already are the public zone; two ways to say it
    would let a config contradict itself."""
    with pytest.raises(ValueError, match="public"):
        FirewallModel(enable=True, zones={"public": {"allowed_services": ["samba"]}})


def test_zones_are_refused_under_ufw():
    with pytest.raises(ValueError, match="zones"):
        FirewallModel(enable=True, backend="ufw",
                      zones={"home": {"allowed_services": ["ssh"]}})


# --------------------------------------------------------------------------- #
#  plan / apply
# --------------------------------------------------------------------------- #

def test_a_missing_zone_file_is_planned(tmp_path):
    planned = [(c.op.name, c.item) for c in _action(HOME, tmp_path).plan(managed=[])]

    assert ("MODIFY", "home") in planned


def test_apply_writes_the_zone_file(tmp_path):
    action = _action(HOME, tmp_path)
    action.apply(action.plan(managed=[]))

    written = open(_zone_file(tmp_path, "home")).read()
    assert '<service name="ssh"/>' in written
    assert '<service name="samba"/>' in written


def test_an_extra_zone_carries_no_upstream_defaults(tmp_path):
    """`allowed_services` IS the list. firewalld's own `home` allows
    dhcpv6-client and mdns; a config that did not ask for them must not get
    them, or the declaration does not describe the machine it produces."""
    action = _action(HOME, tmp_path)
    action.apply(action.plan(managed=[]))

    written = open(_zone_file(tmp_path, "home")).read()
    assert "dhcpv6-client" not in written
    assert "mdns" not in written


def test_plan_apply_plan_is_silent(tmp_path):
    action = _action(HOME, tmp_path)
    action.apply(action.plan(managed=[]))

    assert _action(HOME, tmp_path).plan(managed=[]) == []


def test_the_public_zone_still_works_beside_an_extra_one(tmp_path):
    cfg = {"enable": True, "allowed_services": ["syncthing"],
           "remove_services": ["ssh"],
           "zones": {"home": {"allowed_services": ["samba"]}}}
    action = _action(cfg, tmp_path)
    action.apply(action.plan(managed=[]))

    public = open(_zone_file(tmp_path, "public")).read()
    home = open(_zone_file(tmp_path, "home")).read()
    assert '<service name="syncthing"/>' in public and "ssh" not in public
    assert '<service name="samba"/>' in home


def test_a_rich_rule_works_in_an_extra_zone(tmp_path):
    cfg = {"enable": True, "zones": {"home": {
        "allowed_services": ["samba"],
        "rich_rules": ['rule service name="ssh" accept limit value="2/m"']}}}
    action = _action(cfg, tmp_path)
    action.apply(action.plan(managed=[]))

    assert '<limit value="2/m"/>' in open(_zone_file(tmp_path, "home")).read()


def test_an_undeclared_zone_is_owned_and_removed(tmp_path):
    """Dropping a zone from the config must take its file with it, or the
    machine keeps enforcing rules nothing declares any more."""
    action = _action(HOME, tmp_path)
    action.apply(action.plan(managed=[]))

    plain = _action({"enable": True}, tmp_path)
    planned = [(c.op.name, c.item) for c in plain.plan(managed=["public", "home"])]
    assert ("REMOVE", "home") in planned

    plain.apply([c for c in plain.plan(managed=["public", "home"])])
    assert not os.path.exists(_zone_file(tmp_path, "home"))


def test_ownership_covers_every_declared_zone(tmp_path):
    action = _action(HOME, tmp_path)
    action.apply(action.plan(managed=[]))

    assert set(action.managed_keys()["firewall"]) == {"public", "home"}
    assert "home" in action.actual()


# --------------------------------------------------------------------------- #
#  sync
# --------------------------------------------------------------------------- #

_LIVE = {
    "public": (["dhcpv6-client", "samba", "syncthing"],
               ['rule service name="ssh" accept limit value="2/m"']),
    "home": (["ssh", "mdns", "samba", "dhcpv6-client"], []),
}


def _offline_cmd(root, zones=_LIVE):
    """`firewall-offline-cmd --zone=Z --list-services|--list-rich-rules`."""
    for zone in zones:
        path = _zone_file(root, zone)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").write("<zone/>\n")
    # noise the enumeration must ignore: firewalld's own backup files
    open(_zone_file(root, "home") + ".old", "w").write("<zone/>\n")

    def run(cmd, args=None, **kwargs):
        if cmd != "firewall-offline-cmd":
            return MagicMock(returncode=1, stdout=b"")
        zone = next((a.split("=", 1)[1] for a in args if a.startswith("--zone=")), None)
        services, rich = zones.get(zone, ([], []))
        payload = " ".join(services) if "--list-services" in args else "\n".join(rich)
        return MagicMock(returncode=0, stdout=payload.encode())
    return run


def _captured(tmp_path, cfg=None):
    action = _action(cfg or {"enable": True}, tmp_path)
    action._ufw_installed = MagicMock(return_value=False)
    with patch("dasik.lib.actions.firewall_action.Command.execute",
               side_effect=_offline_cmd(tmp_path)):
        return action.import_state()["firewall"]


def test_sync_captures_a_customised_extra_zone(tmp_path):
    captured = _captured(tmp_path)

    assert captured["zones"]["home"]["allowed_services"] == [
        "dhcpv6-client", "mdns", "samba", "ssh"]


def test_sync_still_captures_public_at_the_top_level(tmp_path):
    captured = _captured(tmp_path)

    assert captured["allowed_services"] == ["samba", "syncthing"]
    assert captured["remove_services"] == ["ssh"]
    assert captured["rich_rules"] == [
        'rule service name="ssh" accept limit value="2/m"']
    assert "public" not in captured.get("zones", {})


def test_sync_ignores_firewalld_backup_files(tmp_path):
    """firewalld leaves `home.xml.old` beside the zone it rewrote; a zone called
    `home.xml` does not exist."""
    captured = _captured(tmp_path)

    assert set(captured.get("zones", {})) == {"home"}


def test_a_machine_with_only_public_captures_no_zones_key(tmp_path):
    action = _action({"enable": True}, tmp_path)
    action._ufw_installed = MagicMock(return_value=False)
    with patch("dasik.lib.actions.firewall_action.Command.execute",
               side_effect=_offline_cmd(tmp_path, {"public": (["samba"], [])})):
        captured = action.import_state()["firewall"]

    assert "zones" not in captured


def test_the_capture_re_plans_to_nothing(tmp_path):
    """sync → plan silent, the invariant that matters."""
    captured = _captured(tmp_path)
    action = _action(captured, tmp_path)
    action.apply(action.plan(managed=[]))

    assert _action(captured, tmp_path).plan(managed=[]) == []


def test_the_capture_validates(tmp_path):
    FirewallModel(**_captured(tmp_path))

"""FirewallAction — declarative firewalld public.xml, idempotent.

Owns /etc/firewalld/zones/public.xml (services = defaults − remove + allowed,
plus rich rules). Idempotent by content compare — fixes the firewalld
default-service quirk where `--remove-service=ssh` re-fired every apply. Verified
with tests (pure file generation), no firewalld/QEMU needed.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from dasik.lib.actions.firewall_action import FirewallAction, _rich_rule_to_xml
from dasik.lib.exceptions.exceptions import ConfigValidationError
from dasik.lib.state.change import Change, Op


def _fw(current=None, **cfg):
    cfg.setdefault("enable", True)
    a = FirewallAction(cfg, context=SimpleNamespace(target=object()))
    a._current_xml = lambda zone='public': current
    return a


def _ctx_with_backend(backend):
    """A context carrying a manifest that recorded which backend dasik
    actually applied last time (S3, ``state_metadata()`` -> action_state)."""
    return SimpleNamespace(target=object(),
                           manifest={"action_state": {"firewall": {"backend": backend}}})


# --- rich-rule converter -------------------------------------------------- #

def test_rich_rule_source_accept():
    xml = _rich_rule_to_xml('rule family=ipv4 source address=192.168.1.0/24 accept')
    assert xml == '<rule family="ipv4"><source address="192.168.1.0/24"/><accept/></rule>'


def test_rich_rule_quoted_and_port():
    xml = _rich_rule_to_xml('rule family="ipv6" port port="443" protocol="tcp" reject')
    assert xml == '<rule family="ipv6"><port port="443" protocol="tcp"/><reject/></rule>'


def test_rich_rule_service_drop():
    xml = _rich_rule_to_xml('rule service name=ssh drop')
    assert xml == '<rule><service name="ssh"/><drop/></rule>'


def test_rich_rule_accept_keeps_rate_limit():
    """A rate limit is part of the action element — dropping it widens access."""
    xml = _rich_rule_to_xml('rule service name="ssh" accept limit value="2/m"')
    assert xml == ('<rule><service name="ssh"/>'
                   '<accept><limit value="2/m"/></accept></rule>')


def test_rich_rule_reject_keeps_rate_limit():
    xml = _rich_rule_to_xml('rule family="ipv4" port port="80" protocol="tcp" '
                            'reject limit value="10/s"')
    assert xml == ('<rule family="ipv4"><port port="80" protocol="tcp"/>'
                   '<reject><limit value="10/s"/></reject></rule>')


def test_rich_rule_unsupported_clause_fails_closed():
    """An access rule that cannot be represented must be rejected, not widened."""
    with pytest.raises(ConfigValidationError):
        _rich_rule_to_xml('rule service name="ssh" log prefix="ssh" level=info accept')


def test_rich_rule_without_action_fails_closed():
    with pytest.raises(ConfigValidationError):
        _rich_rule_to_xml('rule service name="ssh"')


def test_desired_xml_propagates_unsupported_rule(tmp_path):
    a = _fw(rich_rules=['rule service name="ssh" audit accept'])
    with pytest.raises(ConfigValidationError):
        a._desired_xml()


# --- zone XML + idempotent plan ------------------------------------------- #

def test_removed_default_service_absent_allowed_present():
    a = _fw(allowed_services=["syncthing"], remove_services=["ssh"])
    xml = a._desired_xml()
    assert '<service name="ssh"/>' not in xml           # removed default gone
    assert '<service name="dhcpv6-client"/>' in xml     # other default kept
    assert '<service name="syncthing"/>' in xml         # allowed present


def test_converged_zone_is_a_noop():
    a = _fw(allowed_services=["syncthing"], remove_services=["ssh"])
    a._current_xml = lambda zone="public": a._desired_xml(zone)            # file already matches
    assert a.plan([]) == []
    assert a.is_needed() is False


def test_divergent_zone_plans_one_modify():
    a = _fw(current="<zone></zone>", allowed_services=["syncthing"])
    changes = a.plan([])
    assert [(c.op, c.item) for c in changes] == [(Op.MODIFY, "public")]


def test_remove_service_is_idempotent_across_applies():
    """The bug the megamix caught: after applying, a re-plan must be empty (the
    removed service stays absent from the written file)."""
    a = _fw(remove_services=["ssh"])
    written = a._desired_xml()
    a._current_xml = lambda zone="public": written                     # simulate post-apply state
    assert a.plan([]) == []                              # no re-fire
    assert '<service name="ssh"/>' not in written


def test_disabled_plans_nothing():
    assert FirewallAction({"enable": False}, context=SimpleNamespace(target=object())).plan([]) == []


# --- removal: "firewall debe eliminar su config si desaparece" ------------- #
#
# firewalld backend: a zone file the manifest owns must be REMOVEd once the
# block goes `enable: false` or absent — previously `plan()` short-circuited
# at `if not self.enable: return []` before ever consulting `managed`.

def test_disabled_with_an_owned_zone_plans_its_removal():
    a = _fw(current="<zone></zone>", enable=False)
    changes = a.plan(managed=["public"])
    assert [(c.op, c.item) for c in changes] == [(Op.REMOVE, "public")]
    assert changes[0].destructive is True


def test_disabled_with_no_zone_file_plans_nothing():
    a = _fw(current=None, enable=False)
    assert a.plan(managed=["public"]) == []


def test_disabled_does_not_touch_an_unowned_zone():
    a = _fw(current="<zone></zone>", enable=False)
    assert a.plan(managed=[]) == []


# The block absent from the WHOLE config: the reconciler hands
# `empty_config()` ({}), so `backend` defaults to "firewalld" even when the
# machine's owned items are really ufw rule strings from a PAST generation
# that declared `backend: ufw`. The shape of the managed items (a bare zone
# name vs. an "<action> <target>" ufw rule) has to settle it, never a guess
# that could delete the wrong kind of thing.

def test_block_absent_with_zone_shaped_managed_items_removes_the_zone():
    a = FirewallAction(FirewallAction.empty_config(), context=SimpleNamespace(target=object()))
    a._current_xml = lambda zone="public": "<zone></zone>"
    assert [(c.op, c.item) for c in a.plan(managed=["public"])] == [(Op.REMOVE, "public")]


def test_block_absent_with_ufw_shaped_managed_items_removes_the_rule():
    from unittest.mock import patch

    a = FirewallAction(FirewallAction.empty_config(), context=SimpleNamespace(target=object()))
    live_status = ("Status: active\n\nTo Action From\n-- ------ ----\n"
                  "22/tcp ALLOW IN Anywhere\n")
    with patch("dasik.lib.actions.firewall_action.Command.execute",
               return_value=SimpleNamespace(stdout=live_status, returncode=0)):
        changes = a.plan(managed=["allow 22/tcp"])
    assert [(c.op, c.item) for c in changes] == [(Op.REMOVE, "allow 22/tcp")]


def test_block_absent_never_guesses_when_managed_is_empty():
    """Nothing owned -> nothing to clean up either way; no probe needed."""
    a = FirewallAction(FirewallAction.empty_config(), context=SimpleNamespace(target=object()))
    assert a.plan(managed=[]) == []


# --- S3: one backend decision, recorded and reused, never re-guessed ------- #
#
# The shape heuristic (`_looks_like_ufw_items`) is only a fallback for a
# manifest written before this field existed (dasik <= 0.18.0). Once a plan
# has recorded which backend it actually applied (`state_metadata()` ->
# `Manifest.action_state["firewall"]["backend"]`), that recording settles an
# absent-block teardown INSTEAD of guessing from the shape of `managed` --
# the heuristic can guess wrong (e.g. a stray `public.xml` left on a machine
# that is really running ufw).

def test_block_absent_prefers_the_recorded_backend_over_the_shape_heuristic():
    a = FirewallAction(FirewallAction.empty_config(), context=_ctx_with_backend("ufw"))
    a._current_xml = lambda zone="public": "<zone></zone>"   # a stray zone file
    with patch("dasik.lib.actions.firewall_action.Command.execute",
              return_value=SimpleNamespace(stdout="Status: inactive\n", returncode=0)):
        changes = a.plan(managed=["public"])
    # recorded backend says ufw: "public" is not a live ufw rule, so nothing
    # is removed -- and, crucially, the firewalld zone file is left alone
    # even though it exists on disk (the OLD shape-only heuristic guessed
    # firewalld here and planned a REMOVE for it).
    assert changes == []


def test_block_absent_falls_back_to_the_shape_heuristic_without_a_recorded_backend():
    """A manifest written by dasik <= 0.18.0 has no recorded backend -- the
    shape heuristic is the only fallback available, and must still work."""
    a = FirewallAction(FirewallAction.empty_config(), context=SimpleNamespace(target=object()))
    a._current_xml = lambda zone="public": "<zone></zone>"
    assert [(c.op, c.item) for c in a.plan(managed=["public"])] == [(Op.REMOVE, "public")]


def test_state_metadata_persists_the_firewalld_backend():
    a = _fw(current="<zone></zone>", allowed_services=["syncthing"])
    a.plan([])
    assert a.state_metadata() == {"firewall": {"backend": "firewalld"}}


def test_state_metadata_persists_the_ufw_backend():
    action = FirewallAction({"enable": True, "backend": "ufw", "rules": ["allow 22/tcp"]},
                            context=SimpleNamespace(target=object()))
    with patch("dasik.lib.actions.firewall_action.Command.execute",
              return_value=SimpleNamespace(stdout="Status: inactive\n", returncode=0)):
        action.plan(managed=[])
    assert action.state_metadata() == {"firewall": {"backend": "ufw"}}


def test_state_metadata_is_empty_before_any_plan():
    """Nothing to persist until plan() has actually decided something."""
    a = FirewallAction({"enable": True}, context=SimpleNamespace(target=object()))
    assert a.state_metadata() == {}


def test_apply_reuses_plans_decision_never_re_derives_from_the_change_shape(tmp_path):
    """apply() must reuse plan()'s decision (S3) rather than re-deriving one
    from the shape of the CHANGES it is handed -- the mismatch that let plan
    announce one backend while apply drove another (PROBE-4)."""
    a = FirewallAction({"enable": True, "allowed_services": ["syncthing"]},
                       context=SimpleNamespace(target=None))
    a._zone_file = lambda zone="public": str(tmp_path / f"{zone}.xml")
    a.plan([])                      # decides + stores "firewalld"
    with patch("dasik.lib.actions.firewall_action.Command.execute") as run:
        # an item shaped like a ufw rule must NOT flip apply() to the ufw
        # backend -- the decision was already made, once, in plan().
        a.apply([Change("firewall", Op.REMOVE, "allow ssh")])
    assert not run.called    # no ufw CLI (`_ensure_ufw_installed`) invoked


def test_firewall_is_registered_before_packages():
    """A ufw REMOVE needs the `ufw` binary, which PackagesAction may uninstall
    in the SAME apply once the `firewall` toggle stops declaring it — so this
    action must run first, exactly like SnapperAction (dasik#353 precedent)."""
    from dasik.lib.actions.action_registry import get_default_registry
    from dasik.lib.actions.actions_handler_v2 import setup_actions
    setup_actions()
    names = [m["class"].__name__ for m in get_default_registry().get_all_actions()]
    assert names.index("FirewallAction") < names.index("PackagesAction")


def test_apply_writes_zone_file(tmp_path):
    a = FirewallAction({"enable": True, "allowed_services": ["syncthing"]},
                       context=SimpleNamespace(target=None))
    a._zone_file = lambda zone="public": str(tmp_path / f"{zone}.xml")
    a.apply(a.plan([]))
    written = (tmp_path / "public.xml").read_text()
    assert '<service name="syncthing"/>' in written
    # second apply is a no-op (content already matches)
    a._current_xml = lambda zone="public": written
    assert a.plan([]) == []


# --- import_state (sync capture) ----------------------------------------- #

from unittest.mock import patch


def _fw_live(outputs):
    a = FirewallAction({}, context=SimpleNamespace(target=object()))

    def fake(cmd, args=None, *rest, **kw):
        key = tuple(args or [])
        if cmd == "firewall-offline-cmd" and key in outputs:
            return SimpleNamespace(stdout=outputs[key], returncode=0)
        return SimpleNamespace(stdout=b"", returncode=1)

    return a, fake


def test_import_state_reconstructs_from_live_firewalld():
    a, fake = _fw_live({
        ("--zone=public", "--list-services"): b"dhcpv6-client samba syncthing\n",
        ("--zone=public", "--list-rich-rules"):
            b'rule family="ipv4" source address="10.0.0.0/8" accept\n',
    })
    with patch("dasik.lib.actions.firewall_action.Command.execute", side_effect=fake):
        frag = a.import_state(managed=[])
    fw = frag["firewall"]
    assert fw["enable"] is True
    assert fw["allowed_services"] == ["samba", "syncthing"]   # dhcpv6-client is a default
    assert fw["remove_services"] == ["ssh"]                   # ssh default not present -> removed
    assert fw["rich_rules"] == ['rule family="ipv4" source address="10.0.0.0/8" accept']


def test_import_state_empty_when_firewalld_unavailable():
    a, fake = _fw_live({})            # every firewall-cmd returns rc=1
    with patch("dasik.lib.actions.firewall_action.Command.execute", side_effect=fake):
        assert a.import_state(managed=[]) == {}


def test_import_state_no_extra_keys_when_defaults_only():
    a, fake = _fw_live({
        ("--zone=public", "--list-services"): b"dhcpv6-client ssh\n",
        ("--zone=public", "--list-rich-rules"): b"\n",
    })
    with patch("dasik.lib.actions.firewall_action.Command.execute", side_effect=fake):
        frag = a.import_state(managed=[])
    # exactly the upstream defaults -> nothing added/removed, no rich rules
    assert frag["firewall"] == {"enable": True}


def test_import_state_roundtrips_to_noop():
    # capture from live -> feed the block back -> desired xml equals the live one
    a, fake = _fw_live({
        ("--zone=public", "--list-services"): b"dhcpv6-client samba\n",
        ("--zone=public", "--list-rich-rules"): b"",
    })
    with patch("dasik.lib.actions.firewall_action.Command.execute", side_effect=fake):
        captured = a.import_state(managed=[])["firewall"]
    b = FirewallAction(captured, context=SimpleNamespace(target=object()))
    # desired for {defaults - {ssh}} | {samba} = dhcpv6-client, samba
    xml = b._desired_xml()
    assert '<service name="samba"/>' in xml
    assert '<service name="dhcpv6-client"/>' in xml
    assert '<service name="ssh"/>' not in xml


# --- actual() must report reality regardless of `enable` (S2) -------------- #
#
# `actual()` used to return `set()` whenever `enable` was false — so a `sync`
# run while the block was disabled/absent computed
# `actual ∩ (claimable ∪ declared)` with actual=set(), dispossessing the
# manifest of a zone/rule it still owned. `actual()` must be the "A = all"
# convention `SystemdAction.actual()` already follows: report every
# customised zone / every live ufw rule, and let the reconciler's
# intersection with claimable/declared scope it.

from dasik.lib.target.target import Target
from dasik.lib.actions.action_context import ActionContext


def test_actual_reports_zones_even_when_disabled(tmp_path):
    zones = tmp_path / "etc/firewalld/zones"
    zones.mkdir(parents=True)
    (zones / "public.xml").write_text("<zone></zone>\n")
    a = FirewallAction({"enable": False}, ActionContext(target=Target(root=str(tmp_path))))
    assert a.actual() == {"public"}


def test_actual_reports_zones_when_the_block_is_absent(tmp_path):
    zones = tmp_path / "etc/firewalld/zones"
    zones.mkdir(parents=True)
    (zones / "public.xml").write_text("<zone></zone>\n")
    a = FirewallAction(FirewallAction.empty_config(),
                       ActionContext(target=Target(root=str(tmp_path))))
    assert a.actual() == {"public"}


def test_actual_ufw_reports_every_live_rule_even_when_disabled():
    """ufw's `actual()` is now "every live rule", not "every DECLARED rule
    that happens to be live" -- the same widening the reconciler's own
    intersection with claimable/declared already scopes for every other
    domain (systemd, snapper)."""
    cfg = {"enable": False, "backend": "ufw", "rules": ["allow 22/tcp"]}
    live_status = ("Status: active\n\nTo Action From\n-- ------ ----\n"
                  "22/tcp ALLOW IN Anywhere\n9999/tcp ALLOW IN Anywhere\n")
    a = FirewallAction(cfg, context=SimpleNamespace(target=object()))
    with patch("dasik.lib.actions.firewall_action.Command.execute",
              return_value=SimpleNamespace(stdout=live_status, returncode=0)):
        assert a.actual() == {"allow 22/tcp", "allow 9999/tcp"}


def test_actual_with_no_recorded_backend_reports_both_backends_reality(tmp_path):
    """SF-4 residual: block absent, no explicit `backend` declared, and the
    manifest has NO recorded backend at all (a manifest that predates S3, or
    one from a converged apply -- `_cmd_apply` never persists a decision when
    the plan was already empty). `actual()` never sees `managed`, so the
    shape heuristic can never classify an empty tuple and always answers
    "firewalld" -- probing only firewalld and reporting `set()` for a ufw
    machine, which is exactly the S2 dispossession bug reopened for this one
    case (PROBE-3c). The unresolved case must report reality from BOTH
    backends; the reconciler's own intersection with claimable/declared still
    scopes it to what dasik actually owns."""
    zones = tmp_path / "etc/firewalld/zones"
    zones.mkdir(parents=True)
    (zones / "public.xml").write_text("<zone></zone>\n")
    live_status = ("Status: active\n\nTo Action From\n-- ------ ----\n"
                  "22/tcp ALLOW IN Anywhere\n")
    a = FirewallAction(FirewallAction.empty_config(),
                       ActionContext(target=Target(root=str(tmp_path))))
    with patch("dasik.lib.actions.firewall_action.Command.execute",
              return_value=SimpleNamespace(stdout=live_status, returncode=0)):
        assert a.actual() == {"public", "allow 22/tcp"}


def test_actual_with_an_explicit_backend_never_reports_the_other(tmp_path):
    """The union is only for the genuinely UNRESOLVED case -- a machine that
    settles the question with an explicit `backend: ufw` must never ALSO
    claim a firewalld zone that merely happens to have a file on disk (e.g.
    left by a previous firewalld install), which is what `sync` scoping
    unowned drift to nothing depends on."""
    zones = tmp_path / "etc/firewalld/zones"
    zones.mkdir(parents=True)
    (zones / "public.xml").write_text("<zone></zone>\n")
    cfg = {"enable": False, "backend": "ufw", "rules": ["allow 22/tcp"]}
    live_status = ("Status: active\n\nTo Action From\n-- ------ ----\n"
                  "22/tcp ALLOW IN Anywhere\n")
    a = FirewallAction(cfg, ActionContext(target=Target(root=str(tmp_path))))
    with patch("dasik.lib.actions.firewall_action.Command.execute",
              return_value=SimpleNamespace(stdout=live_status, returncode=0)):
        assert a.actual() == {"allow 22/tcp"}


def test_actual_with_a_recorded_backend_never_reports_the_other(tmp_path):
    """Same, for the manifest-recorded path (block absent, no explicit
    `backend`, but a previous apply recorded which one it actually used)."""
    zones = tmp_path / "etc/firewalld/zones"
    zones.mkdir(parents=True)
    (zones / "public.xml").write_text("<zone></zone>\n")
    manifest = {"action_state": {"firewall": {"backend": "ufw"}}}
    live_status = ("Status: active\n\nTo Action From\n-- ------ ----\n"
                  "22/tcp ALLOW IN Anywhere\n")
    a = FirewallAction(FirewallAction.empty_config(),
                       ActionContext(target=Target(root=str(tmp_path)), manifest=manifest))
    with patch("dasik.lib.actions.firewall_action.Command.execute",
              return_value=SimpleNamespace(stdout=live_status, returncode=0)):
        assert a.actual() == {"allow 22/tcp"}

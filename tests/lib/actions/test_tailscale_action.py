"""TailscaleAction: the /etc/tailscale/tailscaled.conf domain.

Every conffile key asserted here was pinned empirically against the binary by
scripts/vmtest/guest-tsspike.sh, because the alpha0 schema ships no
documentation. Three plausible names are NOT keys and are asserted as such —
`ExitNodeAllowLANAccess` (which is the *prefs* name), `SSH` and `NoSNAT` — since
a wrong key is a daemon that refuses to start.
"""
import json
import os

import pytest

from dasik.lib.actions.tailscale_action import (
    TailscaleAction,
    _CONF,
    _CONFFILE_KEYS,
    _parse,
    _render,
)
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.state.change import Op
from dasik.lib.target.target import Target


def _ctx(root):
    return ActionContext(target=Target(root=root))


def _cfg(**over):
    block = {"accept_routes": True}
    block.update(over)
    return {"tailscale": block}


# --- the key map is the part that cannot be guessed ---------------------- #

def test_conffile_key_names_are_the_ones_the_binary_accepts():
    assert _CONFFILE_KEYS["accept_routes"] == "AcceptRoutes"
    assert _CONFFILE_KEYS["accept_dns"] == "AcceptDNS"
    # NOT "SSH" — rejected by tailscaled as an unknown field.
    assert _CONFFILE_KEYS["ssh"] == "RunSSHServer"
    # NOT "ExitNodeAllowLANAccess" — that is the prefs name, not the conffile's.
    assert _CONFFILE_KEYS["exit_node_allow_lan_access"] == "AllowLANWhileUsingExitNode"


@pytest.mark.parametrize("not_a_key", ["ExitNodeAllowLANAccess", "SSH", "NoSNAT",
                                       "AdvertiseTags", "ForceDaemon"])
def test_rejected_names_are_never_emitted(not_a_key):
    """A key tailscaled does not know makes it refuse to start, so the renderer
    must never be able to produce one."""
    assert not_a_key not in _CONFFILE_KEYS.values()


# --- rendering ----------------------------------------------------------- #

def test_render_auth_key_file_becomes_a_file_reference():
    out = json.loads(_render({"auth_key_file": "/etc/tailscale/authkey"}))
    assert out["AuthKey"] == "file:/etc/tailscale/authkey"


def test_parse_captures_only_the_file_form_of_authkey():
    """sync must capture the PATH, and must never copy a literal key — a
    hand-provisioned secret in the conffile stays out of the Git config."""
    assert _parse('{"AuthKey": "file:/etc/tailscale/authkey"}') == {
        "auth_key_file": "/etc/tailscale/authkey"}
    assert _parse('{"AuthKey": "tskey-auth-abc123-def"}') == {}


def test_desired_omits_authkey_while_the_key_file_is_missing(tmp_path):
    """Measured in the guest oracle: tailscaled refuses to START on a dangling
    file: reference. A not-yet-provisioned key must not take the daemon down —
    omit + warn, and converge without it."""
    from unittest.mock import MagicMock, patch

    a = TailscaleAction({"tailscale": {"accept_routes": True,
                                       "auth_key_file": "/etc/tailscale/authkey"}},
                        _ctx(str(tmp_path)))
    with patch("dasik.lib.actions.tailscale_action.run_logger.get",
               return_value=MagicMock()) as log:
        desired = a._desired_value()
    assert "AuthKey" not in desired
    assert '"AcceptRoutes": true' in desired
    assert any("auth_key_file" in str(c)
               for c in log.return_value.warning.call_args_list)


def test_desired_carries_authkey_once_the_key_file_exists(tmp_path):
    keyfile = tmp_path / "etc" / "tailscale" / "authkey"
    keyfile.parent.mkdir(parents=True)
    keyfile.write_text("tskey-auth-dummy\n")
    a = TailscaleAction({"tailscale": {"auth_key_file": "/etc/tailscale/authkey"}},
                        _ctx(str(tmp_path)))
    assert json.loads(a._desired_value())["AuthKey"] == "file:/etc/tailscale/authkey"


def test_missing_key_file_converges_instead_of_planning_forever(tmp_path):
    """plan → apply → plan must end silent even while the key file is absent:
    the omitted AuthKey must not become a perpetual MODIFY."""
    from unittest.mock import MagicMock, patch

    a = TailscaleAction({"tailscale": {"accept_routes": True,
                                       "auth_key_file": "/etc/tailscale/authkey"}},
                        _ctx(str(tmp_path)))
    with patch("dasik.lib.actions.tailscale_action.run_logger.get",
               return_value=MagicMock()):
        a.apply(a.plan(managed=[]))
        assert a.plan(managed=[]) == []


def test_render_always_carries_the_mandatory_version():
    # tailscaled: 'no "version" field defined' when absent, and "alpha0" is the
    # only accepted value.
    assert json.loads(_render({"accept_routes": True}))["version"] == "alpha0"


def test_render_translates_snake_case_to_conffile_keys():
    out = json.loads(_render({"accept_routes": True, "accept_dns": False,
                              "ssh": True}))
    assert out["AcceptRoutes"] is True
    assert out["AcceptDNS"] is False
    assert out["RunSSHServer"] is True
    assert "accept_routes" not in out


def test_render_omits_keys_left_unset():
    """An absent key means 'tailscale's default', which is not the same as
    declaring the default — declaring it takes the pref away from the CLI."""
    out = json.loads(_render({"accept_routes": True}))
    assert set(out) == {"version", "AcceptRoutes"}


def test_render_empty_block_is_none():
    assert _render({}) is None


def test_render_is_canonical_regardless_of_declaration_order():
    a = _render({"accept_routes": True, "shields_up": False})
    b = _render({"shields_up": False, "accept_routes": True})
    assert a == b


def test_parse_roundtrips_render():
    block = {"accept_routes": True, "advertise_routes": ["10.0.0.0/8"],
             "hostname": "box"}
    assert _parse(_render(block)) == block


def test_parse_ignores_the_version_field():
    assert "version" not in _parse('{"version":"alpha0","AcceptRoutes":true}')


def test_parse_keeps_an_unknown_conffile_key_out_of_the_block():
    """Someone else's conffile may carry keys dasik does not model; they must not
    surface as bogus config fields."""
    assert _parse('{"version":"alpha0","AuthKey":"tskey-x"}') == {}


# --- v3 contract --------------------------------------------------------- #

def test_is_v3_and_optional():
    a = TailscaleAction({})
    assert TailscaleAction.is_v3() is True
    assert a.is_optional is True


def test_no_block_plans_nothing(tmp_path):
    assert TailscaleAction({}, _ctx(str(tmp_path))).plan(managed=[]) == []


def test_missing_on_target_plans_a_change(tmp_path):
    changes = TailscaleAction(_cfg(), _ctx(str(tmp_path))).plan(managed=[])
    assert [c.op for c in changes] == [Op.MODIFY]


def test_present_and_equal_plans_nothing(tmp_path):
    action = TailscaleAction(_cfg(), _ctx(str(tmp_path)))
    action.apply(action.plan(managed=[]))
    assert action.plan(managed=[]) == []


def test_apply_writes_the_conffile_where_the_daemon_looks(tmp_path):
    action = TailscaleAction(_cfg(), _ctx(str(tmp_path)))
    action.apply(action.plan(managed=[]))
    written = json.loads((tmp_path / _CONF.lstrip("/")).read_text())
    assert written == {"version": "alpha0", "AcceptRoutes": True}


def test_changed_value_plans_a_modify(tmp_path):
    TailscaleAction(_cfg(), _ctx(str(tmp_path))).apply(
        TailscaleAction(_cfg(), _ctx(str(tmp_path))).plan(managed=[]))
    changes = TailscaleAction(_cfg(accept_routes=False),
                              _ctx(str(tmp_path))).plan(managed=[])
    assert [c.op for c in changes] == [Op.MODIFY]


# --- the block REMOVED, which is not the same as the block off ----------- #

def test_dropped_block_removes_an_owned_conffile(tmp_path):
    """An EMPTY conffile still locks the CLI out, so an undeclared domain has to
    take the file away rather than blank it."""
    action = TailscaleAction(_cfg(), _ctx(str(tmp_path)))
    action.apply(action.plan(managed=[]))
    dropped = TailscaleAction({}, _ctx(str(tmp_path)))
    changes = dropped.plan(managed=[_CONF])
    assert [c.op for c in changes] == [Op.REMOVE]
    dropped.apply(changes)
    assert not (tmp_path / _CONF.lstrip("/")).exists()


def test_dropped_block_leaves_an_unowned_conffile_alone(tmp_path):
    """Someone else's conffile is not dasik's to delete."""
    path = tmp_path / _CONF.lstrip("/")
    os.makedirs(path.parent, exist_ok=True)
    path.write_text('{"version":"alpha0","AuthKey":"tskey-theirs"}')
    assert TailscaleAction({}, _ctx(str(tmp_path))).plan(managed=[]) == []
    assert path.exists()


# --- sync ---------------------------------------------------------------- #

def test_import_reads_the_machine(tmp_path):
    action = TailscaleAction(_cfg(shields_up=True), _ctx(str(tmp_path)))
    action.apply(action.plan(managed=[]))
    captured = TailscaleAction({}, _ctx(str(tmp_path))).import_state()
    assert captured == {"tailscale": {"accept_routes": True, "shields_up": True}}


def test_import_invents_nothing_on_a_machine_without_the_file(tmp_path):
    assert TailscaleAction({}, _ctx(str(tmp_path))).import_state() == {}


def test_import_clears_a_declared_block_the_machine_does_not_have(tmp_path):
    """sync reports reality. ConfigWriter.merge never deletes a key, so silence
    would leave the stale declaration standing."""
    captured = TailscaleAction(_cfg(), _ctx(str(tmp_path))).import_state()
    assert captured == {"tailscale": {}}


def test_sync_then_plan_is_silent(tmp_path):
    action = TailscaleAction(_cfg(ssh=True, hostname="box"), _ctx(str(tmp_path)))
    action.apply(action.plan(managed=[]))
    captured = TailscaleAction({}, _ctx(str(tmp_path))).import_state()
    assert TailscaleAction(captured, _ctx(str(tmp_path))).plan(managed=[]) == []


# --- a preference dasik does not know must not be dropped in silence ----- #

def test_render_refuses_an_unknown_preference():
    """The model forbids extras, but the action is also handed raw dicts that
    never crossed it. Skipping the key would converge and route nothing."""
    with pytest.raises(ValueError, match="unknown tailscale preference"):
        _render({"accpet_routes": True})


def test_the_error_names_the_known_preferences():
    with pytest.raises(ValueError, match="accept_routes"):
        _render({"nonsense": 1})


def test_port_is_not_a_conffile_key():
    """`port` lives in /etc/default/tailscaled beside the --config flag. In the
    conffile it would be an unknown field, and tailscaled would refuse to start."""
    out = json.loads(_render({"accept_routes": True, "port": 41641}))
    assert set(out) == {"version", "AcceptRoutes"}


def test_a_block_of_only_port_writes_no_conffile():
    assert _render({"port": 41641}) is None

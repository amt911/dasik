"""`sync` must report the network manager the MACHINE runs (issue #196).

A config that declares a `hostname` and no `network` block is valid, and the
capture of such a machine used to come back as `network: {"type": ""}` — which
`dasik check` then rejects, because the field is a two-value Literal. That
breaks the whole `sync` -> `check` -> `plan` round trip, silently, until someone
tries to use the captured file.
"""
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.network_action import NetworkAction
from dasik.lib.models.json_model import JsonModel
from dasik.lib.target.target import Target


def _machine(tmp_path, hostname="arch"):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/hostname").write_text(hostname + "\n")
    (tmp_path / "etc/hosts").write_text("127.0.0.1 localhost\n")
    return tmp_path


def _captured(tmp_path, seed, enabled=()):
    """import_state with `systemctl is-enabled` answering for *enabled* units."""
    def fake(cmd, args=None, *_rest, **_kw):
        unit = (args or ["", ""])[1]
        ok = unit in enabled
        return MagicMock(stdout=b"enabled\n" if ok else b"disabled\n",
                         returncode=0 if ok else 1)

    action = NetworkAction(seed, ActionContext(target=Target(root=str(tmp_path))))
    with patch("dasik.lib.actions.network_action.Command.execute", side_effect=fake):
        return action.import_state([])


def test_the_enabled_network_manager_is_what_is_reported(tmp_path):
    captured = _captured(_machine(tmp_path), {"hostname": "arch"},
                         enabled=("NetworkManager.service",))

    assert captured["network"]["type"] == "NetworkManager"


def test_systemd_networkd_is_reported_too(tmp_path):
    captured = _captured(_machine(tmp_path), {"hostname": "arch"},
                         enabled=("systemd-networkd.service",))

    assert captured["network"]["type"] == "systemd-networkd"


def test_a_machine_running_neither_captures_no_network_block(tmp_path):
    """The bug: an empty type is not a value the schema accepts, so reporting
    one produces a capture dasik itself refuses."""
    captured = _captured(_machine(tmp_path), {"hostname": "arch"})

    assert "network" not in captured
    assert captured["hostname"] == "arch"


def test_the_capture_of_such_a_machine_validates(tmp_path):
    captured = _captured(_machine(tmp_path), {"hostname": "arch"})
    JsonModel(**{
        "locales": {"selected_locales": [], "desired_locale": "en_US.UTF-8",
                    "desired_tty_layout": "us"},
        "timezone": {"region": "Europe", "city": "Madrid"},
        **captured,
    })


def test_a_declared_type_survives_a_machine_that_answers_nothing(tmp_path):
    """No probe could answer (no systemctl in a scratch root, a target merely
    mounted): intent is better than dropping the declaration."""
    captured = _captured(_machine(tmp_path),
                         {"hostname": "arch", "network": {"type": "NetworkManager"}})

    assert captured["network"]["type"] == "NetworkManager"


def test_the_machine_beats_the_declaration(tmp_path):
    """sync reports reality: a config that says one thing and a machine that
    runs the other must come back as the machine."""
    captured = _captured(_machine(tmp_path),
                         {"hostname": "arch", "network": {"type": "NetworkManager"}},
                         enabled=("systemd-networkd.service",))

    assert captured["network"]["type"] == "systemd-networkd"


def test_nothing_is_captured_without_a_hostname(tmp_path):
    assert _captured(_machine(tmp_path), {}) == {}


@pytest.mark.parametrize("probe_raises", [OSError, RuntimeError])
def test_a_failing_probe_is_not_a_network_manager(tmp_path, probe_raises):
    action = NetworkAction({"hostname": "arch"},
                           ActionContext(target=Target(root=str(_machine(tmp_path)))))
    with patch("dasik.lib.actions.network_action.Command.execute",
               side_effect=probe_raises("no systemctl")):
        assert "network" not in action.import_state([])

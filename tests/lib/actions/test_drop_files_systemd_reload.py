"""A systemd drop-in dasik writes must reach the daemon, not just the disk.

Issue #300. `grep -rn daemon-reload dasik/` returned nothing: on a day-2
`apply --target /` the file landed exactly as planned, the next `plan` was
silent, and systemd carried on with its cached units. Invisible on an install
(the first boot reads everything) and invisible to the unit suite.

`systemctl restart` does NOT reload unit files, so an explicit restart is not a
workaround — which is what made this worth fixing rather than documenting.

SAFETY: every test here that uses a LIVE target (root="/") stubs the write path.
An unstubbed one would write into the developer's real /etc.
"""
from unittest.mock import MagicMock

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.drop_files_action import DropFilesAction
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target

_UNIT_DROPIN = "/etc/systemd/system/tailscaled.service.d/10-dasik.conf"
_LOGIND = "/etc/systemd/logind.conf.d/10-dasik.conf"
_NOT_SYSTEMD = "/etc/modprobe.d/10-dasik.conf"


@pytest.fixture
def executed(monkeypatch):
    """Record Command.execute calls instead of running systemctl."""
    calls = []

    def fake(cmd, args=None, **kwargs):
        calls.append((cmd, list(args or [])))
        return MagicMock(returncode=0, stdout=b"")

    monkeypatch.setattr("dasik.lib.actions.drop_files_action.Command.execute", fake)
    return calls


@pytest.fixture
def warnings(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr("dasik.lib.actions.drop_files_action.run_logger.get",
                        lambda: logger)
    return logger


def _action(root, files):
    cfg = {"files": [{"path": p, "content": "x\n"} for p in files]}
    return DropFilesAction(cfg, ActionContext(target=Target(root=root)))


def _applied(action, monkeypatch, writes=(), deletes=()):
    """apply() with the write path stubbed — the decision is what is under test,
    and a live target would otherwise write into the real /etc."""
    monkeypatch.setattr(DropFilesAction, "_write_file",
                        lambda self, canonical, content, modes: None)
    monkeypatch.setattr(DropFilesAction, "_pacman_owner", lambda self, path: None)
    monkeypatch.setattr("dasik.lib.actions.drop_files_action.os.path.exists",
                        lambda p: False)
    changes = [Change("files", Op.CREATE, p) for p in writes]
    changes += [Change("files", Op.DELETE, p) for p in deletes]
    action.apply(changes)


def _reloads(calls):
    return [c for c in calls if c == ("systemctl", ["daemon-reload"])]


# --- the reload itself ---------------------------------------------------- #

def test_a_systemd_file_written_on_a_live_target_reloads(executed, warnings,
                                                         monkeypatch):
    action = _action("/", [_UNIT_DROPIN])
    _applied(action, monkeypatch, writes=[_UNIT_DROPIN])
    assert len(_reloads(executed)) == 1


def test_a_systemd_file_DELETED_on_a_live_target_reloads(executed, warnings,
                                                         monkeypatch):
    """Removing a drop-in leaves the daemon running with it until a reload, which
    is the same bug pointing the other way."""
    action = _action("/", [])
    _applied(action, monkeypatch, deletes=[_UNIT_DROPIN])
    assert len(_reloads(executed)) == 1


def test_an_install_target_does_not_reload(executed, warnings, monkeypatch):
    """There is no running systemd under /mnt to reload, and its first boot reads
    every drop-in anyway."""
    action = _action("/mnt", [_UNIT_DROPIN])
    _applied(action, monkeypatch, writes=[_UNIT_DROPIN])
    assert _reloads(executed) == []


def test_a_non_systemd_file_does_not_reload(executed, warnings, monkeypatch):
    action = _action("/", [_NOT_SYSTEMD])
    _applied(action, monkeypatch, writes=[_NOT_SYSTEMD])
    assert _reloads(executed) == []


def test_no_changes_at_all_does_not_reload(executed, warnings, monkeypatch):
    action = _action("/", [_UNIT_DROPIN])
    _applied(action, monkeypatch)
    assert _reloads(executed) == []


def test_many_systemd_files_reload_once(executed, warnings, monkeypatch):
    action = _action("/", [_UNIT_DROPIN, _LOGIND])
    _applied(action, monkeypatch, writes=[_UNIT_DROPIN, _LOGIND])
    assert len(_reloads(executed)) == 1


# --- what a reload does NOT cover ----------------------------------------- #

def test_a_changed_unit_dropin_says_which_unit_to_restart(executed, warnings,
                                                          monkeypatch):
    """daemon-reload makes systemd SEE the drop-in; a unit already running keeps
    its old configuration until it restarts. Saying so beats a silent no-op."""
    action = _action("/", [_UNIT_DROPIN])
    _applied(action, monkeypatch, writes=[_UNIT_DROPIN])
    said = " ".join(str(c) for c in warnings.warning.call_args_list)
    assert "tailscaled.service" in said


def test_logind_says_a_reload_is_not_enough(executed, warnings, monkeypatch):
    """logind re-reads its configuration on restart, not on daemon-reload — and
    dasik must not restart it behind the user's back, since that can end the
    graphical session."""
    action = _action("/", [_LOGIND])
    _applied(action, monkeypatch, writes=[_LOGIND])
    said = " ".join(str(c) for c in warnings.warning.call_args_list)
    assert "systemd-logind" in said


def test_dasik_never_restarts_anything_itself(executed, warnings, monkeypatch):
    """Restarting logind can kill the session; restarting a network daemon can
    drop the connection the apply is running over. Reload, then say what is left
    to do."""
    action = _action("/", [_UNIT_DROPIN, _LOGIND])
    _applied(action, monkeypatch, writes=[_UNIT_DROPIN, _LOGIND])
    assert all(args[:1] != ["restart"] for _cmd, args in executed)


def test_sleep_conf_needs_no_follow_up(executed, warnings, monkeypatch):
    """systemd-sleep reads its configuration each time it runs, so there is
    nothing to tell the user — a warning here would be noise."""
    action = _action("/", ["/etc/systemd/sleep.conf.d/10-dasik.conf"])
    _applied(action, monkeypatch, writes=["/etc/systemd/sleep.conf.d/10-dasik.conf"])
    assert len(_reloads(executed)) == 1
    assert warnings.warning.call_args_list == []

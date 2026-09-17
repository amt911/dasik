"""A firewalld zone file dasik writes or removes on a LIVE target must reach
the running daemon, or it keeps enforcing the stale zone until something else
restarts it (N4). Mirrors `DropFilesAction._reload_systemd` / issue #300's
same lesson: pre-existing for MODIFY too (`grep -rn firewall-cmd dasik/`
returned nothing before this).

SAFETY: `_zone_file` is stubbed into tmp_path even for a "/" Target, so no
test here touches the real /etc.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from dasik.lib.actions.firewall_action import FirewallAction
from dasik.lib.target.target import Target


def _mock_logger(monkeypatch):
    """Patch the module's run_logger.get() with a MagicMock and return it, so
    a test can assert on `.warning(...)` calls (SF-2)."""
    logger = MagicMock()
    monkeypatch.setattr("dasik.lib.actions.firewall_action.run_logger.get",
                        lambda: logger)
    return logger


def _fw(root, **cfg):
    cfg.setdefault("enable", True)
    return FirewallAction(cfg, SimpleNamespace(target=Target(root=root)))


def _wired(action, tmp_path, monkeypatch, responses=None):
    action._zone_file = lambda zone="public": str(tmp_path / f"{zone}.xml")
    calls = []

    def fake(cmd, args=None, **kw):
        calls.append((cmd, list(args or [])))
        if responses and cmd in responses:
            return responses[cmd]
        return MagicMock(returncode=0, stdout=b"")

    monkeypatch.setattr("dasik.lib.actions.firewall_action.Command.execute", fake)
    return calls


def test_a_zone_write_on_a_live_target_reloads_firewalld(tmp_path, monkeypatch):
    action = _fw("/", allowed_services=["syncthing"])
    calls = _wired(action, tmp_path, monkeypatch)
    action.apply(action.plan([]))
    assert ("systemctl", ["is-active", "firewalld"]) in calls
    assert ("firewall-cmd", ["--reload"]) in calls


def test_a_zone_removal_on_a_live_target_reloads_firewalld(tmp_path, monkeypatch):
    (tmp_path / "public.xml").write_text("<zone></zone>\n")
    action = _fw("/", enable=False)
    calls = _wired(action, tmp_path, monkeypatch)
    action.apply(action.plan(managed=["public"]))
    assert ("firewall-cmd", ["--reload"]) in calls


def test_an_install_target_never_reloads(tmp_path, monkeypatch):
    """No systemd/firewalld running under /mnt to reload, and the first boot
    reads the zone fresh -- mirrors DropFilesAction's own install-target skip."""
    action = _fw("/mnt", allowed_services=["syncthing"])
    calls = _wired(action, tmp_path, monkeypatch)
    action.apply(action.plan([]))
    assert not any(cmd in ("firewall-cmd", "systemctl") for cmd, _ in calls)


def test_reload_is_skipped_when_firewalld_is_not_active(tmp_path, monkeypatch):
    action = _fw("/", allowed_services=["syncthing"])
    calls = _wired(action, tmp_path, monkeypatch,
                   responses={"systemctl": MagicMock(returncode=3, stdout=b"")})
    action.apply(action.plan([]))
    assert ("systemctl", ["is-active", "firewalld"]) in calls
    assert not any(cmd == "firewall-cmd" for cmd, _ in calls)


def test_a_noop_apply_never_probes_the_daemon(tmp_path, monkeypatch):
    """Nothing was written or removed (the zone already matched) -- no reason
    to even ask whether firewalld is active."""
    action = _fw("/", allowed_services=["syncthing"])
    action._zone_file = lambda zone="public": str(tmp_path / f"{zone}.xml")
    (tmp_path / "public.xml").write_text(action._desired_xml())
    calls = _wired(action, tmp_path, monkeypatch)
    action.apply(action.plan([]))
    assert calls == []


def test_the_ufw_backend_never_touches_firewall_cmd(tmp_path, monkeypatch):
    action = FirewallAction({"enable": True, "backend": "ufw", "rules": ["allow 22/tcp"]},
                            SimpleNamespace(target=Target(root="/")))
    calls = _wired(action, tmp_path, monkeypatch,
                   responses={"ufw": MagicMock(returncode=1, stdout=b"")})
    action.apply(action.plan(managed=[]))
    assert not any(cmd in ("firewall-cmd", "systemctl") for cmd, _ in calls)


# --- the reload must be best-effort: firewalld can be mid-reinstall ------- #
#
# VM-caught (fix round 1): `rollback` restoring a dropped `firewall` block
# re-applies FirewallAction (writes the zone, reloads) BEFORE PackagesAction
# (which reinstalls the `firewalld` package the PREVIOUS apply just
# uninstalled, per the SAME registry order that keeps a ufw REMOVE working
# while the binary is still there). `systemctl is-active firewalld` can still
# report "active" (a resident process from before the uninstall) while
# `/usr/bin/firewall-cmd` is genuinely gone from disk at that exact moment --
# `subprocess.run` then raises a bare `FileNotFoundError`, which used to
# propagate out of `apply()` and abort the ENTIRE apply/rollback over a
# cosmetic reload. The reload must be best-effort, exactly like
# `_ufw_status`'s own "a failed probe means unknown" idiom.

def test_a_missing_firewall_cmd_binary_never_aborts_the_apply(tmp_path, monkeypatch):
    action = _fw("/", allowed_services=["syncthing"])
    action._zone_file = lambda zone="public": str(tmp_path / f"{zone}.xml")
    logger = _mock_logger(monkeypatch)
    calls = []

    def fake(cmd, args=None, **kw):
        calls.append((cmd, list(args or [])))
        if cmd == "systemctl":
            return MagicMock(returncode=0, stdout=b"")     # stale "active" state
        if cmd == "firewall-cmd":
            raise FileNotFoundError(2, "No such file or directory", "firewall-cmd")
        return MagicMock(returncode=0, stdout=b"")

    monkeypatch.setattr("dasik.lib.actions.firewall_action.Command.execute", fake)
    action.apply(action.plan([]))              # must NOT raise
    assert (tmp_path / "public.xml").exists()  # the zone write itself still happened
    # N-5: the guard is against the fix regressing, not against an
    # intermediate commit -- assert the reload was genuinely attempted.
    assert ("systemctl", ["is-active", "firewalld"]) in calls
    assert ("firewall-cmd", ["--reload"]) in calls
    # SF-2: a swallowed reload failure must not be silent.
    assert logger.warning.called
    message = str(logger.warning.call_args)
    assert "firewalld" in message
    assert "systemctl restart firewalld" in message


def test_a_missing_systemctl_probe_never_aborts_the_apply(tmp_path, monkeypatch):
    """Defensive: the probe call itself is wrapped too, not just the reload."""
    action = _fw("/", allowed_services=["syncthing"])
    action._zone_file = lambda zone="public": str(tmp_path / f"{zone}.xml")
    logger = _mock_logger(monkeypatch)
    calls = []

    def fake(cmd, args=None, **kw):
        calls.append((cmd, list(args or [])))
        if cmd == "systemctl":
            raise FileNotFoundError(2, "No such file or directory", "systemctl")
        return MagicMock(returncode=0, stdout=b"")

    monkeypatch.setattr("dasik.lib.actions.firewall_action.Command.execute", fake)
    action.apply(action.plan([]))              # must NOT raise
    assert (tmp_path / "public.xml").exists()
    assert ("systemctl", ["is-active", "firewalld"]) in calls
    assert logger.warning.called
    message = str(logger.warning.call_args)
    assert "firewalld" in message
    assert "systemctl restart firewalld" in message


def test_a_reload_failure_rc_warns_with_remediation(tmp_path, monkeypatch):
    """SF-2: `firewall-cmd --reload` returning rc!=0 (e.g. a zone naming a
    service firewalld does not know) must not be silent -- the daemon keeps
    the previous runtime while dasik reports "Applied"."""
    action = _fw("/", allowed_services=["syncthing"])
    logger = _mock_logger(monkeypatch)
    calls = _wired(action, tmp_path, monkeypatch,
                   responses={"firewall-cmd": MagicMock(returncode=1, stdout=b"",
                                                        stderr=b"Error: INVALID_SERVICE")})
    action.apply(action.plan([]))
    assert ("firewall-cmd", ["--reload"]) in calls
    assert logger.warning.called
    message = str(logger.warning.call_args)
    assert "firewalld" in message
    assert "systemctl restart firewalld" in message


# --- SF-3: finalize_apply() retries once the whole apply has settled ------ #
#
# MEASURED live (fix round 2 VM re-drive): a `drop firewall block -> rollback`
# leaves the RUNNING daemon serving the stale (default) zone even though the
# file is restored and the plan goes silent -- FirewallAction runs BEFORE
# PackagesAction/SystemdAction, so its immediate reload attempt hits a
# transiently-missing `firewalld` binary (FileNotFoundError) precisely when
# the SAME apply also needs to reinstall the package. finalize_apply() is
# called by the reconciler once every action in the apply has completed
# successfully, so `firewalld` and its unit are guaranteed present by then.

def test_finalize_apply_retries_via_try_restart_after_a_missing_binary(tmp_path, monkeypatch):
    action = _fw("/", allowed_services=["syncthing"])
    action._zone_file = lambda zone="public": str(tmp_path / f"{zone}.xml")
    logger = _mock_logger(monkeypatch)

    def fake_during_apply(cmd, args=None, **kw):
        if cmd == "systemctl":
            return MagicMock(returncode=0, stdout=b"")     # stale "active" state
        if cmd == "firewall-cmd":
            raise FileNotFoundError(2, "No such file or directory", "firewall-cmd")
        return MagicMock(returncode=0, stdout=b"")

    monkeypatch.setattr("dasik.lib.actions.firewall_action.Command.execute",
                        fake_during_apply)
    action.apply(action.plan([]))
    assert action._reload_pending is True

    calls = []

    def fake_finalize(cmd, args=None, **kw):
        calls.append((cmd, list(args or [])))
        return MagicMock(returncode=0, stdout=b"")

    monkeypatch.setattr("dasik.lib.actions.firewall_action.Command.execute",
                        fake_finalize)
    action.finalize_apply()
    assert ("systemctl", ["try-restart", "firewalld"]) in calls


def test_finalize_apply_does_nothing_when_the_immediate_reload_succeeded(tmp_path, monkeypatch):
    action = _fw("/", allowed_services=["syncthing"])
    calls = _wired(action, tmp_path, monkeypatch)
    action.apply(action.plan([]))
    assert action._reload_pending is False
    calls.clear()
    action.finalize_apply()
    assert calls == []


def test_finalize_apply_does_nothing_when_nothing_was_ever_applied():
    """A fresh instance (plan() never ran, or apply() had no changes) has
    nothing pending -- finalize_apply() must be a safe no-op."""
    action = _fw("/", allowed_services=["syncthing"])
    action.finalize_apply()   # must not raise


def test_finalize_apply_respects_the_install_target_gate(tmp_path):
    """Defensive: even if `_reload_pending` were somehow set on an install
    target, finalize_apply() must never touch it -- no daemon runs under
    `/mnt` to retry against."""
    action = _fw("/mnt", allowed_services=["syncthing"])
    action._reload_pending = True
    calls = []

    def fake(cmd, args=None, **kw):
        calls.append((cmd, list(args or [])))
        return MagicMock(returncode=0, stdout=b"")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("dasik.lib.actions.firewall_action.Command.execute", fake)
        action.finalize_apply()
    assert calls == []


def test_finalize_apply_warns_when_the_retry_itself_fails(tmp_path, monkeypatch):
    action = _fw("/", allowed_services=["syncthing"])
    action._reload_pending = True
    logger = _mock_logger(monkeypatch)

    def fake(cmd, args=None, **kw):
        return MagicMock(returncode=1, stdout=b"", stderr=b"Unit firewalld.service not found.")

    monkeypatch.setattr("dasik.lib.actions.firewall_action.Command.execute", fake)
    action.finalize_apply()   # must not raise
    assert logger.warning.called
    message = str(logger.warning.call_args)
    assert "firewalld" in message
    assert "systemctl restart firewalld" in message

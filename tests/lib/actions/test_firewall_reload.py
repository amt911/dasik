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

    def fake(cmd, args=None, **kw):
        if cmd == "systemctl":
            return MagicMock(returncode=0, stdout=b"")     # stale "active" state
        if cmd == "firewall-cmd":
            raise FileNotFoundError(2, "No such file or directory", "firewall-cmd")
        return MagicMock(returncode=0, stdout=b"")

    monkeypatch.setattr("dasik.lib.actions.firewall_action.Command.execute", fake)
    action.apply(action.plan([]))              # must NOT raise
    assert (tmp_path / "public.xml").exists()  # the zone write itself still happened


def test_a_missing_systemctl_probe_never_aborts_the_apply(tmp_path, monkeypatch):
    """Defensive: the probe call itself is wrapped too, not just the reload."""
    action = _fw("/", allowed_services=["syncthing"])
    action._zone_file = lambda zone="public": str(tmp_path / f"{zone}.xml")

    def fake(cmd, args=None, **kw):
        if cmd == "systemctl":
            raise FileNotFoundError(2, "No such file or directory", "systemctl")
        return MagicMock(returncode=0, stdout=b"")

    monkeypatch.setattr("dasik.lib.actions.firewall_action.Command.execute", fake)
    action.apply(action.plan([]))              # must NOT raise
    assert (tmp_path / "public.xml").exists()

"""expand_pam — pwquality's package, and faillock's polkit-sandbox drop-in.

The drop-in is the 2026-08-18 laptop finding: polkit's agent helper runs under
``ProtectSystem=strict`` with no ``ReadWritePaths``, so ``pam_faillock``
cannot open its tally directory and every KDE authentication dialog dies with
"System error" — the correct password included. dasik's own ``persistent``
toggle points the tally at /var/lib/faillock, so dasik must also let the
helper write there.
"""
from dasik.lib.expand.toggles import expand_pam

_DROPIN = "/etc/systemd/system/polkit-agent-helper@.service.d/10-dasik-faillock.conf"


def _dropin(out):
    return next((f for f in out.get("files", []) if f["path"] == _DROPIN), None)


def test_faillock_ships_the_polkit_sandbox_dropin_for_the_persistent_dir():
    out = expand_pam({"pam": {"faillock": {"deny": 5, "persistent": True}}})
    f = _dropin(out)
    assert f is not None
    assert "ReadWritePaths=/var/lib/faillock" in f["content"]
    assert f["content"].rstrip().startswith("#")   # says WHY, not just what


def test_faillock_defaults_to_the_persistent_dir():
    out = expand_pam({"pam": {"faillock": {"deny": 3}}})
    assert "ReadWritePaths=/var/lib/faillock" in _dropin(out)["content"]


def test_non_persistent_faillock_whitelists_the_run_dir():
    out = expand_pam({"pam": {"faillock": {"persistent": False}}})
    assert "ReadWritePaths=/run/faillock" in _dropin(out)["content"]


def test_no_faillock_ships_no_dropin():
    out = expand_pam({"pam": {"pwquality": {"enable": True}}})
    assert _dropin(out) is None
    assert out.get("packages") == ["libpwquality"]


def test_no_pam_block_contributes_nothing():
    assert expand_pam({}) == {}


def test_pwquality_and_faillock_compose():
    out = expand_pam({"pam": {"pwquality": {"enable": True},
                              "faillock": {"deny": 5}}})
    assert out["packages"] == ["libpwquality"]
    assert _dropin(out) is not None

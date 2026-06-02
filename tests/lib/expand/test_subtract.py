from dasik.lib.expand import subtract_contributions


def test_subtract_removes_toggle_packages_from_capture():
    original = {"packages": ["firefox"], "bluetooth": {"enable": True}}
    captured = {"packages": ["firefox", "bluez", "bluez-utils", "htop"]}
    out = subtract_contributions(captured, original)
    assert out["packages"] == ["firefox", "htop"]  # bluez* attributed to toggle


def test_subtract_keeps_package_user_also_declared():
    original = {"packages": ["bluez"], "bluetooth": {"enable": True}}
    captured = {"packages": ["bluez", "bluez-utils"]}
    out = subtract_contributions(captured, original)
    assert "bluez" in out["packages"]  # user-declared, kept
    assert "bluez-utils" not in out["packages"]  # toggle-only, removed


def test_subtract_removes_units_and_sockets():
    original = {"cups": {"install": True}, "bluetooth": {"enable": True}}
    captured = {"systemd": {"enable_units": ["bluetooth.service", "sshd.service"],
                            "enable_sockets": ["cups.socket", "other.socket"]}}
    out = subtract_contributions(captured, original)
    assert out["systemd"]["enable_units"] == ["sshd.service"]
    assert out["systemd"]["enable_sockets"] == ["other.socket"]


def test_subtract_noop_without_toggles():
    captured = {"packages": ["firefox", "htop"]}
    assert subtract_contributions(captured, {}) == captured

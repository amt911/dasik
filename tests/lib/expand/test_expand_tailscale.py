"""expand_tailscale: the daemon, its unit, and the flag that makes the conffile
matter at all.

Writing /etc/tailscale/tailscaled.conf changes nothing on its own — tailscaled
reads it only when started with `--config`. A block that converged and did
nothing is exactly the silent failure this repo keeps finding, so the drop-in is
asserted here rather than assumed.
"""
from dasik.lib.expand import expand_config, subtract_contributions
from dasik.lib.expand.toggles import expand_tailscale

_DEFAULTS = "/etc/default/tailscaled"


def test_no_block_contributes_nothing():
    assert expand_tailscale({}) == {}
    assert expand_tailscale({"tailscale": {}}) == {}


def test_block_pulls_the_package_and_the_unit():
    out = expand_tailscale({"tailscale": {"accept_routes": True}})
    assert out["packages"] == ["tailscale"]
    assert out["units"] == ["tailscaled.service"]


def test_block_contributes_the_environment_file():
    """MEASURED in a guest: a tailscaled.service.d drop-in setting
    `Environment=FLAGS=...` does NOT reach the daemon — the vendor unit's
    EnvironmentFile wins, daemon-reload or not. So dasik writes that file."""
    out = expand_tailscale({"tailscale": {"accept_routes": True}})
    assert [f["path"] for f in out["files"]] == [_DEFAULTS]


def test_the_file_carries_the_config_flag():
    body = expand_tailscale({"tailscale": {"accept_routes": True}})["files"][0]["content"]
    assert 'FLAGS="--config=/etc/tailscale/tailscaled.conf"' in body


def test_the_file_also_carries_PORT():
    """The vendor unit interpolates ${PORT}; an empty one is not a working
    command line, so writing FLAGS alone would break the daemon."""
    body = expand_tailscale({"tailscale": {"accept_routes": True}})["files"][0]["content"]
    assert 'PORT="41641"' in body


def test_a_declared_port_overrides_the_vendor_default():
    body = expand_tailscale({"tailscale": {"accept_routes": True,
                                           "port": 51820}})["files"][0]["content"]
    assert 'PORT="51820"' in body


def test_no_systemd_dropin_is_written():
    """The drop-in mechanism was measured NOT to work; leaving one behind would be
    a file that looks like the cause of anything that goes wrong."""
    out = expand_tailscale({"tailscale": {"accept_routes": True}})
    assert all("systemd/system" not in f["path"] for f in out["files"])


def test_expand_config_merges_it_all_in():
    merged = expand_config({"tailscale": {"accept_routes": True},
                            "packages": ["base"]})
    assert "tailscale" in merged["packages"]
    assert "tailscaled.service" in merged["systemd"]["enable_units"]
    assert _DEFAULTS in [f["path"] for f in merged["files"]]


def test_a_config_that_already_declares_them_gets_no_duplicates():
    """Both real machines list the package and the unit by hand; the toggle must
    not double them up."""
    merged = expand_config({
        "tailscale": {"accept_routes": True},
        "packages": ["tailscale"],
        "systemd": {"enable_units": ["tailscaled.service"]},
    })
    assert merged["packages"].count("tailscale") == 1
    assert merged["systemd"]["enable_units"].count("tailscaled.service") == 1


def test_subtract_drops_what_the_toggle_re_derives():
    """sync must not write back a package the block already implies — otherwise
    the captured config grows a line every round trip."""
    original = {"tailscale": {"accept_routes": True}}
    captured = expand_config(original)
    stripped = subtract_contributions(captured, original)
    assert "tailscale" not in stripped.get("packages", [])
    assert _DEFAULTS not in [f["path"] for f in stripped.get("files", [])]

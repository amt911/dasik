"""Declaring oomd settings implies wanting systemd-oomd to run.

Without the unit the block converges — the drop-in lands on disk — and does
absolutely nothing, which is the silent half-feature the expand toggles exist
to prevent.
"""
from dasik.lib.expand import expand_config
from dasik.lib.expand.toggles import expand_oomd


def test_declared_settings_enable_the_daemon():
    assert expand_oomd({"oomd": {"SwapUsedLimit": "80%"}}) == {
        "units": ["systemd-oomd.service"]}


def test_an_empty_block_contributes_nothing():
    assert expand_oomd({"oomd": {}}) == {}


def test_no_block_contributes_nothing():
    assert expand_oomd({}) == {}


def test_the_manager_conf_blocks_need_no_daemon():
    """system.conf/user.conf configure PID 1 itself — nothing to enable."""
    assert expand_oomd({"systemd_system_conf": {"LogLevel": "debug"}}) == {}


def test_expand_config_enables_the_unit():
    merged = expand_config({"oomd": {"SwapUsedLimit": "80%"}})
    assert "systemd-oomd.service" in merged["systemd"]["enable_units"]

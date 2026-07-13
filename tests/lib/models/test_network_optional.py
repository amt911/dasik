"""`network` is an optional top-level section (CLAUDE.md: keep sections optional).

A minimal config that only sets a hostname (and no network manager) must parse:
``NetworkAction`` already no-ops on an absent ``network`` block, but ``JsonModel``
used to *require* it — which made ``config/vm-minimal.json`` (no network) fail to
parse and contradicted the "many optional sections" design. These tests pin the
field as optional with a ``None`` default while keeping a declared block intact.
"""
import glob
import json

import pytest

from dasik.lib.models.json_model import JsonModel


def _base(**extra):
    cfg = {
        "locales": {"selected_locales": ["en_US.UTF-8 UTF-8"],
                    "desired_locale": "en_US.UTF-8", "desired_tty_layout": "us"},
        "timezone": {"region": "Europe", "city": "Madrid"},
        "hostname": "arch",
    }
    cfg.update(extra)
    return JsonModel.model_validate(cfg)


def test_network_is_optional_and_defaults_to_none():
    # No `network` block at all — must parse, network is None.
    assert _base().network is None


def test_network_none_survives_model_dump():
    # `debug()` returns model_dump(); a consumer doing cfg.get("network", {}) or {}
    # must see a falsy value, never a missing key that KeyErrors.
    assert _base().model_dump()["network"] is None


def test_declared_network_still_parsed():
    m = _base(network={"type": "NetworkManager", "add_default_hosts": True})
    assert m.network is not None
    assert m.network.type == "NetworkManager"


def test_vm_minimal_sample_parses():
    # Regression: config/vm-minimal.json omits `network` and was committed broken.
    with open("config/vm-minimal.json") as f:
        JsonModel.model_validate(json.load(f))


# Every tracked full-install sample (has a top-level `hostname`) must parse — a
# regression guard so a sample can never be committed against a drifted schema.
_FULL_CONFIGS = [
    p for p in sorted(glob.glob("config/*.json"))
    if "hostname" in json.load(open(p))
]


@pytest.mark.parametrize("path", _FULL_CONFIGS)
def test_all_full_sample_configs_parse(path):
    with open(path) as f:
        JsonModel.model_validate(json.load(f))

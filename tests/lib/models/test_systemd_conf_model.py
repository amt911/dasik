"""The three /etc/systemd/*.conf blocks are ini content written verbatim.

Whatever the config says lands in a file systemd parses, so the boundary has to
reject anything that would forge structure — a newline in a value can add an
arbitrary directive, and a key is a bare directive name, never an expression.
"""
import pytest

from dasik.lib.models.json_model import JsonModel


_KEYS = ["oomd", "systemd_system_conf", "systemd_user_conf"]


@pytest.mark.parametrize("key", _KEYS)
def test_defaults_to_absent(key):
    assert getattr(JsonModel(), key) is None


@pytest.mark.parametrize("key", _KEYS)
def test_accepts_a_section_of_settings(key):
    m = JsonModel(**{key: {"DefaultMemoryPressureDurationSec": "20s",
                           "SwapUsedLimit": "90%"}})
    assert getattr(m, key)["SwapUsedLimit"] == "90%"


@pytest.mark.parametrize("key", _KEYS)
def test_accepts_a_numeric_value(key):
    """`{"LogLevel": 4}` is the natural way to write it in JSON."""
    m = JsonModel(**{key: {"RuntimeWatchdogSec": 30}})
    assert getattr(m, key)["RuntimeWatchdogSec"] == "30"


@pytest.mark.parametrize("bad", ["Bad Key", "Key=Value", "[OOM]", "", "2Fast",
                                 "Key\nOther"])
def test_rejects_a_key_that_is_not_a_directive_name(bad):
    with pytest.raises(ValueError):
        JsonModel(oomd={bad: "1"})


@pytest.mark.parametrize("bad", ["20s\nSwapUsedLimit=1%", "20s\n[Manager]"])
def test_rejects_a_value_that_would_forge_another_directive(bad):
    with pytest.raises(ValueError):
        JsonModel(oomd={"DefaultMemoryPressureDurationSec": bad})


def test_rejects_a_value_that_is_not_a_scalar():
    with pytest.raises(ValueError):
        JsonModel(oomd={"SwapUsedLimit": ["90%"]})

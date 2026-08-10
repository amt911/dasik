import pytest
from pydantic import ValidationError

from dasik.lib.models.json_model import JsonModel
from dasik.lib.models.reflector_model import ReflectorModel


def test_defaults():
    m = ReflectorModel()
    assert m.countries == []
    assert m.protocols == ["https"]
    assert m.latest == 20
    assert m.sort == "rate"
    assert m.save == "/etc/pacman.d/mirrorlist"


def test_rejects_an_unknown_protocol():
    with pytest.raises(ValidationError):
        ReflectorModel(protocols=["carrier-pigeon"])


def test_rejects_a_non_positive_latest():
    with pytest.raises(ValidationError):
        ReflectorModel(latest=0)


def test_rejects_a_country_with_a_newline():
    with pytest.raises(ValidationError):
        ReflectorModel(countries=["ES\n--save /etc/passwd"])


def test_json_model_accepts_the_block():
    cfg = JsonModel(**{"reflector": {"countries": ["ES"]}})
    assert cfg.reflector is not None and cfg.reflector.countries == ["ES"]

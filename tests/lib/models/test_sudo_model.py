import pytest
from pydantic import ValidationError

from dasik.lib.models.sudo_model import SudoModel
from dasik.lib.models.json_model import JsonModel


def test_defaults_grant_wheel_with_password():
    m = SudoModel()
    assert m.wheel is True
    assert m.nopasswd is False
    assert m.rules == []


def test_rules_are_kept_verbatim_and_in_order():
    m = SudoModel(rules=["andres ALL=(ALL) NOPASSWD: /usr/bin/pacman",
                         "%docker ALL=(ALL) NOPASSWD: /usr/bin/docker"])
    assert m.rules[0].startswith("andres ")
    assert m.rules[1].startswith("%docker ")


@pytest.mark.parametrize("bad", [
    "andres ALL=(ALL) ALL\n%wheel ALL=(ALL) NOPASSWD: ALL",   # smuggled second line
    "andres ALL=(ALL) ALL\rroot ALL=(ALL) ALL",
    "   ",
    "@includedir /etc/sudoers.d",
    "#include /tmp/evil",
])
def test_rejects_multiline_blank_and_include_rules(bad):
    with pytest.raises(ValidationError):
        SudoModel(rules=[bad])


def test_json_model_accepts_a_sudo_block():
    cfg = JsonModel(**{"sudo": {"wheel": True, "nopasswd": False, "rules": []}})
    assert cfg.sudo is not None and cfg.sudo.wheel is True


def test_json_model_sudo_defaults_to_none():
    assert JsonModel().sudo is None

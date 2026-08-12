"""The `apparmor` block.

Declaring the block IS the declaration — `enable` defaults to true, so an empty
`"apparmor": {}` means "protect this machine". `enable: false` exists so the
block can stay in the config while turned off, which is also what `sync` reports
for a machine that has the package but never made it the active LSM.
"""
import pytest
from pydantic import ValidationError

from dasik.lib.models.apparmor_model import ApparmorModel


def test_an_empty_block_means_enabled_without_audit():
    model = ApparmorModel()
    assert model.enable is True
    assert model.audit is False
    assert model.extra_profiles == []


def test_audit_is_opt_in():
    assert ApparmorModel(audit=True).audit is True


def test_profiles_are_named_and_carry_content():
    model = ApparmorModel(extra_profiles=[
        {"name": "usr.bin.foo", "content": "profile foo {}\n"}])
    assert model.extra_profiles[0].name == "usr.bin.foo"
    assert model.extra_profiles[0].content == "profile foo {}\n"


@pytest.mark.parametrize("name", ["../../etc/passwd", "sub/dir", "with space", ""])
def test_a_profile_name_cannot_escape_the_profile_directory(name):
    with pytest.raises(ValidationError, match="profile name"):
        ApparmorModel(extra_profiles=[{"name": name, "content": ""}])

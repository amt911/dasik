import pytest

from dasik.lib.models.package_model import PackageSpec
from dasik.lib.models.json_model import JsonModel


def _base(**extra):
    return JsonModel(
        locales={"selected_locales": [], "desired_locale": "en_US.UTF-8",
                 "desired_tty_layout": "us"},
        timezone={"region": "Europe", "city": "Madrid"},
        network={"type": "NetworkManager", "add_default_hosts": True},
        hostname="arch",
        **extra,
    )


def test_packagespec_defaults_to_explicit():
    assert PackageSpec(name="git").reason == "explicit"


def test_packagespec_accepts_dep():
    assert PackageSpec(name="foo", reason="dep").reason == "dep"


def test_packagespec_rejects_bad_reason():
    with pytest.raises(ValueError):
        PackageSpec(name="foo", reason="weird")


def test_json_model_packages_accepts_str_and_object():
    m = _base(packages=["git", {"name": "foo", "reason": "dep"}, "aur-yay"])
    assert m.packages[0] == "git"
    assert m.packages[1].name == "foo" and m.packages[1].reason == "dep"
    assert m.packages[2] == "aur-yay"

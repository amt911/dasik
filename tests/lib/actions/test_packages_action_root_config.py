"""PackagesAction reads root config (packages + package_sources + package_policy).

PLAN v3 §5: the action is registered as ``__root__`` so it can read the sibling
``package_sources`` and ``package_policy`` maps, not just the packages list.
A plain list is still accepted for back-compat with existing call-sites/tests.
"""
from dasik.lib.actions.packages_action import PackagesAction
from dasik.lib.actions.actions_handler_v2 import setup_actions
from dasik.lib.actions.action_registry import get_default_registry


_SRC = {
    "type": "pkgbuild-git",
    "url": "https://github.com/amt911/config-saver-aur.git",
    "ref": "a520605367e13ec25db4c3c7e1c4bf46175ba8cd",
    "subdir": ".",
}


def test_root_dict_config_populates_desired():
    a = PackagesAction(config={"packages": ["git", "config-saver"]})
    assert a.desired == ["git", "config-saver"]


def test_root_dict_reads_package_sources():
    a = PackagesAction(config={
        "packages": ["config-saver"],
        "package_sources": {"config-saver": _SRC},
    })
    assert a.package_sources == {"config-saver": _SRC}


def test_root_dict_reads_package_policy():
    a = PackagesAction(config={
        "packages": ["git"],
        "package_policy": {"unknown": "error"},
    })
    assert a.unknown_policy == "error"


def test_root_dict_defaults_policy_warn_and_skip():
    a = PackagesAction(config={"packages": ["git"]})
    assert a.unknown_policy == "warn-and-skip"
    assert a.package_sources == {}


def test_list_config_still_supported():
    a = PackagesAction(config=["git", "htop"])
    assert a.desired == ["git", "htop"]
    assert a.package_sources == {}
    assert a.unknown_policy == "warn-and-skip"


def test_empty_config_is_constructible():
    a = PackagesAction(config=PackagesAction.empty_config())
    assert a.desired == []


def test_registered_as_root():
    setup_actions()
    metas = get_default_registry().get_all_actions()
    pkg = [m for m in metas if m["class"] is PackagesAction]
    assert len(pkg) == 1
    assert pkg[0]["config_key"] == "__root__"

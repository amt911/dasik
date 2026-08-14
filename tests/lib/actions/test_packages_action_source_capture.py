"""`sync` must capture `package_sources`, not just the package name.

A package built from a Git PKGBUILD exists in no pacman repo and in no AUR. If
`import_state` reports only its name, the captured config re-plans into
"unknown package" and warn-and-skip drops it — the feature converges one way
and evaporates on the way back.
"""
from unittest.mock import MagicMock

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.packages_action import PackagesAction
from dasik.lib.target.target import Target


_SHA = "a520605367e13ec25db4c3c7e1c4bf46175ba8cd"
_SRC = {"type": "pkgbuild-git",
        "url": "https://github.com/amt911/config-saver-aur.git",
        "ref": _SHA, "subdir": "."}


def _action(config, manifest=None, installed=("config-saver", "git"), explicit=None):
    a = PackagesAction(
        config=config,
        context=ActionContext(target=Target(root="/"), manifest=manifest),
    )
    a._installed_all = MagicMock(return_value=set(installed))          # type: ignore
    a.actual = MagicMock(return_value=set(installed if explicit is None else explicit))  # type: ignore
    a._unit_provider_packages = MagicMock(return_value=set())          # type: ignore
    return a


def test_declared_source_survives_a_sync():
    a = _action({"packages": ["config-saver", "git"],
                 "package_sources": {"config-saver": _SRC}})
    assert a.import_state([])["package_sources"] == {"config-saver": _SRC}


def test_source_recovered_from_the_manifest_with_an_empty_seed():
    # The bootstrap case: `sync` on a machine dasik installed, from `{}`.
    manifest = {"managed": {"packages": ["config-saver"]},
                "action_state": {"packages": {"sources": {"config-saver": _SRC}}}}
    a = _action({}, manifest=manifest)
    out = a.import_state(["config-saver"])
    assert out["package_sources"] == {"config-saver": _SRC}
    assert "config-saver" in out["packages"]


def test_declared_source_wins_over_the_recorded_one():
    # The config is intent: a ref the admin just bumped must not be overwritten
    # by the SHA the last apply happened to build.
    old = dict(_SRC, ref="b" * 40)
    manifest = {"managed": {"packages": ["config-saver"]},
                "action_state": {"packages": {"sources": {"config-saver": old}}}}
    a = _action({"packages": ["config-saver"], "package_sources": {"config-saver": _SRC}},
                manifest=manifest)
    assert a.import_state(["config-saver"])["package_sources"]["config-saver"]["ref"] == _SHA


def test_nothing_invented_for_a_package_that_is_not_installed():
    manifest = {"managed": {"packages": ["config-saver"]},
                "action_state": {"packages": {"sources": {"config-saver": _SRC}}}}
    a = _action({}, manifest=manifest, installed=("git",))
    assert "package_sources" not in a.import_state(["config-saver"])


def test_no_key_at_all_on_a_machine_with_no_git_sources():
    a = _action({"packages": ["git"]}, installed=("git",))
    assert "package_sources" not in a.import_state([])


def test_legacy_manifest_with_only_refs_captures_nothing_it_cannot_prove():
    # source_refs holds a SHA and no URL — a source cannot be reconstructed from
    # it, and inventing one would produce a config that fails to build.
    manifest = {"managed": {"packages": ["config-saver"]},
                "action_state": {"packages": {"source_refs": {"config-saver": _SHA}}}}
    a = _action({}, manifest=manifest)
    assert "package_sources" not in a.import_state(["config-saver"])

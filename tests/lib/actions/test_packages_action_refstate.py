"""Git source-ref idempotency in PackagesAction (PLAN v3 §10).

A changed ``ref`` must trigger a rebuild even when the package name is already
installed; an unchanged ref must be a no-op. The applied SHA is tracked in the
manifest's ``action_state["packages"]["source_refs"]`` via ``state_metadata()``.
"""
from unittest.mock import MagicMock, patch

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.packages_action import PackagesAction
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target


_SHA = "a520605367e13ec25db4c3c7e1c4bf46175ba8cd"
_OLD = "b" * 40
_SRC = {"type": "pkgbuild-git",
        "url": "https://github.com/amt911/config-saver-aur.git",
        "ref": _SHA, "subdir": "."}


def _action(manifest=None, installed=("config-saver",)):
    a = PackagesAction(
        config={"packages": ["config-saver", "git"], "package_sources": {"config-saver": _SRC}},
        context=ActionContext(target=Target(root="/"), manifest=manifest),
    )
    a._installed_all = MagicMock(return_value=set(installed))  # type: ignore
    a.actual = MagicMock(return_value=set(installed))          # type: ignore
    return a


def _ok():
    return MagicMock(returncode=0, stdout=b"", stderr=b"")


# --- state_metadata -------------------------------------------------------

def test_state_metadata_records_installed_git_ref():
    a = _action(installed=("config-saver", "git"))
    assert a.state_metadata()["packages"]["source_refs"] == {"config-saver": _SHA}


def test_state_metadata_records_the_whole_source():
    # The ref alone cannot rebuild anything: sync needs the URL back too, or a
    # captured config drops a package that exists in no repo and no AUR.
    a = _action(installed=("config-saver", "git"))
    assert a.state_metadata()["packages"]["sources"] == {"config-saver": _SRC}


def test_state_metadata_empty_when_git_pkg_not_installed():
    a = _action(installed=("git",))
    assert a.state_metadata() == {}


def test_plan_modify_from_legacy_manifest_without_sources():
    # A manifest written before `sources` existed still answers the ref question.
    manifest = {"managed": {"packages": ["config-saver"]},
                "action_state": {"packages": {"source_refs": {"config-saver": _SHA}}}}
    a = _action(manifest=manifest, installed=("config-saver", "git"))
    assert _ref_modifies(a.plan(managed=["config-saver", "git"])) == []


def test_plan_modify_reads_ref_from_sources_when_refs_absent():
    manifest = {"managed": {"packages": ["config-saver"]},
                "action_state": {"packages": {"sources": {"config-saver": _SRC}}}}
    a = _action(manifest=manifest, installed=("config-saver", "git"))
    assert _ref_modifies(a.plan(managed=["config-saver", "git"])) == []


# --- plan MODIFY on ref change --------------------------------------------

def _ref_modifies(changes):
    return [c for c in changes if c.op is Op.MODIFY and "source ref" in c.reason]


def test_plan_modify_when_applied_ref_differs():
    manifest = {"managed": {"packages": ["config-saver", "git"]},
                "action_state": {"packages": {"source_refs": {"config-saver": _OLD}}}}
    a = _action(manifest=manifest, installed=("config-saver", "git"))
    mods = _ref_modifies(a.plan(managed=["config-saver", "git"]))
    assert [c.item for c in mods] == ["config-saver"]


def test_plan_no_modify_when_ref_matches():
    manifest = {"managed": {"packages": ["config-saver", "git"]},
                "action_state": {"packages": {"source_refs": {"config-saver": _SHA}}}}
    a = _action(manifest=manifest, installed=("config-saver", "git"))
    assert _ref_modifies(a.plan(managed=["config-saver", "git"])) == []


def test_plan_modify_when_no_recorded_ref():
    manifest = {"managed": {"packages": ["config-saver", "git"]}, "action_state": {}}
    a = _action(manifest=manifest, installed=("config-saver", "git"))
    mods = _ref_modifies(a.plan(managed=["config-saver", "git"]))
    assert [c.item for c in mods] == ["config-saver"]


def test_plan_no_ref_modify_when_not_installed_yet():
    # not installed -> it's an INSTALL, never a ref MODIFY
    manifest = {"managed": {}, "action_state": {}}
    a = _action(manifest=manifest, installed=("git",))
    changes = a.plan(managed=[])
    assert _ref_modifies(changes) == []
    assert any(c.op is Op.INSTALL and c.item == "config-saver" for c in changes)


# --- apply rebuilds on ref MODIFY -----------------------------------------

def test_apply_ref_modify_rebuilds_via_installer():
    a = _action(installed=("config-saver", "git"))
    change = Change("packages", Op.MODIFY, "config-saver", reason="source ref changed")
    with patch("dasik.lib.actions.packages_action.Command.execute", MagicMock(return_value=_ok())), \
         patch("dasik.lib.actions.pkgbuild_git_installer.PkgbuildGitInstaller.install") as install:
        a.apply([change])
    install.assert_called_once()
    built = install.call_args.args[0]
    assert [p.name for p in built] == ["config-saver"]
    assert built[0].source == _SRC


def test_apply_ref_modify_is_not_treated_as_reason_change():
    a = _action(installed=("config-saver", "git"))
    change = Change("packages", Op.MODIFY, "config-saver", reason="source ref changed")
    fake = MagicMock(return_value=_ok())
    with patch("dasik.lib.actions.packages_action.Command.execute", fake), \
         patch("dasik.lib.actions.pkgbuild_git_installer.PkgbuildGitInstaller.install"):
        a.apply([change])
    # no `pacman -D` reason change fired for a ref rebuild
    assert not any(c.args[0] == "pacman" and c.args[1][:1] == ["-D"] for c in fake.call_args_list)

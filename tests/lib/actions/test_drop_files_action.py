from unittest.mock import mock_open, patch

from dasik.lib.actions.drop_files_action import DropFilesAction, _sha256
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Change, Op


def _ctx(root="/"):
    return ActionContext(target=Target(root=root))


def _cfg(udev=None, modprobe=None, profile=None, env=None):
    return {
        "udev_rules": udev or [],
        "modprobe_conf": modprobe or [],
        "profile_d": profile or [],
        "etc_environment": env or [],
    }


def test_sha256_deterministic():
    assert _sha256("x") == _sha256("x")


def test_desired_maps_sections_to_canonical_paths():
    a = DropFilesAction(_cfg(
        udev=[{"name": "99-x.rules", "content": "RULE"}],
        modprobe=[{"name": "x.conf", "content": "options x"}],
        profile=[{"name": "x.sh", "content": "export A=1"}],
        env=["EDITOR=vim", "PAGER=less"],
    ), _ctx("/"))
    d = a._desired()
    assert d["/etc/udev/rules.d/99-x.rules"] == "RULE"
    assert d["/etc/modprobe.d/x.conf"] == "options x"
    assert d["/etc/profile.d/x.sh"] == "export A=1"
    assert d["/etc/environment"] == "EDITOR=vim\nPAGER=less\n"


def test_desired_omits_environment_when_no_lines():
    a = DropFilesAction(_cfg(udev=[{"name": "a.rules", "content": "R"}]), _ctx("/"))
    assert "/etc/environment" not in a._desired()


def test_abs_resolves_through_target():
    a = DropFilesAction(_cfg(), _ctx("/mnt"))
    assert a._abs("/etc/environment") == "/mnt/etc/environment"
    b = DropFilesAction(_cfg(), _ctx("/"))
    assert b._abs("/etc/environment") == "/etc/environment"


def test_actual_returns_declared_paths_that_exist():
    a = DropFilesAction(_cfg(
        udev=[{"name": "a.rules", "content": "R"}, {"name": "b.rules", "content": "R2"}],
    ), _ctx("/"))
    exists = {"/etc/udev/rules.d/a.rules"}
    with patch("dasik.lib.actions.drop_files_action.os.path.exists",
               side_effect=lambda p: p in exists):
        assert a.actual() == {"/etc/udev/rules.d/a.rules"}


def test_actual_empty_without_target():
    a = DropFilesAction(_cfg(udev=[{"name": "a.rules", "content": "R"}]), None)
    assert a.actual() == set()


# --- legacy is_needed / execute / verify (migrated to {name,content}) --- #


def test_legacy_needed_when_file_absent():
    a = DropFilesAction(_cfg(udev=[{"name": "a.rules", "content": "R"}]), _ctx("/"))
    with patch("dasik.lib.actions.drop_files_action.os.path.exists", return_value=False):
        assert a.is_needed() is True


def test_legacy_not_needed_when_content_matches():
    a = DropFilesAction(_cfg(udev=[{"name": "a.rules", "content": "R"}]), _ctx("/"))
    with patch("dasik.lib.actions.drop_files_action.os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data="R")):
        assert a.is_needed() is False
        assert a.verify() is True


def test_legacy_needed_when_content_differs():
    a = DropFilesAction(_cfg(udev=[{"name": "a.rules", "content": "NEW"}]), _ctx("/"))
    with patch("dasik.lib.actions.drop_files_action.os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data="OLD")):
        assert a.is_needed() is True


def test_name_and_optional():
    a = DropFilesAction(_cfg())
    assert a.name == "Drop Config Files"
    assert a.is_optional is True


# ---------------------------------------------------------------------- #
#  Task 3: plan() + managed_keys()                                        #
# ---------------------------------------------------------------------- #


def _v3(cfg, actual, ondisk=None):
    a = DropFilesAction(cfg, _ctx("/"))
    a.actual = lambda: set(actual)
    a._read = lambda p: (ondisk or {}).get(p, "")
    return a


def test_plan_creates_missing_file():
    a = _v3(_cfg(udev=[{"name": "a.rules", "content": "R"}]), actual=[])
    changes = a.plan(managed=[])
    assert [(c.op, c.item) for c in changes] == [(Op.CREATE, "/etc/udev/rules.d/a.rules")]


def test_plan_deletes_orphan_owned():
    a = _v3(_cfg(), actual=[])
    changes = a.plan(managed=["/etc/modprobe.d/old.conf"])
    assert [(c.op, c.item) for c in changes] == [(Op.DELETE, "/etc/modprobe.d/old.conf")]


def test_plan_modifies_on_content_drift():
    p = "/etc/udev/rules.d/a.rules"
    a = _v3(_cfg(udev=[{"name": "a.rules", "content": "NEW"}]),
            actual=[p], ondisk={p: "OLD"})
    changes = a.plan(managed=[p])
    assert [(c.op, c.item) for c in changes] == [(Op.MODIFY, p)]


def test_plan_empty_when_converged():
    p = "/etc/udev/rules.d/a.rules"
    a = _v3(_cfg(udev=[{"name": "a.rules", "content": "R"}]),
            actual=[p], ondisk={p: "R"})
    assert a.plan(managed=[p]) == []


def test_managed_keys_lists_canonical_paths():
    a = DropFilesAction(_cfg(
        udev=[{"name": "a.rules", "content": "R"}], env=["X=1"]), _ctx("/"))
    assert a.managed_keys() == {"files": ["/etc/environment", "/etc/udev/rules.d/a.rules"]}

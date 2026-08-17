from unittest.mock import MagicMock, mock_open, patch

from dasik.lib.actions.drop_files_action import DropFilesAction, _sha256
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Change, Op
from dasik.lib.logging import run_logger


def _ctx(root="/"):
    return ActionContext(target=Target(root=root))


def _quiet_logger():
    """Neutralise the process-wide run logger for a test that plans.

    `is_needed()` goes through `plan()` now (issue #238), and this domain warns
    there. The logger is a singleton holding a stream, so under a different test
    ORDER — mutmut runs the suite in its own copy — that stream can already be
    closed, and the warning dies with "I/O operation on closed file". The
    warning is not what these two are about.
    """
    return patch.object(run_logger, "get", return_value=MagicMock())


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
    with _quiet_logger(), \
         patch("dasik.lib.actions.drop_files_action.os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data="R")):
        assert a.is_needed() is False
        assert a.verify() is True


def test_legacy_needed_when_content_differs():
    a = DropFilesAction(_cfg(udev=[{"name": "a.rules", "content": "NEW"}]), _ctx("/"))
    with _quiet_logger(), \
         patch("dasik.lib.actions.drop_files_action.os.path.exists", return_value=True), \
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
    # These import_state tests exercise the declared-refresh path only; disable
    # live directory/crypttab discovery so they don't read the real /etc.
    a._discover_section = lambda directory: []
    a._discover_crypttab = lambda: None
    # …and _discover_paths, which arrived later with the sshd/samba capture and
    # was never added here. The context is root="/", so it walks the REAL
    # /etc/ssh/sshd_config.d and /etc/samba: these tests then passed or failed
    # according to what the machine running them happened to have installed.
    # Green on a developer box with samba, red on a CI runner whose sshd drop-in
    # says "…Authentication no" — and red on main for exactly that reason.
    a._discover_paths = lambda: []
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


# ---------------------------------------------------------------------- #
#  Task 4: apply()                                                        #
# ---------------------------------------------------------------------- #


def test_apply_writes_created_and_modified_files():
    a = DropFilesAction(_cfg(
        udev=[{"name": "a.rules", "content": "R"}],
        modprobe=[{"name": "b.conf", "content": "B"}]), _ctx("/"))
    m = mock_open()
    changes = [
        Change("files", Op.CREATE, "/etc/udev/rules.d/a.rules"),
        Change("files", Op.MODIFY, "/etc/modprobe.d/b.conf"),
    ]
    with patch("dasik.lib.actions.drop_files_action.os.makedirs") as mkdirs, \
         patch("builtins.open", m):
        a.apply(changes)
    written = {c.args[0] for c in m.call_args_list}
    assert "/etc/udev/rules.d/a.rules" in written
    assert "/etc/modprobe.d/b.conf" in written
    assert mkdirs.call_count == 2
    bodies = "".join(c.args[0] for c in m().write.call_args_list)
    assert "R" in bodies and "B" in bodies


def test_apply_removes_orphan_files():
    a = DropFilesAction(_cfg(), _ctx("/"))
    with patch("dasik.lib.actions.drop_files_action.os.path.exists", return_value=True), \
         patch("dasik.lib.actions.drop_files_action.os.remove") as rm:
        a.apply([Change("files", Op.DELETE, "/etc/modprobe.d/old.conf")])
    rm.assert_called_once_with("/etc/modprobe.d/old.conf")


def test_apply_delete_skips_missing_file():
    a = DropFilesAction(_cfg(), _ctx("/"))
    with patch("dasik.lib.actions.drop_files_action.os.path.exists", return_value=False), \
         patch("dasik.lib.actions.drop_files_action.os.remove") as rm:
        a.apply([Change("files", Op.DELETE, "/etc/modprobe.d/old.conf")])
    rm.assert_not_called()


def test_apply_create_before_delete():
    a = DropFilesAction(_cfg(udev=[{"name": "a.rules", "content": "R"}]), _ctx("/"))
    changes = [
        Change("files", Op.DELETE, "/etc/modprobe.d/old.conf"),
        Change("files", Op.CREATE, "/etc/udev/rules.d/a.rules"),
    ]
    order = []
    with patch("dasik.lib.actions.drop_files_action.os.makedirs",
               side_effect=lambda *a_, **k: order.append("write")), \
         patch("builtins.open", mock_open()), \
         patch("dasik.lib.actions.drop_files_action.os.path.exists", return_value=True), \
         patch("dasik.lib.actions.drop_files_action.os.remove",
               side_effect=lambda p: order.append("del")):
        a.apply(changes)
    assert order == ["write", "del"]


def test_apply_noop_without_target():
    a = DropFilesAction(_cfg(udev=[{"name": "a.rules", "content": "R"}]), None)
    with patch("builtins.open", mock_open()) as m, \
         patch("dasik.lib.actions.drop_files_action.os.remove") as rm:
        a.apply([Change("files", Op.CREATE, "/etc/udev/rules.d/a.rules")])
    m.assert_not_called()
    rm.assert_not_called()


# ---------------------------------------------------------------------- #
#  Task 5: import_state() (sync)                                          #
# ---------------------------------------------------------------------- #


def test_import_state_refreshes_content_from_disk():
    p = "/etc/udev/rules.d/a.rules"
    a = _v3(_cfg(udev=[{"name": "a.rules", "content": "OLD"}]),
            actual=[p], ondisk={p: "EDITED-ON-DISK"})
    frag = a.import_state(managed=[p])
    assert frag["udev_rules"] == [{"name": "a.rules", "content": "EDITED-ON-DISK"}]


def test_import_state_keeps_declared_content_when_absent():
    a = _v3(_cfg(profile=[{"name": "x.sh", "content": "export A=1"}]), actual=[])
    frag = a.import_state(managed=[])
    assert frag["profile_d"] == [{"name": "x.sh", "content": "export A=1"}]


def test_import_state_splits_environment_back_to_lines():
    p = "/etc/environment"
    a = _v3(_cfg(env=["A=1", "B=2"]), actual=[p], ondisk={p: "A=1\nB=2\nC=3\n"})
    frag = a.import_state(managed=[p])
    assert frag["etc_environment"] == ["A=1", "B=2", "C=3"]


# ---------------------------------------------------------------------- #
#  Arbitrary /etc paths (files section) — Plan 13                         #
# ---------------------------------------------------------------------- #


def _cfg_files(entries):
    c = _cfg()
    c["files"] = entries
    return c


def test_desired_includes_arbitrary_paths():
    a = DropFilesAction(_cfg_files(
        [{"path": "/etc/ssh/sshd_config.d/99-dasik.conf", "content": "X11Forwarding no"}]),
        _ctx("/"))
    assert a._desired()["/etc/ssh/sshd_config.d/99-dasik.conf"] == "X11Forwarding no"


def test_plan_creates_arbitrary_path():
    a = _v3(_cfg_files([{"path": "/etc/samba/smb.conf", "content": "[global]"}]), actual=[])
    changes = a.plan(managed=[])
    assert [(c.op, c.item) for c in changes] == [(Op.CREATE, "/etc/samba/smb.conf")]


def test_plan_modify_on_arbitrary_path_drift():
    p = "/etc/ssh/sshd_config.d/99-dasik.conf"
    a = _v3(_cfg_files([{"path": p, "content": "NEW"}]), actual=[p], ondisk={p: "OLD"})
    changes = a.plan(managed=[p])
    assert [(c.op, c.item) for c in changes] == [(Op.MODIFY, p)]


def test_managed_keys_includes_arbitrary_path():
    a = DropFilesAction(_cfg_files([{"path": "/etc/samba/smb.conf", "content": "x"}]), _ctx("/"))
    assert "/etc/samba/smb.conf" in a.managed_keys()["files"]


def test_import_state_rebuilds_files_section_from_disk():
    p = "/etc/samba/smb.conf"
    a = _v3(_cfg_files([{"path": p, "content": "OLD"}]), actual=[p], ondisk={p: "EDITED"})
    frag = a.import_state(managed=[p])
    assert frag["files"] == [{"path": p, "content": "EDITED"}]


def test_import_state_keeps_declared_files_content_when_absent():
    p = "/etc/samba/smb.conf"
    a = _v3(_cfg_files([{"path": p, "content": "DECLARED"}]), actual=[])
    frag = a.import_state(managed=[])
    assert frag["files"] == [{"path": p, "content": "DECLARED"}]

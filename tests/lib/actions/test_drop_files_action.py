from unittest.mock import mock_open, patch

from dasik.lib.actions.drop_files_action import DropFilesAction, _sha256


def test_sha256_deterministic():
    assert _sha256("x") == _sha256("x")


def test_plan_maps_each_section_to_files():
    a = DropFilesAction({
        "udev_rules": ["RULE1", "RULE2"],
        "modprobe_conf": ["options x"],
        "profile_d": ["export A=1"],
        "etc_environment": ["EDITOR=vim", "PAGER=less"],
    })
    plan = dict(a._plan())
    assert "/mnt/etc/udev/rules.d/99-dasik-01.rules" in plan
    assert "/mnt/etc/udev/rules.d/99-dasik-02.rules" in plan
    assert plan["/mnt/etc/modprobe.d/dasik-01.conf"] == "options x\n"
    assert plan["/mnt/etc/profile.d/dasik-01.sh"] == "export A=1\n"
    # etc/environment joins all lines into one file
    assert plan["/mnt/etc/environment"] == "EDITOR=vim\nPAGER=less\n"


def test_empty_config_has_empty_plan():
    a = DropFilesAction({})
    assert a._plan() == []
    assert a.is_needed() is False


def test_needs_write_true_when_file_absent():
    a = DropFilesAction({})
    with patch("dasik.lib.actions.drop_files_action.os.path.exists", return_value=False):
        assert a._needs_write("/mnt/x", "content") is True


def test_needs_write_false_when_content_matches():
    a = DropFilesAction({})
    with patch("dasik.lib.actions.drop_files_action.os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data="content")):
        assert a._needs_write("/mnt/x", "content") is False


def test_needs_write_true_when_content_differs():
    a = DropFilesAction({})
    with patch("dasik.lib.actions.drop_files_action.os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data="old")):
        assert a._needs_write("/mnt/x", "new") is True


def test_is_needed_and_verify_track_plan():
    a = DropFilesAction({"udev_rules": ["R"]})
    with patch("dasik.lib.actions.drop_files_action.os.path.exists", return_value=False):
        assert a.is_needed() is True
        assert a.verify() is False
    with patch("dasik.lib.actions.drop_files_action.os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data="R\n")):
        assert a.is_needed() is False
        assert a.verify() is True


def test_execute_writes_only_files_that_need_writing():
    a = DropFilesAction({"udev_rules": ["R"], "modprobe_conf": ["M"]})
    m = mock_open()
    # udev file already correct, modprobe file missing → only modprobe written
    def needs(path, content):
        return "modprobe" in path
    with patch.object(DropFilesAction, "_needs_write", side_effect=needs), \
         patch("dasik.lib.actions.drop_files_action.os.makedirs") as mkdirs, \
         patch("builtins.open", m):
        a.execute()
    written_paths = [c.args[0] for c in m.call_args_list]
    assert any("modprobe" in p for p in written_paths)
    assert not any("udev" in p for p in written_paths)
    mkdirs.assert_called_once()


def test_name_and_optional():
    a = DropFilesAction({})
    assert a.name == "Drop Config Files"
    assert a.is_optional is True

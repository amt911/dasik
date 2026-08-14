"""The CLI expands `etc_tree` before anything else looks at the config.

Same contract as the include directives: `check`, `plan` and `apply` see the
finished thing. An action must never learn that a `files` entry came from a
directory rather than from the JSON.
"""
import json

import dasik.__main__ as m


def _config(tmp_path, extra=None):
    (tmp_path / "etc" / "pam.d").mkdir(parents=True)
    (tmp_path / "etc" / "pam.d" / "sudo").write_text("auth required pam_unix.so\n")
    (tmp_path / "etc" / "profile.d").mkdir()
    script = tmp_path / "etc" / "profile.d" / "dasik.sh"
    script.write_text("export EDITOR=vim\n")
    script.chmod(0o755)

    main = tmp_path / "main.json"
    main.write_text(json.dumps({"etc_tree": "etc", **(extra or {})}))
    return main


def test_the_loader_turns_the_tree_into_files(tmp_path):
    config = m._load_validated_config(_config(tmp_path))

    by_path = {e["path"]: e for e in config["files"]}
    assert set(by_path) == {"/etc/pam.d/sudo", "/etc/profile.d/dasik.sh"}
    assert by_path["/etc/pam.d/sudo"]["content"] == "auth required pam_unix.so\n"
    assert by_path["/etc/profile.d/dasik.sh"]["mode"] == "0755"


def test_check_accepts_a_config_that_uses_a_tree(tmp_path, capsys):
    assert m._cmd_check(_config(tmp_path)) == 0
    assert "OK" in capsys.readouterr().out


def test_a_broken_tree_is_reported_against_the_config(tmp_path, capsys):
    main = tmp_path / "main.json"
    main.write_text(json.dumps({"etc_tree": "missing"}))

    assert m._cmd_check(main) == 1
    assert "missing" in capsys.readouterr().err


def test_sync_extracts_a_captured_etc_file_into_the_tree(tmp_path, capsys,
                                                         monkeypatch):
    """The capture must grow the tree, not the JSON."""
    from tests.lib.test_main_includes import _stub_capture

    main = _config(tmp_path)
    _stub_capture(monkeypatch, {
        "etc_tree": "etc",
        "files": [
            {"path": "/etc/pam.d/sudo", "content": "auth sufficient pam_unix.so\n"},
            {"path": "/etc/profile.d/dasik.sh", "content": "export EDITOR=vim\n",
             "mode": "0755"},
            {"path": "/etc/vconsole.conf", "content": "KEYMAP=es\n"},
        ]})

    assert m._cmd_sync(main, "/") == 0

    # bodies in the tree, not in the JSON
    assert (tmp_path / "etc" / "pam.d" / "sudo").read_text() == \
        "auth sufficient pam_unix.so\n"
    assert (tmp_path / "etc" / "vconsole.conf").read_text() == "KEYMAP=es\n"
    assert json.loads(main.read_text())["files"] == []
    # and the executable bit is carried by the file itself
    assert (tmp_path / "etc" / "profile.d" / "dasik.sh").stat().st_mode & 0o111


def test_sync_removes_a_tree_file_the_machine_no_longer_has(tmp_path, monkeypatch):
    from tests.lib.test_main_includes import _stub_capture

    main = _config(tmp_path)
    _stub_capture(monkeypatch, {
        "etc_tree": "etc",
        "files": [{"path": "/etc/pam.d/sudo", "content": "auth required pam_unix.so\n"}]})

    assert m._cmd_sync(main, "/") == 0

    assert not (tmp_path / "etc" / "profile.d" / "dasik.sh").exists()


def test_the_schema_accepts_the_two_new_keys(tmp_path, capsys):
    """They survive validation: the config still carries the tree after
    expansion, because `sync` has to know it exists to write back into it."""
    main = _config(tmp_path, {"etc_tree_modes": {"pam.d/sudo": "0600"}})

    assert m._cmd_check(main) == 0
    config = m._load_validated_config(main)
    assert config["etc_tree"] == "etc"
    assert {e["path"]: e.get("mode") for e in config["files"]}["/etc/pam.d/sudo"] == "0600"

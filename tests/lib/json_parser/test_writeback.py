"""Writing a captured config back THROUGH its `$include` directives.

`sync` rewrites the file it is given. Until now it refused to touch a config
assembled from several files, because the only writer emitted one document and
every directive would have been replaced by its resolved value — the split
silently undone. That refusal made the documented way of keeping a password
hash out of the committed config (`$include_line`) disable `sync` forever.

The writeback puts each value back in the file it came from. The rule that
makes it safe: a directive whose resolved value did not change is left alone,
and its file is not opened for writing at all.
"""
import json

import pytest

from dasik.lib.json_parser.writeback import write_back


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


# --- no directives: behave exactly like the old writer ---------------------- #

def test_config_without_directives_is_written_whole(tmp_path):
    root = _write(tmp_path / "c.json", {"hostname": "old"})

    written = write_back(root, {"hostname": "new"})

    assert written == [root]
    assert json.loads(root.read_text()) == {"hostname": "new"}


def test_unchanged_config_writes_nothing(tmp_path):
    root = _write(tmp_path / "c.json", {"hostname": "same"})
    before = root.stat().st_mtime_ns

    written = write_back(root, {"hostname": "same"})

    assert written == []
    assert root.stat().st_mtime_ns == before


# --- $include --------------------------------------------------------------- #

def test_unchanged_include_is_left_alone(tmp_path):
    _write(tmp_path / "pkgs.json", ["firefox"])
    root = _write(tmp_path / "c.json", {"packages": {"$include": "pkgs.json"}})

    written = write_back(root, {"packages": ["firefox"]})

    assert written == []
    # the directive survives, byte for byte
    assert json.loads(root.read_text()) == {"packages": {"$include": "pkgs.json"}}


def test_changed_include_is_written_to_the_included_file(tmp_path):
    inc = _write(tmp_path / "pkgs.json", ["firefox"])
    root = _write(tmp_path / "c.json", {"packages": {"$include": "pkgs.json"}})

    written = write_back(root, {"packages": ["firefox", "vim"]})

    assert written == [inc]
    assert json.loads(inc.read_text()) == ["firefox", "vim"]
    assert json.loads(root.read_text()) == {"packages": {"$include": "pkgs.json"}}


def test_include_nested_in_the_included_file_is_preserved(tmp_path):
    _write(tmp_path / "frag" / "base.json", ["base"])
    inc = _write(tmp_path / "frag" / "pkgs.json",
                 {"a": {"$include": "base.json"}, "b": ["old"]})
    root = _write(tmp_path / "c.json", {"blob": {"$include": "frag/pkgs.json"}})

    written = write_back(root, {"blob": {"a": ["base"], "b": ["new"]}})

    assert written == [tmp_path / "frag" / "pkgs.json"]
    assert json.loads(inc.read_text()) == {"a": {"$include": "base.json"},
                                           "b": ["new"]}


# --- $include_text / $include_line ------------------------------------------ #

def test_changed_include_text_is_written_verbatim(tmp_path):
    body = tmp_path / "sudo.pam"
    body.write_text("#%PAM-1.0\nauth sufficient pam_unix.so\n")
    root = _write(tmp_path / "c.json",
                  {"files": {"content": {"$include_text": "sudo.pam"}}})

    write_back(root, {"files": {"content": "#%PAM-1.0\nauth required pam_unix.so\n"}})

    # verbatim: the trailing newline a PAM file needs is the caller's, not ours
    assert body.read_text() == "#%PAM-1.0\nauth required pam_unix.so\n"


def test_changed_include_line_replaces_only_the_first_line(tmp_path):
    secret = tmp_path / "hash"
    secret.write_text("$6$old\n# generated with dasik hash-password\n")
    root = _write(tmp_path / "c.json",
                  {"users": {"hashed_password": {"$include_line": "hash"}}})

    write_back(root, {"users": {"hashed_password": "$6$new"}})

    assert secret.read_text() == "$6$new\n# generated with dasik hash-password\n"


# --- $concat ---------------------------------------------------------------- #

def test_concat_appends_new_entries_to_the_last_member(tmp_path):
    base = _write(tmp_path / "base.json", ["linux", "base"])
    dev = _write(tmp_path / "dev.json", ["git"])
    root = _write(tmp_path / "c.json", {"packages": {"$concat": [
        {"$include": "base.json"}, {"$include": "dev.json"}]}})

    write_back(root, {"packages": ["linux", "base", "git", "vim", "htop"]})

    assert json.loads(base.read_text()) == ["linux", "base"]
    assert json.loads(dev.read_text()) == ["git", "vim", "htop"]


def test_concat_removes_an_entry_from_the_member_that_holds_it(tmp_path):
    base = _write(tmp_path / "base.json", ["linux", "base"])
    dev = _write(tmp_path / "dev.json", ["git"])
    root = _write(tmp_path / "c.json", {"packages": {"$concat": [
        {"$include": "base.json"}, {"$include": "dev.json"}]}})

    write_back(root, {"packages": ["linux", "git"]})

    assert json.loads(base.read_text()) == ["linux"]
    assert json.loads(dev.read_text()) == ["git"]


# --- new and removed keys --------------------------------------------------- #

def test_a_key_no_file_declared_lands_in_the_root(tmp_path):
    _write(tmp_path / "pkgs.json", ["firefox"])
    root = _write(tmp_path / "c.json", {"packages": {"$include": "pkgs.json"}})

    write_back(root, {"packages": ["firefox"], "hostname": "torre"})

    assert json.loads(root.read_text()) == {
        "packages": {"$include": "pkgs.json"}, "hostname": "torre"}


def test_a_key_sync_dropped_disappears_from_the_root(tmp_path):
    root = _write(tmp_path / "c.json", {"hostname": "torre", "timezone": "UTC"})

    write_back(root, {"hostname": "torre"})

    assert json.loads(root.read_text()) == {"hostname": "torre"}


# --- list entries keep their directives ------------------------------------- #

def test_a_files_entry_keeps_its_include_text_when_the_body_drifted(tmp_path):
    """The case the split exists for: a PAM body pulled in from a real file.

    When the machine's copy drifted, the capture must rewrite THAT file — not
    inline the whole body into the config and lose the split.
    """
    body = tmp_path / "files" / "sudo.pam"
    body.parent.mkdir()
    body.write_text("auth sufficient pam_unix.so\n")
    root = _write(tmp_path / "c.json", {"files": [
        {"path": "/etc/pam.d/sudo", "content": {"$include_text": "files/sudo.pam"}},
        {"path": "/etc/hostname", "content": "torre\n"}]})

    write_back(root, {"files": [
        {"path": "/etc/pam.d/sudo", "content": "auth required pam_unix.so\n"},
        {"path": "/etc/hostname", "content": "torre\n"}]})

    assert body.read_text() == "auth required pam_unix.so\n"
    assert json.loads(root.read_text())["files"][0]["content"] == {
        "$include_text": "files/sudo.pam"}


def test_an_entry_the_machine_grew_is_written_literally(tmp_path):
    body = tmp_path / "sudo.pam"
    body.write_text("auth sufficient pam_unix.so\n")
    root = _write(tmp_path / "c.json", {"files": [
        {"path": "/etc/pam.d/sudo", "content": {"$include_text": "sudo.pam"}}]})

    write_back(root, {"files": [
        {"path": "/etc/pam.d/sudo", "content": "auth sufficient pam_unix.so\n"},
        {"path": "/etc/vconsole.conf", "content": "KEYMAP=es\n"}]})

    files = json.loads(root.read_text())["files"]
    assert files[0]["content"] == {"$include_text": "sudo.pam"}   # untouched
    assert files[1] == {"path": "/etc/vconsole.conf", "content": "KEYMAP=es\n"}
    assert body.read_text() == "auth sufficient pam_unix.so\n"


def test_an_unchanged_secret_file_is_not_rewritten(tmp_path):
    secret = tmp_path / "hash"
    secret.write_text("$6$same\n")
    root = _write(tmp_path / "c.json",
                  {"users": [{"username": "andres",
                              "hashed_password": {"$include_line": "hash"}}]})
    before = secret.stat().st_mtime_ns

    written = write_back(root, {"users": [{"username": "andres",
                                           "hashed_password": "$6$same"}]})

    assert written == []
    assert secret.stat().st_mtime_ns == before


# --- all or nothing --------------------------------------------------------- #

def test_a_missing_included_file_writes_nothing_at_all(tmp_path):
    root = _write(tmp_path / "c.json",
                  {"hostname": "old", "packages": {"$include": "gone.json"}})

    with pytest.raises(Exception):
        write_back(root, {"hostname": "new", "packages": ["firefox"]})

    # the root must not have been half-updated
    assert json.loads(root.read_text())["hostname"] == "old"

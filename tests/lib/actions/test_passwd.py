from dasik.lib.actions.passwd import passwd_entries


def test_parses_home_uid_gid(tmp_path):
    p = tmp_path / "passwd"
    p.write_text("root:x:0:0:r:/root:/bin/sh\nann:x:1000:1001::/home/ann:/bin/zsh\n")
    assert passwd_entries(str(p)) == {"root": ("/root", 0, 0),
                                       "ann": ("/home/ann", 1000, 1001)}


def test_skips_short_and_non_numeric_lines(tmp_path):
    p = tmp_path / "passwd"
    p.write_text("short:x:1\nbad:x:abc:0::/h:/s\nok:x:5:6::/h:/s\n")
    assert passwd_entries(str(p)) == {"ok": ("/h", 5, 6)}


def test_missing_file_is_empty(tmp_path):
    assert passwd_entries(str(tmp_path / "nope")) == {}

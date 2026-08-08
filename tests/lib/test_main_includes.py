"""The CLI resolves a split config before anything else looks at it.

`check`, `plan` and `apply` must all see the assembled config — the schema
validates the finished thing, not the split. `sync` is the exception: it
REWRITES the file it is given, so flattening an assembled config would undo the
split without saying so.
"""
import json

import dasik.__main__ as m


def _split(tmp_path):
    (tmp_path / "packages.json").write_text('["base", "linux"]')
    (tmp_path / "hostname.txt").write_text("split-host")
    main = tmp_path / "main.json"
    main.write_text(json.dumps({
        "hostname": {"$include_text": "hostname.txt"},
        "packages": {"$concat": [{"$include": "packages.json"}, ["git"]]},
    }))
    return main


def test_check_accepts_a_split_config(tmp_path, capsys):
    assert m._cmd_check(_split(tmp_path)) == 0
    assert "OK" in capsys.readouterr().out


def test_loader_returns_the_assembled_config(tmp_path):
    config = m._load_validated_config(_split(tmp_path))
    assert config["packages"] == ["base", "linux", "git"]
    assert config["hostname"] == "split-host"


def test_a_bad_directive_is_reported_against_the_config(tmp_path, capsys):
    main = tmp_path / "main.json"
    main.write_text(json.dumps({"packages": {"$include": "missing.json"}}))
    assert m._cmd_check(main) == 1
    assert "missing.json" in capsys.readouterr().err


def test_traversal_out_of_the_config_directory_is_refused(tmp_path, capsys):
    (tmp_path / "secret.json").write_text('["x"]')
    sub = tmp_path / "sub"
    sub.mkdir()
    main = sub / "main.json"
    main.write_text(json.dumps({"packages": {"$include": "../secret.json"}}))
    assert m._cmd_check(main) == 1
    assert ".." in capsys.readouterr().err


def test_sync_refuses_to_flatten_a_split_config(tmp_path, capsys):
    main = _split(tmp_path)
    before = main.read_text()
    assert m._cmd_sync(main, "/mnt") == 1
    err = capsys.readouterr().err
    assert "flatten" in err
    assert main.read_text() == before, "sync must not touch the file it refused"

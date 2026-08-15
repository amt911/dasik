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


def _stub_capture(monkeypatch, captured):
    """Make `sync` report *captured* as system reality, touching no system."""
    class _Manifest:
        @staticmethod
        def to_dict():
            return {}

    class _Store:
        def __init__(self, _target):
            pass

        @staticmethod
        def load():
            return _Manifest()

    class _Reconciler:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def sync():
            return dict(captured), {"generation": 1}

    monkeypatch.setattr(m, "StateStore", _Store)
    monkeypatch.setattr(m, "Reconciler", _Reconciler)


def test_sync_writes_through_a_split_config(tmp_path, monkeypatch, capsys):
    """The whole point: a config kept in several files survives being synced."""
    main = _split(tmp_path)
    _stub_capture(monkeypatch, {"hostname": "split-host",
                                "packages": ["base", "linux", "git", "vim"]})

    assert m._cmd_sync(main, "/") == 0

    raw = json.loads(main.read_text())
    # the directives are still there — the split was not undone
    assert raw["hostname"] == {"$include_text": "hostname.txt"}
    # the new package landed in the last member, the included file is untouched
    assert raw["packages"]["$concat"][1] == ["git", "vim"]
    assert json.loads((tmp_path / "packages.json").read_text()) == ["base", "linux"]


def test_sync_of_a_converged_split_config_writes_nothing(tmp_path, monkeypatch, capsys):
    main = _split(tmp_path)
    before = {p: p.read_text() for p in tmp_path.iterdir() if p.is_file()}
    _stub_capture(monkeypatch, {"hostname": "split-host",
                                "packages": ["base", "linux", "git"]})

    assert m._cmd_sync(main, "/") == 0

    assert {p: p.read_text() for p in tmp_path.iterdir() if p.is_file()} == before
    assert "already matches" in capsys.readouterr().out


def test_sync_names_every_file_it_wrote(tmp_path, monkeypatch, capsys):
    main = _split(tmp_path)
    _stub_capture(monkeypatch, {"hostname": "other-host",
                                "packages": ["base", "linux", "git"]})

    assert m._cmd_sync(main, "/") == 0

    out = capsys.readouterr().out
    assert "hostname.txt" in out, "a file it rewrote must be named"
    assert (tmp_path / "hostname.txt").read_text() == "other-host"


def test_check_does_not_depend_on_the_validating_machine(tmp_path, monkeypatch, capsys):
    """`check` validates a FILE. It has no target, and it is routinely run
    somewhere other than the machine the config describes — another laptop, a
    CI runner, a container. Refusing an EFI bootloader because *this* host
    booted BIOS makes it impossible to validate a perfectly good config, which
    is what broke the dasik-aur package smoke test.

    `plan` and `apply` still refuse it: they are about to install here.
    """
    config = tmp_path / "efi.json"
    config.write_text(json.dumps({
        "bootloader": "sd-boot",
        "disks": {"disks": [{"device": "/dev/vda", "partition_table": "gpt",
                             "partitions": [{"label": "esp", "size": "512MiB",
                                             "filesystem": "fat32",
                                             "partition_type": "esp",
                                             "mountpoint": "/boot"}]}]},
    }))
    # A machine that is NOT booted in EFI mode.
    monkeypatch.setattr(m.os.path, "exists",
                        lambda p: False if p == "/sys/firmware/efi" else True)

    assert m._cmd_check(config) == 0
    assert "OK" in capsys.readouterr().out

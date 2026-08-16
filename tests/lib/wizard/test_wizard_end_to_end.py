"""The verb: from a recorded `lsblk` to a config on disk that `check` accepts.

This is the assertion the issue actually asks for — "el bloque producido valida
(`dasik check`) y planifica lo que se compuso" — and it is why the wizard's core
is pure: a recorded payload plus a script of keys drives the whole thing with no
disk, no curses and no root.
"""
import json

import pytest

from dasik.__main__ import _cmd_partition_wizard
from dasik.lib.wizard.recipes import Options, find

_LSBLK = {"blockdevices": [
    {"name": "vda", "path": "/dev/vda", "type": "disk", "size": 8589934592,
     "pttype": None},
]}


def _record(tmp_path, payload=_LSBLK):
    path = tmp_path / "lsblk.json"
    path.write_text(json.dumps(payload))
    return str(path)


def _choices(recipe_key="luks-btrfs", passphrase="hunter2", **kw):
    """What the screens would have returned, so the verb can be tested alone."""
    from dasik.lib.wizard.tui import Choices
    return Choices(device="/dev/vda", recipe_key=recipe_key,
                   options=Options(device="/dev/vda", **kw),
                   passphrase=passphrase, hostname="box")


def _run(tmp_path, monkeypatch, choices, **kw):
    monkeypatch.setattr("dasik.__main__._run_wizard_screens", lambda disks: choices)
    return _cmd_partition_wizard(from_lsblk=_record(tmp_path), **kw)


def test_it_writes_a_config_the_loader_accepts(tmp_path, monkeypatch, capsys):
    out = tmp_path / "main.json"

    rc = _run(tmp_path, monkeypatch, _choices(), output=str(out))

    assert rc == 0
    config = json.loads(out.read_text())
    assert config["disks"]["disks"][0]["device"] == "/dev/vda"
    assert "valid" in capsys.readouterr().out.lower()


def test_the_passphrase_lands_in_a_file_not_in_the_json(tmp_path, monkeypatch):
    out = tmp_path / "main.json"

    _run(tmp_path, monkeypatch, _choices(passphrase="hunter2"), output=str(out))

    text = out.read_text()
    assert "hunter2" not in text
    assert '"$include_line"' in text
    assert (tmp_path / "secrets" / "luks-passphrase").read_text() == "hunter2\n"


def test_the_written_config_passes_check(tmp_path, monkeypatch):
    """The round trip the issue names: the secret file exists, so the loader can
    resolve the directive and the schema is satisfied."""
    from dasik.__main__ import _cmd_check
    out = tmp_path / "main.json"
    _run(tmp_path, monkeypatch, _choices(), output=str(out))

    assert _cmd_check(out) == 0      # the verb takes a Path, as argparse hands it


def test_a_plain_recipe_needs_no_secret_file(tmp_path, monkeypatch):
    out = tmp_path / "main.json"

    _run(tmp_path, monkeypatch, _choices(recipe_key="ext4", passphrase=None),
         output=str(out))

    assert not (tmp_path / "secrets").exists()


def test_merge_keeps_the_rest_of_an_existing_config(tmp_path, monkeypatch):
    existing = tmp_path / "main.json"
    existing.write_text(json.dumps({
        "hostname": "keepme",
        "packages": ["base", "linux", "linux-firmware", "vim"],
        "timezone": {"region": "Etc", "city": "UTC"},
        "locales": {"selected_locales": ["en_US.UTF-8 UTF-8"],
                    "desired_locale": "en_US.UTF-8", "desired_tty_layout": "us"},
        "network": {"type": "NetworkManager"},
        "bootloader": "sd-boot",
        "disks": {"disks": [{"device": "/dev/OLD", "partition_table": "gpt",
                             "partitions": [{"label": "x", "size": "rest",
                                             "filesystem": "ext4",
                                             "partition_type": "linux",
                                             "mountpoint": "/", "format": True}]}]},
    }))

    rc = _run(tmp_path, monkeypatch, _choices(recipe_key="ext4", passphrase=None),
              merge_into=str(existing))

    assert rc == 0
    config = json.loads(existing.read_text())
    assert config["hostname"] == "keepme"
    assert "vim" in config["packages"]
    assert config["disks"]["disks"][0]["device"] == "/dev/vda"


def test_it_refuses_to_clobber_an_output_that_exists(tmp_path, monkeypatch, capsys):
    out = tmp_path / "main.json"
    out.write_text("{}")

    rc = _run(tmp_path, monkeypatch, _choices(), output=str(out))

    assert rc == 1
    assert out.read_text() == "{}"
    assert "--force" in capsys.readouterr().err


def test_force_replaces_it(tmp_path, monkeypatch):
    out = tmp_path / "main.json"
    out.write_text("{}")

    rc = _run(tmp_path, monkeypatch, _choices(), output=str(out), force=True)

    assert rc == 0 and json.loads(out.read_text())["hostname"] == "box"


def test_abandoning_the_wizard_writes_nothing(tmp_path, monkeypatch, capsys):
    out = tmp_path / "main.json"

    rc = _run(tmp_path, monkeypatch, None, output=str(out))

    assert rc == 1
    assert not out.exists()
    assert "nothing was written" in capsys.readouterr().out.lower()


def test_it_tells_you_what_to_run_next(tmp_path, monkeypatch, capsys):
    """The whole point of the split: the wizard composes, `plan` reviews and
    only `apply` touches a disk."""
    out = tmp_path / "main.json"

    _run(tmp_path, monkeypatch, _choices(), output=str(out))

    printed = capsys.readouterr().out
    assert "dasik plan" in printed and "dasik apply" in printed


def test_output_or_merge_into_is_required(tmp_path, monkeypatch, capsys):
    rc = _run(tmp_path, monkeypatch, _choices())

    assert rc == 2
    assert "--output" in capsys.readouterr().err


def test_a_recorded_lsblk_that_has_no_disks_is_reported(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("dasik.__main__._run_wizard_screens",
                        lambda disks: pytest.fail("should not have opened a screen"))
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"blockdevices": []}))

    rc = _cmd_partition_wizard(output=str(tmp_path / "out.json"),
                               from_lsblk=str(path))

    assert rc == 1
    assert "no disks" in capsys.readouterr().err.lower()


def test_the_hibernate_recipe_lands_its_resume_parameter(tmp_path, monkeypatch):
    out = tmp_path / "main.json"

    _run(tmp_path, monkeypatch, _choices(recipe_key="luks-btrfs-hibernate"),
         output=str(out))

    config = json.loads(out.read_text())
    assert "resume=/dev/mapper/cryptswap" in config["kernel_cmdline"]


def test_the_custom_layout_reaches_the_file(tmp_path, monkeypatch):
    from dasik.lib.wizard.tui import Choices
    partitions = [
        {"label": "ESP", "size": "512MiB", "filesystem": "fat32",
         "partition_type": "esp", "mountpoint": "/boot", "format": True},
        {"label": "root", "size": "rest", "filesystem": "xfs",
         "partition_type": "linux", "mountpoint": "/", "format": True},
    ]
    choices = Choices(device="/dev/vda", recipe_key="custom",
                      options=Options(device="/dev/vda"), passphrase=None,
                      hostname="box", custom_partitions=partitions)
    out = tmp_path / "main.json"

    _run(tmp_path, monkeypatch, choices, output=str(out))

    written = json.loads(out.read_text())["disks"]["disks"][0]
    assert [p["filesystem"] for p in written["partitions"]] == ["fat32", "xfs"]


# --- the terminal it needs --------------------------------------------------- #

def test_no_terminal_is_a_message_not_a_curses_traceback(tmp_path, capsys,
                                                         monkeypatch):
    """Run from a script, or with stdin redirected, curses says
    `setupterm: could not find terminal` and dies. That is not a useful
    sentence to end a partitioning session on."""
    import dasik.__main__ as main

    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: False)
    out = tmp_path / "main.json"

    rc = main._cmd_partition_wizard(output=str(out), from_lsblk=_record(tmp_path))

    assert rc == 1
    assert not out.exists()
    err = capsys.readouterr().err.lower()
    assert "terminal" in err and "--from-lsblk" not in err.split("terminal")[0]


def test_a_curses_failure_is_reported_rather_than_raised(tmp_path, capsys,
                                                         monkeypatch):
    import dasik.__main__ as main

    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main, "_open_curses",
                        lambda disks: (_ for _ in ()).throw(
                            Exception("setupterm: could not find terminal")))
    out = tmp_path / "main.json"

    rc = main._cmd_partition_wizard(output=str(out), from_lsblk=_record(tmp_path))

    assert rc == 1
    assert "setupterm" in capsys.readouterr().err

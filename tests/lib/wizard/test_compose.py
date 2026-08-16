"""Turning a chosen layout into a file on disk — and the secret beside it.

Two things this must never do: overwrite a config it was not told to touch, and
write a passphrase into JSON. The second is why `write_secret` exists at all: a
config carrying `{"$include_line": "secrets/…"}` is refused by `dasik check`
until that file exists, so a wizard that emitted the reference and stopped would
hand you a config the tool rejects.
"""
import json
import os
import stat

import pytest

from dasik.lib.wizard.compose import (compose, merge_into, warnings_for,
                                      write_config, write_secret)
from dasik.lib.wizard.recipes import Options, find

_BUILT = find("luks-btrfs").build(Options(device="/dev/vda"))
_PLAIN = find("ext4").build(Options(device="/dev/vda"))


def test_compose_wraps_the_disk_in_a_config_that_validates():
    config = compose(_PLAIN, hostname="box")

    assert config["hostname"] == "box"
    assert config["disks"]["disks"] == [_PLAIN.disk]


def test_compose_carries_the_recipe_s_kernel_cmdline():
    built = find("luks-btrfs-hibernate").build(Options(device="/dev/vda"))

    config = compose(built, hostname="box")

    assert "resume=/dev/mapper/cryptswap" in config["kernel_cmdline"]


def test_compose_gives_a_new_config_the_minimum_that_installs():
    """A `disks` block alone is not an installable config; the wizard's output
    should be something `dasik check` accepts and a human can grow."""
    config = compose(_PLAIN, hostname="box")

    for key in ("hostname", "timezone", "locales", "network", "bootloader",
                "packages"):
        assert key in config


def test_merge_replaces_only_the_disks_block():
    existing = {"hostname": "keepme", "packages": ["base"],
                "disks": {"disks": [{"device": "/dev/OLD", "partitions": []}]},
                "kernel_cmdline": ["quiet"]}

    merged = merge_into(existing, _PLAIN)

    assert merged["hostname"] == "keepme"
    assert merged["packages"] == ["base"]
    assert merged["disks"]["disks"] == [_PLAIN.disk]


def test_merge_appends_the_kernel_cmdline_without_duplicating():
    built = find("luks-btrfs-hibernate").build(Options(device="/dev/vda"))
    existing = {"hostname": "box",
                "kernel_cmdline": ["quiet", "resume=/dev/mapper/cryptswap"]}

    merged = merge_into(existing, built)

    assert merged["kernel_cmdline"].count("resume=/dev/mapper/cryptswap") == 1
    assert "quiet" in merged["kernel_cmdline"]


def test_merge_does_not_mutate_what_it_was_given():
    existing = {"hostname": "box", "disks": {"disks": []}}

    merge_into(existing, _PLAIN)

    assert existing["disks"]["disks"] == []


def test_write_config_creates_the_file_and_a_readable_json(tmp_path):
    path = tmp_path / "out" / "main.json"

    write_config(path, compose(_PLAIN, hostname="box"))

    assert json.loads(path.read_text())["hostname"] == "box"
    assert path.read_text().endswith("\n")


def test_write_config_refuses_to_clobber_an_existing_file(tmp_path):
    path = tmp_path / "main.json"
    path.write_text("{}")

    with pytest.raises(FileExistsError):
        write_config(path, {"hostname": "box"})


def test_write_config_overwrites_when_told_to(tmp_path):
    path = tmp_path / "main.json"
    path.write_text("{}")

    write_config(path, {"hostname": "box"}, overwrite=True)

    assert json.loads(path.read_text()) == {"hostname": "box"}


def test_the_secret_is_written_beside_the_config_at_0600(tmp_path):
    config_path = tmp_path / "main.json"

    written = write_secret(config_path, "secrets/luks-passphrase", "hunter2")

    assert written == tmp_path / "secrets" / "luks-passphrase"
    assert written.read_text() == "hunter2\n"
    assert stat.S_IMODE(os.stat(written).st_mode) == 0o600


def test_the_secret_file_is_relative_to_the_config_that_names_it(tmp_path):
    """`$include_line` resolves against the config's directory, so the wizard
    has to write it there and nowhere else."""
    config_path = tmp_path / "deep" / "main.json"

    written = write_secret(config_path, "secrets/luks", "pw")

    assert written == tmp_path / "deep" / "secrets" / "luks"


def test_an_empty_passphrase_is_refused(tmp_path):
    with pytest.raises(ValueError):
        write_secret(tmp_path / "main.json", "secrets/luks", "")


def test_warnings_name_a_disk_that_holds_data():
    config = compose(_PLAIN, hostname="box")

    warnings = warnings_for(config, wiping="/dev/vda", disk_is_empty=False)

    assert any("/dev/vda" in w for w in warnings)
    assert any("erase" in w.lower() for w in warnings)


def test_an_empty_disk_earns_no_erase_warning():
    config = compose(_PLAIN, hostname="box")

    assert warnings_for(config, wiping=None, disk_is_empty=True) == []


def test_warnings_include_what_preflight_says_about_the_layout():
    """Reuse, not a second opinion: preflight already knows several of these
    (a crypttab for a label nothing provides, hibernation on a random swap)."""
    built = find("luks-btrfs-swap").build(Options(device="/dev/vda"))
    config = compose(built, hostname="box")
    config["kernel_cmdline"] = ["resume=/dev/mapper/cryptswap"]   # cannot work

    warnings = warnings_for(config, wiping=None, disk_is_empty=True)

    assert any("hibernat" in w.lower() or "resume" in w.lower() for w in warnings)

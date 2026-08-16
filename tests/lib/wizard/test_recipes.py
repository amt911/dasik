"""The layouts the wizard offers, as pure functions.

Each one is a layout this repo already installs and boots in QEMU, which is the
whole reason they are offered: the value of an assistant is producing a block
that is CORRECT, and these are the ones known to work.

Every recipe is asserted three ways — it validates through the model, it says
what it claims to say, and (in test_wizard_end_to_end.py) the config it lands in
passes `check`.
"""
import pytest

from dasik.lib.wizard.recipes import (RECIPES, Options, custom_disk, find,
                                      validate_layout)

_SECRET = {"$include_line": "secrets/luks-passphrase"}


def _built(key, **kw):
    return find(key).build(Options(device="/dev/vda", **kw))


def _labels(disk):
    return [p["label"] for p in disk["partitions"]]


def test_every_recipe_has_a_key_a_title_and_a_detail():
    for recipe in RECIPES:
        assert recipe.key and recipe.title and recipe.detail
    assert len({r.key for r in RECIPES}) == len(RECIPES)


@pytest.mark.parametrize("key", [r.key for r in RECIPES])
def test_every_recipe_validates_through_the_model(key):
    """Against a RESOLVED copy, which is the only thing the model ever sees.

    The block the wizard writes still holds `{"$include_line": …}` for the
    passphrase — the loader resolves that before validation, and the model
    declares `luks_password: str`. Validating the raw block would force the
    wizard to write the secret in clear, which is the one thing the issue rules
    out.
    """
    validate_layout(_built(key).disk)


@pytest.mark.parametrize("key", [r.key for r in RECIPES])
def test_every_recipe_puts_an_esp_at_boot_first(key):
    disk = _built(key).disk

    esp = disk["partitions"][0]
    assert esp["partition_type"] == "esp"
    assert esp["filesystem"] == "fat32"
    assert esp["mountpoint"] == "/boot"


@pytest.mark.parametrize("key", [r.key for r in RECIPES])
def test_exactly_one_partition_takes_the_rest(key):
    sizes = [p["size"] for p in _built(key).disk["partitions"]]

    assert sizes.count("rest") == 1
    assert sizes[-1] == "rest"           # and it is the last one


def test_the_plain_recipe_is_esp_plus_ext4_root():
    disk = _built("ext4").disk

    assert _labels(disk) == ["ESP", "root"]
    root = disk["partitions"][1]
    assert root["filesystem"] == "ext4"
    assert root["mountpoint"] == "/"
    assert root.get("encrypt", False) is False


def test_the_luks_btrfs_recipe_carries_the_repo_subvolume_layout():
    root = _built("luks-btrfs").disk["partitions"][1]

    assert root["encrypt"] is True and root["luks_name"] == "cryptroot"
    assert root["filesystem"] == "btrfs"
    # btrfs root is mounted through its subvolumes, never directly.
    assert root["mountpoint"] is None
    assert [s["name"] for s in root["btrfs_subvolumes"]] == [
        "@", "@home", "@log", "@pkg", "@.snapshots"]
    assert root["mount_options"] == ["compress-force=zstd:3"]


def test_the_passphrase_is_never_written_in_clear():
    """The one rule the issue states outright."""
    for key in [r.key for r in RECIPES]:
        for part in _built(key).disk["partitions"]:
            if part.get("encrypt"):
                assert part["luks_password"] == _SECRET


def test_the_swap_recipe_uses_a_random_key_and_says_it_cannot_hibernate():
    built = _built("luks-btrfs-swap")

    swap = next(p for p in built.disk["partitions"] if p["filesystem"] == "swap")
    assert swap["swap_encryption"] == "random"
    assert swap.get("encrypt", False) is False      # random != LUKS
    assert any("hibernat" in note.lower() for note in built.notes)


def test_the_hibernate_recipe_uses_a_luks_swap_and_adds_resume():
    """A random-key swap gets a fresh key every boot, so it can never be read
    back — which is exactly why hibernation needs the LUKS one. And `resume=`
    is not derived from anything: without this line the machine has a swap it
    cannot resume from."""
    built = _built("luks-btrfs-hibernate")

    swap = next(p for p in built.disk["partitions"] if p["filesystem"] == "swap")
    assert swap["encrypt"] is True
    assert swap["luks_name"] == "cryptswap"
    assert swap.get("swap_encryption", "none") == "none"
    assert built.kernel_cmdline == ("resume=/dev/mapper/cryptswap",)


def test_sizes_and_names_come_from_the_options():
    built = _built("luks-btrfs-hibernate", esp_size="1GiB", swap_size="32GiB",
                   luks_name="cryptsys", swap_luks_name="cryptzzz")
    disk = built.disk

    assert disk["partitions"][0]["size"] == "1GiB"
    swap = next(p for p in disk["partitions"] if p["filesystem"] == "swap")
    assert swap["size"] == "32GiB" and swap["luks_name"] == "cryptzzz"
    assert built.kernel_cmdline == ("resume=/dev/mapper/cryptzzz",)


def test_the_disk_is_not_wiped_unless_asked():
    """`wipe_disk` is the destructive flag. An empty disk needs no wipe, and
    the wizard must not turn it on just because it composed the layout."""
    assert _built("ext4").disk["wipe_disk"] is False
    assert _built("ext4", wipe=True).disk["wipe_disk"] is True


def test_partitions_are_formatted_because_the_layout_is_new():
    for part in _built("luks-btrfs").disk["partitions"]:
        assert part["format"] is True


def test_an_unknown_key_is_an_error_naming_what_exists():
    with pytest.raises(KeyError) as e:
        find("nope")

    assert "ext4" in str(e.value)


# --- the custom path -------------------------------------------------------- #

def test_custom_builds_a_disk_from_composed_partitions():
    disk = custom_disk("/dev/sdb", [
        {"label": "ESP", "size": "512MiB", "filesystem": "fat32",
         "partition_type": "esp", "mountpoint": "/boot", "format": True},
        {"label": "root", "size": "rest", "filesystem": "xfs",
         "partition_type": "linux", "mountpoint": "/", "format": True},
    ])

    validate_layout(disk)
    assert disk["device"] == "/dev/sdb"
    assert disk["wipe_disk"] is False
    assert _labels(disk) == ["ESP", "root"]


def test_custom_refuses_a_layout_the_model_rejects():
    """Better here than three screens later: two partitions sized `rest` is
    something the schema already knows is impossible."""
    with pytest.raises(ValueError):
        custom_disk("/dev/sdb", [
            {"label": "a", "size": "rest", "filesystem": "ext4",
             "partition_type": "linux", "mountpoint": "/", "format": True},
            {"label": "b", "size": "rest", "filesystem": "ext4",
             "partition_type": "linux", "mountpoint": "/home", "format": True},
        ])

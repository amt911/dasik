import pytest

from dasik.lib.expand import expand_config
from dasik.lib.expand.toggles import expand_sdboot_update


@pytest.mark.parametrize("loader", ["sd-boot", "systemd-boot"])
def test_enables_the_native_updater_for_systemd_boot(loader):
    assert expand_sdboot_update({"bootloader": loader}) == {
        "units": ["systemd-boot-update.service"]}


def test_grub_contributes_nothing():
    assert expand_sdboot_update({"bootloader": "grub"}) == {}


def test_missing_bootloader_contributes_nothing():
    assert expand_sdboot_update({}) == {}


def test_expand_config_enables_the_unit():
    merged = expand_config({"bootloader": "sd-boot"})
    assert "systemd-boot-update.service" in merged["systemd"]["enable_units"]

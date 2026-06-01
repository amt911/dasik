from dasik.lib.models.json_model import JsonModel


def _base(**extra):
    return JsonModel(
        locales={"selected_locales": ["en_US.UTF-8 UTF-8"],
                 "desired_locale": "en_US.UTF-8", "desired_tty_layout": "us"},
        timezone={"region": "Europe", "city": "Madrid"},
        network={"type": "NetworkManager", "add_default_hosts": True},
        hostname="arch",
        **extra,
    )


def test_initramfs_defaults_to_mkinitcpio():
    assert _base().initramfs == "mkinitcpio"


def test_initramfs_accepts_dracut():
    assert _base(initramfs="dracut").initramfs == "dracut"

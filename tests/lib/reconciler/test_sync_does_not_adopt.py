"""`sync` must not claim ownership of what it merely observed (issue #197, half 2).

The repo's safety property, in `set_math`'s own words: *"removal is scoped to M
(what dasik itself applied). Manually-installed items appear as drift and become
candidates for `sync`, never for automatic removal."*

`sync` broke it. It recorded `managed ← actual()`, and for the domains whose
`actual()` reads the whole machine — every explicit package, every enabled unit
— that meant dasik took ownership of `mkinitcpio`, `getty@.service`,
`remote-fs.target`… things it never installed and never enabled. The bill
arrived at the next `rollback`, which proposed removing them; pacman refused to
drop `mkinitcpio` and the rollback died half-applied.

What `sync` may keep owning: what it already owned, and what the config
declares and reality confirms. Never a pure observation.
"""
from unittest.mock import MagicMock, patch

from dasik.lib.actions.action_registry import get_default_registry
from dasik.lib.actions.actions_handler_v2 import setup_actions
from dasik.lib.expand import expand_config
from dasik.lib.reconciler.reconciler import Reconciler
from dasik.lib.target.target import Target


_SEED = {"bootloader": "sd-boot", "packages": ["base", "linux"]}


def _sync(tmp_path, manifest=None, installed=("base", "linux", "mkinitcpio", "htop")):
    """Sync against a machine whose pacman reports *installed* as explicit."""
    from dasik.lib.actions.packages_action import PackagesAction

    setup_actions()
    reconciler = Reconciler(
        config=_SEED, target=Target(root=str(tmp_path)), manifest=manifest,
        action_metas=get_default_registry().get_all_actions(),
        owned_config=expand_config(_SEED),
    )
    with patch.object(PackagesAction, "actual", return_value=set(installed)), \
         patch.object(PackagesAction, "_installed_all", return_value=set(installed)), \
         patch.object(PackagesAction, "_unit_provider_packages", return_value=set()), \
         patch.object(PackagesAction, "_base_guaranteed", return_value=set()):
        config, manifest_out = reconciler.sync()
    return config, manifest_out.to_dict()["managed"]


def test_a_package_dasik_never_installed_is_not_adopted(tmp_path):
    """`mkinitcpio` arrives with the kernel and `htop` by hand. Capturing them
    into the config is right; claiming the right to delete them is not."""
    _config, managed = _sync(tmp_path)

    assert "mkinitcpio" not in managed["packages"]
    assert "htop" not in managed["packages"]


def test_what_the_config_declares_and_the_machine_has_stays_owned(tmp_path):
    _config, managed = _sync(tmp_path)

    assert set(managed["packages"]) == {"base", "linux"}


def test_what_dasik_already_owned_stays_owned(tmp_path):
    """An earlier apply installed htop; a later sync must not disown it, or
    dropping it from the config would stop removing it."""
    manifest = {"managed": {"packages": ["base", "linux", "htop"]}}

    _config, managed = _sync(tmp_path, manifest=manifest)

    assert "htop" in managed["packages"]


def test_an_owned_package_that_vanished_is_dropped(tmp_path):
    """Ownership follows reality downwards: what is gone is not owned."""
    manifest = {"managed": {"packages": ["base", "linux", "vim"]}}

    _config, managed = _sync(tmp_path, manifest=manifest)

    assert "vim" not in managed["packages"]


def test_the_observation_still_reaches_the_config(tmp_path):
    """Not adopting is not the same as not capturing: the point of sync is that
    the config gains what the machine has."""
    config, _managed = _sync(tmp_path)

    assert "htop" in config["packages"]


def test_the_capture_is_what_makes_it_owned_next_time(tmp_path):
    """Apply the captured config and dasik owns it — by having applied it, which
    is the whole rule."""
    from dasik.lib.actions.packages_action import PackagesAction

    config, _managed = _sync(tmp_path)
    action = PackagesAction(config, None)

    assert "htop" in action.managed_keys()["packages"]

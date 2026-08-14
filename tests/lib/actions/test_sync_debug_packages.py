"""A capture must not name a package that no source can install.

Building an AUR package with `makepkg -si` also builds and installs its split
`-debug` package (Arch's default makepkg.conf asks for one). pacman then reports
`yay-debug` as an explicitly-installed foreign package, and `sync` wrote it into
the config:

    "packages": [..., "yay", "base-devel", "git", "mkinitcpio", "yay-debug"]

There is no `yay-debug` in any repo and none in the AUR — it only ever exists as
a by-product of building `yay` on this machine. Apply that captured config
somewhere else and the name resolves nowhere: warn-and-skip drops it, or
`package_policy: error` aborts the install.

A `-debug` package somebody actually declares is left alone: that is intent.
"""
from unittest.mock import MagicMock

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.packages_action import PackagesAction
from dasik.lib.target.target import Target


def _captured(config, explicit):
    action = PackagesAction(config, ActionContext(target=Target(root="/")))
    action._installed_all = MagicMock(return_value=set(explicit))
    action.actual = MagicMock(return_value=set(explicit))
    action._unit_provider_packages = MagicMock(return_value=set())
    return [p if isinstance(p, str) else p["name"]
            for p in action.import_state([])["packages"]]


def test_a_debug_by_product_is_not_captured():
    captured = _captured({"packages": ["yay"]}, explicit={"yay", "yay-debug"})

    assert "yay" in captured
    assert "yay-debug" not in captured


def test_a_declared_debug_package_survives():
    """Intent wins: if the config asks for it, the capture keeps it."""
    captured = _captured({"packages": ["yay", "yay-debug"]},
                         explicit={"yay", "yay-debug"})

    assert "yay-debug" in captured


def test_a_repo_package_called_debug_is_not_touched():
    """Only a by-product is dropped — one whose BASE package is installed too.
    A package that merely ends in -debug, with no base beside it, is somebody's
    real package and stays."""
    captured = _captured({"packages": []}, explicit={"some-debug"})

    assert "some-debug" in captured


def test_everything_else_still_comes_back():
    captured = _captured({"packages": ["yay"]},
                         explicit={"yay", "yay-debug", "htop"})

    assert "htop" in captured

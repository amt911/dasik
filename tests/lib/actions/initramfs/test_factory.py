import pytest

from dasik.lib.actions.initramfs import make_backend
from dasik.lib.actions.initramfs.mkinitcpio import MkinitcpioBackend
from dasik.lib.actions.initramfs.dracut import DracutBackend
from dasik.lib.target.target import Target


def test_make_backend_mkinitcpio():
    assert isinstance(make_backend("mkinitcpio", {}, Target(root="/")), MkinitcpioBackend)


def test_make_backend_dracut():
    assert isinstance(make_backend("dracut", {}, Target(root="/")), DracutBackend)


def test_make_backend_unknown_raises():
    with pytest.raises(ValueError):
        make_backend("booster", {}, Target(root="/"))

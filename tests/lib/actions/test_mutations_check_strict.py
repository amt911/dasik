"""Every mutating shell-out runs with check=True (F-06/F-18 family).

A mutation that exits non-zero must raise, not be recorded as applied state:
`locale-gen`, `pacman -Sy`, `ln -sf`/`hwclock`, `mkinitcpio -P` all wrote their
config files first, so a failing command left the action looking converged while
the derived artefact (locale archive, sync db, /etc/localtime, initramfs image)
was missing or stale. Pure argv assertions — nothing is executed.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.initramfs.mkinitcpio import MkinitcpioBackend
from dasik.lib.actions.locale_action import LocaleAction
from dasik.lib.actions.pacman_action import PacmanAction
from dasik.lib.actions.timezone_action import TimezoneAction
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


def _mutating_calls(run):
    return [c for c in run.call_args_list]


def test_locale_gen_uses_check_true(tmp_path):
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "locale.gen").write_text("#en_US.UTF-8 UTF-8\n")
    a = LocaleAction({"selected_locales": ["en_US.UTF-8 UTF-8"],
                      "desired_locale": "en_US.UTF-8",
                      "desired_tty_layout": "us"}, _ctx(tmp_path))
    with patch("dasik.lib.actions.locale_action.Command.execute") as run:
        a._set_value()
    assert run.call_args_list[0].args[0] == "locale-gen"
    assert run.call_args_list[0].kwargs.get("check") is True


def test_timezone_link_and_hwclock_use_check_true(tmp_path):
    (tmp_path / "etc").mkdir()
    a = TimezoneAction({"region": "Europe", "city": "Madrid"}, _ctx(tmp_path))
    with patch("dasik.lib.actions.timezone_action.Command.execute") as run:
        a._set_value()
    assert [c.args[0] for c in run.call_args_list] == ["ln", "hwclock"]
    assert all(c.kwargs.get("check") is True for c in run.call_args_list)


def test_pacman_sync_db_uses_check_true(tmp_path):
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "pacman.conf").write_text("[options]\n")
    a = PacmanAction({"multilib": True}, _ctx(tmp_path))
    with patch("dasik.lib.actions.pacman_action.Command.execute") as run:
        a.apply([Change("pacman", Op.MODIFY, "pacman.conf")])
    syncs = [c for c in run.call_args_list if c.args[0] == "pacman"]
    assert syncs, "expected a `pacman -Sy` after enabling multilib"
    assert all(c.kwargs.get("check") is True for c in syncs)


def test_mkinitcpio_regeneration_uses_check_true(tmp_path):
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "mkinitcpio.conf").write_text("HOOKS=(base udev)\n")
    backend = MkinitcpioBackend(
        {"hooks": ["base", "systemd", "sd-encrypt"]},
        target=Target(root=str(tmp_path)),
    )
    with patch("dasik.lib.actions.initramfs.mkinitcpio.Command.execute") as run:
        backend.apply()
    assert run.call_args_list[-1].args[0] == "mkinitcpio"
    assert run.call_args_list[-1].kwargs.get("check") is True

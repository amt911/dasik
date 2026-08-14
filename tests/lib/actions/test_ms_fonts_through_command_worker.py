"""The font installer shelled out behind dasik's own back.

Every other action runs through `Command.execute`, which locates the binary,
prefixes `arch-chroot` for the target, raises `CommandNotFoundException` with a
sentence instead of a raw errno — and, the part that matters here, **records the
run**. `MicrosoftFontsAction._install` called `subprocess.run` directly, so the
install log (added precisely so a failed install can be read afterwards) had a
hole exactly where a 7z extraction of a Windows ISO might go wrong.

The commands themselves are unchanged; only the door they go through is.
"""
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.ms_fonts_action import MicrosoftFontsAction
from dasik.lib.target.target import Target


def _action(tmp_path):
    return MicrosoftFontsAction({"install": True, "source_iso": "/iso/win.iso"},
                                ActionContext(target=Target(root=str(tmp_path))))


def _install_calls(tmp_path):
    action = _action(tmp_path)
    with patch("dasik.lib.actions.ms_fonts_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0)
        action._install()
    return execute.call_args_list


def test_nothing_reaches_subprocess_directly(tmp_path):
    action = _action(tmp_path)
    with patch("dasik.lib.actions.ms_fonts_action.Command.execute") as execute, \
         patch("subprocess.run") as raw:
        execute.return_value = MagicMock(returncode=0)
        action._install()

    raw.assert_not_called()


def test_every_step_goes_through_the_command_worker(tmp_path):
    calls = _install_calls(tmp_path)

    commands = [c.args[0] for c in calls]
    assert commands[0] == "pacman"                 # 7zip first
    assert "fc-cache" in commands                  # …and the cache rebuild last
    assert all(c.kwargs.get("check") for c in calls), "a failed step must abort"


def test_it_runs_inside_the_target_not_on_the_host(tmp_path):
    calls = _install_calls(tmp_path)

    for call in calls:
        assert call.kwargs.get("target") is not None, call.args
    # and no call re-adds arch-chroot by hand — that is Command's job
    for call in calls:
        assert call.args[0] != "arch-chroot"
        assert "arch-chroot" not in call.args[1]


def test_the_iso_path_is_still_made_relative_to_the_target(tmp_path):
    """The ISO lives on the target, so the path handed to 7z must be the one the
    chroot sees."""
    action = MicrosoftFontsAction(
        {"install": True, "source_iso": f"{tmp_path}/iso/win.iso"},
        ActionContext(target=Target(root=str(tmp_path))))
    with patch("dasik.lib.actions.ms_fonts_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0)
        action._install()

    seven_zip = [c for c in execute.call_args_list if c.args[0] == "7z"][0]
    assert "/iso/win.iso" in seven_zip.args[1]
    assert str(tmp_path) not in " ".join(seven_zip.args[1])

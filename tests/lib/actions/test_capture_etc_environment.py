"""A customised /etc/environment must survive a capture.

`sync` read that file only when the config already DECLARED `etc_environment`
(`if _ENV_PATH in actual`, and `actual` is "declared paths that exist"). On a
machine that never declared it — the usual case for a first capture — the lines
were simply lost, and the file cannot be rescued by file discovery either,
because /etc/environment belongs to the `pam` package and discovery skips
package-owned files on purpose.

Measured on a real Arch machine:

    $ pacman -Qo /etc/environment
    /etc/environment is owned by pam 1.7.2-2
    $ grep -cvE '^\\s*(#|$)' /etc/environment
    3                       <- three real settings a sync would have dropped

The stock file is nothing but comments, so "has effective lines" is exactly the
question "did somebody put something here": capture those, and stay quiet on a
machine where nobody did.
"""
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.drop_files_action import DropFilesAction
from dasik.lib.target.target import Target

_STOCK = ("#\n# This file is parsed by pam_env module\n#\n"
          "# Syntax: simple \"KEY=VAL\" pairs on separate lines\n#\n")


def _write(tmp_path, text):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/environment").write_text(text)


def _action(tmp_path, config=None):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    action = DropFilesAction(config or {}, ActionContext(target=Target(root=str(tmp_path))))
    action._pacman_owner = lambda path: "pam" if path == "/etc/environment" else None
    return action


def test_the_lines_somebody_added_are_captured(tmp_path):
    _write(tmp_path, _STOCK + "LIBVA_DRIVER_NAME=nvidia\nMOZ_DISABLE_RDD_SANDBOX=1\n")

    captured = _action(tmp_path).import_state(managed=[])

    assert captured["etc_environment"] == ["LIBVA_DRIVER_NAME=nvidia",
                                           "MOZ_DISABLE_RDD_SANDBOX=1"]


def test_the_stock_file_captures_nothing(tmp_path):
    _write(tmp_path, _STOCK)

    captured = _action(tmp_path).import_state(managed=[])

    assert not captured.get("etc_environment")


def test_no_file_at_all_captures_nothing(tmp_path):
    captured = _action(tmp_path).import_state(managed=[])

    assert not captured.get("etc_environment")


def test_a_declared_block_still_reports_the_machine(tmp_path):
    """Declared or not, sync reports what is on the disk — not the config."""
    _write(tmp_path, _STOCK + "EDITOR=nvim\n")
    action = _action(tmp_path, {"etc_environment": ["EDITOR=vim"]})

    assert action.import_state(managed=[])["etc_environment"] == ["EDITOR=nvim"]


def test_comments_and_blank_lines_are_not_settings(tmp_path):
    _write(tmp_path, "# a note\n\nEDITOR=nvim\n\n#end\n")

    assert _action(tmp_path).import_state(managed=[])["etc_environment"] == ["EDITOR=nvim"]

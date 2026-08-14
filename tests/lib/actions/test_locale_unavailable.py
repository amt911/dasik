"""A locale the target cannot generate must fail loudly, not for ever.

/etc/locale.gen ships every locale glibc knows, commented out; enabling one is
uncommenting its line. A locale that is NOT in that file cannot be enabled at
all — and dasik wrote `LANG=` for it anyway and reported success:

    $ dasik apply config.json     # selected_locales: ["xx_XX.UTF-8 UTF-8"]
    ~ [locales] modify …
    $ dasik plan config.json
    ~ [locales] modify …          # the same change, for ever

The apply cannot converge because the state it claims to have reached was never
reachable. One clear failure beats an eternal silent loop.
"""
import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.locale_action import LocaleAction
from dasik.lib.exceptions.exceptions import ConfigValidationError
from dasik.lib.target.target import Target

_GEN = "#en_US.UTF-8 UTF-8\n#es_ES.UTF-8 UTF-8\n#C.UTF-8 UTF-8\n"


def _action(tmp_path, selected, desired="en_US.UTF-8", gen=_GEN):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/locale.gen").write_text(gen)
    return LocaleAction({"selected_locales": selected, "desired_locale": desired,
                         "desired_tty_layout": "us"},
                        ActionContext(target=Target(root=str(tmp_path))))


def test_a_locale_the_target_does_not_ship_stops_the_apply(tmp_path, monkeypatch):
    action = _action(tmp_path, ["xx_XX.UTF-8 UTF-8"], desired="xx_XX.UTF-8")

    with pytest.raises(ConfigValidationError) as exc:
        action.apply(action.plan(managed=[]))

    assert "xx_XX.UTF-8 UTF-8" in str(exc.value)
    assert "locale.gen" in str(exc.value)


def test_it_names_every_missing_one_at_once(tmp_path):
    action = _action(tmp_path, ["xx_XX.UTF-8 UTF-8", "en_US.UTF-8 UTF-8", "zz_ZZ.UTF-8 UTF-8"])

    with pytest.raises(ConfigValidationError) as exc:
        action.apply(action.plan(managed=[]))

    named = str(exc.value).split("does not list ")[1].split(". A locale")[0]
    assert "xx_XX.UTF-8 UTF-8" in named and "zz_ZZ.UTF-8 UTF-8" in named
    assert "en_US" not in named        # the one it CAN enable is not blamed


def test_nothing_is_written_when_it_refuses(tmp_path):
    action = _action(tmp_path, ["xx_XX.UTF-8 UTF-8"], desired="xx_XX.UTF-8")

    with pytest.raises(ConfigValidationError):
        action.apply(action.plan(managed=[]))

    assert not (tmp_path / "etc/locale.conf").exists()
    assert (tmp_path / "etc/locale.gen").read_text() == _GEN


def test_a_locale_that_is_there_still_applies(tmp_path, monkeypatch):
    import unittest.mock as mock
    action = _action(tmp_path, ["en_US.UTF-8 UTF-8"])

    with mock.patch("dasik.lib.actions.locale_action.Command.execute") as execute:
        execute.return_value = mock.MagicMock(returncode=0)
        action.apply(action.plan(managed=[]))

    assert "LANG=en_US.UTF-8" in (tmp_path / "etc/locale.conf").read_text()
    assert "\nen_US.UTF-8 UTF-8" in "\n" + (tmp_path / "etc/locale.gen").read_text()
    assert action.plan(managed=[]) == []          # converges


def test_an_already_enabled_line_counts_as_present(tmp_path):
    """A locale.gen where the line is already uncommented is not 'missing'."""
    import unittest.mock as mock
    action = _action(tmp_path, ["en_US.UTF-8 UTF-8"], gen="en_US.UTF-8 UTF-8\n")

    with mock.patch("dasik.lib.actions.locale_action.Command.execute") as execute:
        execute.return_value = mock.MagicMock(returncode=0)
        action.apply(action.plan(managed=[]))     # must not raise


def test_a_target_without_locale_gen_is_left_to_the_old_error(tmp_path):
    """No locale.gen at all is a broken target, not a bad config: let the
    original FileNotFoundError speak rather than blaming the locale."""
    (tmp_path / "etc").mkdir(parents=True)
    action = LocaleAction({"selected_locales": ["en_US.UTF-8 UTF-8"],
                           "desired_locale": "en_US.UTF-8", "desired_tty_layout": "us"},
                          ActionContext(target=Target(root=str(tmp_path))))

    with pytest.raises(FileNotFoundError):
        action.apply(action.plan(managed=[]))

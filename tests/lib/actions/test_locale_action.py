from unittest.mock import MagicMock, mock_open, patch

from dasik.lib.actions.locale_action import LocaleAction


def _cfg(selected=None, locale="en_US.UTF-8", layout="us"):
    return {
        "selected_locales": selected if selected is not None else ["en_US.UTF-8 UTF-8"],
        "desired_locale": locale,
        "desired_tty_layout": layout,
    }


_GEN_OK = "#es_ES.UTF-8 UTF-8\nen_US.UTF-8 UTF-8\n"
_GEN_WRONG_COUNT = "en_US.UTF-8 UTF-8\nde_DE.UTF-8 UTF-8\n"


def _fake_open(files):
    def opener(path, *a, **k):
        for key, content in files.items():
            if key in str(path):
                return mock_open(read_data=content)()
        raise FileNotFoundError(path)
    return opener


def test_needed_when_uncommented_count_mismatch():
    a = LocaleAction(_cfg())
    with patch("builtins.open", side_effect=_fake_open({"locale.gen": _GEN_WRONG_COUNT})):
        assert a.is_needed() is True


def test_needed_when_selected_locale_not_in_gen():
    a = LocaleAction(_cfg(selected=["fr_FR.UTF-8 UTF-8"]))
    with patch("builtins.open", side_effect=_fake_open({"locale.gen": _GEN_OK})):
        assert a.is_needed() is True


def test_needed_when_locale_conf_absent():
    a = LocaleAction(_cfg())
    with patch("builtins.open", side_effect=_fake_open({"locale.gen": _GEN_OK})), \
         patch("dasik.lib.actions.locale_action.Path") as P:
        P.return_value = MagicMock(exists=MagicMock(return_value=False))
        assert a.is_needed() is True


def test_not_needed_when_everything_matches():
    files = {
        "locale.gen": _GEN_OK,
        "locale.conf": "LANG=en_US.UTF-8",
        "vconsole.conf": "KEYMAP=us",
    }
    a = LocaleAction(_cfg())
    with patch("builtins.open", side_effect=_fake_open(files)), \
         patch("dasik.lib.actions.locale_action.Path") as P:
        P.return_value = MagicMock(exists=MagicMock(return_value=True))
        assert a.is_needed() is False
        assert a.verify() is True


def test_name_and_optional():
    a = LocaleAction(_cfg())
    assert a.name == "Locale Configuration"
    assert a.is_optional is True

from unittest.mock import patch

from dasik.lib.actions.ms_fonts_action import MicrosoftFontsAction


def test_disabled_is_never_needed():
    assert MicrosoftFontsAction({"install": False}).is_needed() is False


def test_no_source_iso_warns_and_skips(capsys):
    a = MicrosoftFontsAction({"install": True, "source_iso": ""})
    assert a.is_needed() is False
    assert "no source_iso" in capsys.readouterr().out


def test_needed_when_fonts_dir_absent():
    a = MicrosoftFontsAction({"install": True, "source_iso": "/win.iso"})
    with patch("dasik.lib.actions.ms_fonts_action.os.path.isdir", return_value=False):
        assert a.is_needed() is True


def test_not_needed_when_fonts_dir_populated():
    a = MicrosoftFontsAction({"install": True, "source_iso": "/win.iso"})
    with patch("dasik.lib.actions.ms_fonts_action.os.path.isdir", return_value=True), \
         patch("dasik.lib.actions.ms_fonts_action.os.listdir", return_value=list(range(50))):
        assert a.is_needed() is False
        assert a.verify() is True


def test_needed_when_fonts_dir_underpopulated():
    a = MicrosoftFontsAction({"install": True, "source_iso": "/win.iso"})
    with patch("dasik.lib.actions.ms_fonts_action.os.path.isdir", return_value=True), \
         patch("dasik.lib.actions.ms_fonts_action.os.listdir", return_value=[1, 2, 3]):
        assert a.is_needed() is True


def test_name_and_optional():
    a = MicrosoftFontsAction({"install": True})
    assert a.name == "Microsoft Fonts"
    assert a.is_optional is True

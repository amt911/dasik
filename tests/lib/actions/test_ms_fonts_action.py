from unittest.mock import patch

from dasik.lib.actions.ms_fonts_action import MicrosoftFontsAction
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Change, Op

_FONTS = "/usr/local/share/fonts/WindowsFonts"


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


def _populate(tmp_path, n=20):
    d = tmp_path / _FONTS.lstrip("/")
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (d / f"f{i}.ttf").write_text("x")


def test_is_v3_true():
    assert MicrosoftFontsAction.is_v3() is True


def test_actual_empty_when_absent(tmp_path):
    a = MicrosoftFontsAction({"install": True, "source_iso": "/w.iso"}, _ctx(tmp_path))
    assert a.actual() == set()


def test_actual_present_when_populated(tmp_path):
    _populate(tmp_path)
    a = MicrosoftFontsAction({"install": True, "source_iso": "/w.iso"}, _ctx(tmp_path))
    assert a.actual() == {"windows-fonts"}


def test_plan_install_when_declared_and_missing(tmp_path):
    a = MicrosoftFontsAction({"install": True, "source_iso": "/w.iso"}, _ctx(tmp_path))
    changes = a.plan(managed=[])
    assert changes and changes[0].op is Op.INSTALL


def test_plan_empty_when_present(tmp_path):
    _populate(tmp_path)
    a = MicrosoftFontsAction({"install": True, "source_iso": "/w.iso"}, _ctx(tmp_path))
    assert a.plan(managed=[]) == []


def test_plan_empty_when_no_iso(tmp_path):
    a = MicrosoftFontsAction({"install": True, "source_iso": ""}, _ctx(tmp_path))
    assert a.plan(managed=[]) == []


def test_plan_empty_when_not_install(tmp_path):
    a = MicrosoftFontsAction({"install": False, "source_iso": "/w.iso"}, _ctx(tmp_path))
    assert a.plan(managed=[]) == []


def test_apply_runs_install_when_changes(tmp_path):
    a = MicrosoftFontsAction({"install": True, "source_iso": "/w.iso"}, _ctx(tmp_path))
    with patch.object(MicrosoftFontsAction, "_install") as inst:
        a.apply(a.plan(managed=[]))
        inst.assert_called_once()


def test_apply_noop_when_fonts_present(tmp_path):
    # apply is reality-driven: fonts already present -> never re-extract, even if
    # a (stale) INSTALL change is passed
    _populate(tmp_path)
    a = MicrosoftFontsAction({"install": True, "source_iso": "/w.iso"}, _ctx(tmp_path))
    with patch.object(MicrosoftFontsAction, "_install") as inst:
        a.apply([Change("microsoft_fonts", Op.INSTALL, "windows-fonts")])
        inst.assert_not_called()


def test_apply_noop_when_not_declared(tmp_path):
    a = MicrosoftFontsAction({"install": False, "source_iso": "/w.iso"}, _ctx(tmp_path))
    with patch.object(MicrosoftFontsAction, "_install") as inst:
        a.apply([Change("microsoft_fonts", Op.INSTALL, "windows-fonts")])
        inst.assert_not_called()


def test_import_state_empty(tmp_path):
    a = MicrosoftFontsAction({"install": True, "source_iso": "/w.iso"}, _ctx(tmp_path))
    assert a.import_state(managed=[]) == {}


def test_name_and_optional():
    a = MicrosoftFontsAction({})
    assert a.name == "Microsoft Fonts"
    assert a.is_optional is True

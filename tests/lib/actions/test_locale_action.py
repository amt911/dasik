from unittest.mock import mock_open, patch

from dasik.lib.actions.locale_action import LocaleAction
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Op


def _ctx(root="/"):
    return ActionContext(target=Target(root=root))


def _cfg(selected=None, locale="en_US.UTF-8", layout="us"):
    return {
        "selected_locales": selected if selected is not None else ["en_US.UTF-8 UTF-8"],
        "desired_locale": locale,
        "desired_tty_layout": layout,
    }


_GEN = "#es_ES.UTF-8 UTF-8\nen_US.UTF-8 UTF-8\n"


def _open_tree(gen=_GEN, conf="LANG=en_US.UTF-8", vconsole="KEYMAP=us", missing=()):
    def opener(path, *a, **k):
        p = str(path)
        if "locale.gen" in p:
            data = gen
        elif "locale.conf" in p:
            if "locale.conf" in missing:
                raise FileNotFoundError(p)
            data = conf
        else:
            if "vconsole" in missing:
                raise FileNotFoundError(p)
            data = vconsole
        return mock_open(read_data=data)()
    return patch("builtins.open", side_effect=opener)


def test_is_v3_true():
    assert LocaleAction.is_v3() is True


def test_desired_state_sorts_selected():
    a = LocaleAction(_cfg(selected=["es_ES.UTF-8 UTF-8", "en_US.UTF-8 UTF-8"]), _ctx("/"))
    st = a._desired_state()
    assert st == {
        "selected_locales": ["en_US.UTF-8 UTF-8", "es_ES.UTF-8 UTF-8"],
        "desired_locale": "en_US.UTF-8",
        "desired_tty_layout": "us",
    }


def test_actual_state_parses_three_files():
    a = LocaleAction(_cfg(), _ctx("/"))
    with _open_tree():
        st = a._actual_state()
    assert st == {
        "selected_locales": ["en_US.UTF-8 UTF-8"],
        "desired_locale": "en_US.UTF-8",
        "desired_tty_layout": "us",
    }


def test_actual_state_none_when_locale_conf_missing():
    a = LocaleAction(_cfg(), _ctx("/"))
    with _open_tree(missing=("locale.conf",)):
        assert a._actual_state() is None


def test_actual_state_none_when_vconsole_missing():
    a = LocaleAction(_cfg(), _ctx("/"))
    with _open_tree(missing=("vconsole",)):
        assert a._actual_state() is None


def test_plan_empty_when_converged():
    a = LocaleAction(_cfg(), _ctx("/"))
    with _open_tree():
        assert a.plan(managed=[]) == []


def test_plan_modify_when_lang_differs():
    a = LocaleAction(_cfg(locale="de_DE.UTF-8"), _ctx("/"))
    with _open_tree():
        changes = a.plan(managed=[])
    assert changes[0].op is Op.MODIFY and "desired_locale" in changes[0].item


def test_plan_modify_when_keymap_differs():
    a = LocaleAction(_cfg(layout="es"), _ctx("/"))
    with _open_tree():
        changes = a.plan(managed=[])
    assert changes[0].op is Op.MODIFY and "desired_tty_layout" in changes[0].item


def test_import_fragment_returns_live_state():
    a = LocaleAction(_cfg(), _ctx("/"))
    with _open_tree():
        frag = a.import_state(managed=[])
    assert frag == {"locales": {
        "selected_locales": ["en_US.UTF-8 UTF-8"],
        "desired_locale": "en_US.UTF-8",
        "desired_tty_layout": "us",
    }}


def test_name_and_optional():
    a = LocaleAction(_cfg())
    assert a.name == "Locale Configuration"
    assert a.is_optional is True

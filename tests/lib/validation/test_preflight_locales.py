"""A LANG nobody generates is a machine with a broken locale.

`selected_locales` is what gets uncommented in /etc/locale.gen and generated;
`desired_locale` is what goes into LANG. Nothing tied the two together, so this
config installs cleanly and converges — and the machine ends up announcing a
locale that was never built:

    "selected_locales": ["en_US.UTF-8 UTF-8"],
    "desired_locale":   "es_ES.UTF-8"

Cross-field coherence on the expanded config is exactly preflight's job. A
warning rather than an error: C.UTF-8 and POSIX are built into glibc and need no
generation, and a target may already carry locales an earlier generation built.
"""
from dasik.lib.validation.preflight import has_errors, preflight


def _messages(locales):
    return [i.message for i in preflight({"packages": ["base"], "locales": locales},
                                         efi_boot=True)
            if i.code == "lang_never_generated"]


def test_a_lang_outside_the_generated_set_is_flagged():
    messages = _messages({"selected_locales": ["en_US.UTF-8 UTF-8"],
                          "desired_locale": "es_ES.UTF-8",
                          "desired_tty_layout": "us"})

    assert len(messages) == 1
    assert "es_ES.UTF-8" in messages[0] and "en_US.UTF-8" in messages[0]


def test_the_matching_pair_is_quiet():
    assert _messages({"selected_locales": ["en_US.UTF-8 UTF-8"],
                      "desired_locale": "en_US.UTF-8",
                      "desired_tty_layout": "us"}) == []


def test_the_charset_half_is_not_part_of_the_name():
    """`en_US.UTF-8 UTF-8` in locale.gen IS `en_US.UTF-8` as a LANG."""
    assert _messages({"selected_locales": ["en_US.UTF-8 UTF-8", "es_ES.UTF-8 UTF-8"],
                      "desired_locale": "es_ES.UTF-8",
                      "desired_tty_layout": "us"}) == []


def test_the_builtin_locales_need_no_generation():
    for builtin in ("C.UTF-8", "C", "POSIX"):
        assert _messages({"selected_locales": ["en_US.UTF-8 UTF-8"],
                          "desired_locale": builtin,
                          "desired_tty_layout": "us"}) == [], builtin


def test_it_never_becomes_an_error():
    issues = preflight({"packages": ["base"],
                        "locales": {"selected_locales": ["en_US.UTF-8 UTF-8"],
                                    "desired_locale": "es_ES.UTF-8",
                                    "desired_tty_layout": "us"}}, efi_boot=True)

    assert not has_errors(issues)


def test_a_config_without_locales_is_quiet():
    assert [i for i in preflight({"packages": ["base"]}, efi_boot=True)
            if i.code == "lang_never_generated"] == []

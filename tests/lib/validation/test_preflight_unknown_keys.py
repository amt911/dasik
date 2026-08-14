"""A key dasik does not know is silence today; say it out loud.

The config file IS the interface, and a typo in it produces a machine quietly
missing the feature:

    "firewal": {"allowed_services": ["ssh"]}     -> no firewall, no message

The model deliberately IGNORES unknown top-level keys (see
tests/lib/json_parser/test_unknown_top_level_keys_are_ignored), which keeps a
config written for another version loadable — so this is a preflight WARNING,
not a validation error. It informs; it never aborts.
"""
from dasik.lib.validation.preflight import preflight


def _codes(config):
    return [i.code for i in preflight({"packages": ["base"], **config}, efi_boot=True)]


def _messages(config):
    return [i.message for i in preflight({"packages": ["base"], **config}, efi_boot=True)
            if i.code == "unknown_config_key"]


def test_a_typo_in_a_top_level_block_is_named():
    messages = _messages({"firewal": {"allowed_services": ["ssh"]}})

    assert len(messages) == 1
    assert "firewal" in messages[0]


def test_and_the_key_it_probably_meant_is_suggested():
    assert "firewall" in _messages({"firewal": {}})[0]


def test_the_real_key_says_nothing():
    assert _messages({"firewall": {"allowed_services": ["ssh"]}}) == []


def test_a_typo_inside_a_block_is_named_too():
    messages = _messages({"sudo": {"whel": True}})

    assert len(messages) == 1
    assert "sudo.whel" in messages[0]
    assert "wheel" in messages[0]


def test_metadata_is_free_form_and_never_flagged():
    assert _messages({"metadata": {"note": "anything", "author": "me"}}) == []


def test_a_clean_config_is_quiet():
    assert "unknown_config_key" not in _codes({"hostname": "box"})


def test_the_warning_never_becomes_an_error():
    from dasik.lib.validation.preflight import has_errors
    issues = preflight({"packages": ["base"], "firewal": {}}, efi_boot=True)

    assert not has_errors(issues)


def test_a_map_shaped_block_is_not_treated_as_a_model():
    """`package_sources` is keyed by PACKAGE NAME, not by model field. Descending
    into it flagged every real entry as a typo — found by running the check over
    the repo's own sample configs:

        unknown key 'package_sources.config-saver'
    """
    messages = _messages({"package_sources": {
        "config-saver": {"type": "git", "url": "https://github.com/x/y.git"}}})

    assert messages == []


def test_and_neither_is_zram():
    """Same shape: the keys are device names (`zram0`), chosen by the user."""
    assert _messages({"zram": {"zram0": {"zram-size": "ram/2"}}}) == []


def test_a_real_typo_inside_a_model_block_is_still_caught():
    assert "sudo.whel" in _messages({"sudo": {"whel": True}})[0]

"""A snippet the system will never read is a snippet that does nothing.

Each /etc/*.d directory only loads files with one suffix: sysctl.d, modprobe.d,
modules-load.d, tmpfiles.d and sddm.conf.d read `*.conf`, udev/rules.d reads
`*.rules`, profile.d reads `*.sh`. dasik writes whatever `name` says, verbatim:

    "sysctl_d": [{"name": "swappiness", "content": "vm.swappiness=42"}]
      -> /etc/sysctl.d/swappiness      <- exists, converges, is never read

`plan` is silent afterwards because the file IS what the config asked for. The
config is not wrong, exactly — it just cannot work — so this is a warning that
names the suffix, not an error and not a silent rename.
"""
from dasik.lib.validation.preflight import has_errors, preflight


def _messages(config):
    return [i.message for i in preflight({"packages": ["base"], **config}, efi_boot=True)
            if i.code == "snippet_never_read"]


def test_a_sysctl_snippet_without_conf_is_flagged():
    messages = _messages({"sysctl_d": [{"name": "swappiness", "content": "vm.swappiness=42"}]})

    assert len(messages) == 1
    assert "swappiness" in messages[0] and ".conf" in messages[0]


def test_a_udev_rule_needs_rules():
    messages = _messages({"udev_rules": [{"name": "qudelix", "content": "..."}]})

    assert len(messages) == 1 and ".rules" in messages[0]


def test_a_profile_snippet_needs_sh():
    messages = _messages({"profile_d": [{"name": "editor", "content": "export EDITOR=vim"}]})

    assert len(messages) == 1 and ".sh" in messages[0]


def test_the_right_suffix_is_quiet():
    assert _messages({"sysctl_d": [{"name": "swappiness.conf", "content": "x"}],
                      "udev_rules": [{"name": "qudelix.rules", "content": "x"}],
                      "profile_d": [{"name": "editor.sh", "content": "x"}],
                      "modprobe_conf": [{"name": "nested.conf", "content": "x"}],
                      "modules_load": [{"name": "kvm.conf", "content": "x"}],
                      "tmpfiles_d": [{"name": "cache.conf", "content": "x"}],
                      "sddm_conf_d": [{"name": "theme.conf", "content": "x"}]}) == []


def test_every_wrong_one_is_named():
    messages = _messages({"sysctl_d": [{"name": "a", "content": "x"},
                                       {"name": "b.conf", "content": "x"}],
                          "udev_rules": [{"name": "c", "content": "x"}]})

    assert len(messages) == 2


def test_the_free_form_files_section_is_not_second_guessed():
    """`files` takes absolute paths the user chose deliberately."""
    assert _messages({"files": [{"path": "/etc/sysctl.d/swappiness", "content": "x"}]}) == []


def test_it_never_becomes_an_error():
    issues = preflight({"packages": ["base"],
                        "sysctl_d": [{"name": "swappiness", "content": "x"}]}, efi_boot=True)

    assert not has_errors(issues)

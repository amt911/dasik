"""A `files` entry aimed at a path another domain owns can never converge.

Two writers, one path, different content by construction: `files` writes its
content, the owning domain writes the domain's, and the next plan proposes both
again — forever, with every apply reporting success. Driven by hand:

    round 1: files=['CREATE'] sudo=['MODIFY'] -> '%wheel ALL=(ALL:ALL) ALL'
    round 2: files=['MODIFY'] sudo=['MODIFY'] -> …

`sync` never produces such a config (discovery skips dasik's own artifacts), so
this is only reachable by hand — but by hand it is easy, and nothing said a word.
"""
from dasik.lib.validation.preflight import preflight


def _issues(config, code):
    return [i for i in preflight({"packages": ["base", "sudo"], **config}, efi_boot=True)
            if i.code == code]


def test_a_files_entry_over_dasiks_sudoers_fragment_is_flagged():
    issues = _issues({"files": [{"path": "/etc/sudoers.d/10-dasik", "content": "x"}]},
                     "file_owned_by_another_domain")

    assert len(issues) == 1
    assert issues[0].level == "warning"
    assert "sudo" in issues[0].message


def test_the_zram_generator_config_too():
    issues = _issues({"files": [{"path": "/etc/systemd/zram-generator.conf", "content": "x"}]},
                     "file_owned_by_another_domain")

    assert [i.level for i in issues] == ["warning"]
    assert "zram" in issues[0].message


def test_every_colliding_entry_is_named_once():
    issues = _issues({"files": [
        {"path": "/etc/sudoers.d/10-dasik", "content": "x"},
        {"path": "/etc/systemd/zram-generator.conf", "content": "y"},
        {"path": "/etc/motd", "content": "hello"},
    ]}, "file_owned_by_another_domain")

    assert len(issues) == 2


def test_an_ordinary_file_is_not_flagged():
    assert _issues({"files": [{"path": "/etc/motd", "content": "hello"}]},
                   "file_owned_by_another_domain") == []


def test_crypttab_is_not_a_collision():
    """dasik itself captures /etc/crypttab into `files`; that pairing is intended."""
    assert _issues({"files": [{"path": "/etc/crypttab", "content": "swap /dev/vda3 /dev/urandom"}]},
                   "file_owned_by_another_domain") == []


def test_a_config_with_no_files_block_is_quiet():
    assert _issues({}, "file_owned_by_another_domain") == []

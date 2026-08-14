"""dasik must not reject the /etc/crypttab it writes itself.

Found in a VM: an encrypted machine with dracut has

    cryptroot UUID=… none luks,x-initrd.attach

written by dasik's own dracut backend. `sync` captures the file verbatim into
`files`, and `dasik check` on that capture then fails:

    [error] crypttab_bad_option: /etc/crypttab entry 'cryptroot':
            unknown option 'x-initrd.attach'

`x-initrd.attach` is a bare flag in crypttab(5); it sat in the key=value table,
so the bare form read as unknown. Every encrypted machine's capture was
unusable — the same class as #196, with dasik refusing its own output.
"""
import pytest

from dasik.lib.validation.preflight import preflight

_DISKS = {"disks": [{"device": "/dev/vda", "partition_table": "gpt", "partitions": [
    {"label": "ROOT", "size": "rest", "filesystem": "btrfs", "partition_type": "linux",
     "mountpoint": "/", "encrypt": True, "luks_name": "cryptroot",
     "luks_uuid": "0ed69442-a99e-5f65-88d8-28d3e9945408"}]}]}


def _codes(options):
    cfg = {"disks": _DISKS, "files": [{
        "path": "/etc/crypttab",
        "content": f"cryptroot UUID=0ed69442-a99e-5f65-88d8-28d3e9945408 none {options}\n"}]}
    return {i.code for i in preflight(cfg, efi_boot=True)}


def test_the_line_dasik_writes_for_a_dracut_machine_is_accepted():
    assert "crypttab_bad_option" not in _codes("luks,x-initrd.attach")


@pytest.mark.parametrize("flag", [
    "x-initrd.attach", "keyfile-erase", "nofail", "discard", "headless",
])
def test_bare_flags_are_bare(flag):
    assert "crypttab_bad_option" not in _codes(f"luks,{flag}")


@pytest.mark.parametrize("opt", [
    "keyfile-offset=4096", "timeout=30s", "tpm2-device=auto", "fido2-device=auto",
    "x-systemd.device-timeout=10s", "sector-size=4096",
])
def test_valued_options_still_need_their_value(opt):
    assert "crypttab_bad_option" not in _codes(f"luks,{opt}")


def test_an_option_that_needs_a_value_is_refused_bare():
    """The check still earns its keep: `size` without a value is the malformed
    `size512` shape that started all this."""
    assert "crypttab_bad_option" in _codes("luks,size")


def test_a_flag_given_a_value_is_refused():
    assert "crypttab_bad_option" in _codes("luks,discard=yes")


def test_try_empty_password_takes_both_forms():
    """crypttab(5) documents it as `try-empty-password[=bool]`."""
    assert "crypttab_bad_option" not in _codes("luks,try-empty-password")
    assert "crypttab_bad_option" not in _codes("luks,try-empty-password=yes")


def test_a_genuinely_unknown_option_is_still_an_error():
    assert "crypttab_bad_option" in _codes("luks,size512")

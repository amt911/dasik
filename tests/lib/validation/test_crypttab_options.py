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


# --- options crypttab(5) (systemd 262) documents that the table lacked -------
# Found by the nofail data-volume journey: the crypttab line dasik composes
# for an optional data volume carries the declared luks_options verbatim, and
# `check` then refused `headless=true` — crypttab(5) documents `headless=` as a
# boolean, so the valued form is the documented one.

@pytest.mark.parametrize("opt", [
    "headless=true", "headless=false", "keyfile-timeout=10s",
    "password-cache=read-only", "password-echo=masked",
    "fido2-pin=yes", "fido2-up=no", "fido2-uv=no", "tpm2-pin=yes",
    "tpm2-pcrlock=/var/lib/systemd/pcrlock.json", "tpm2-measure-bank=sha256",
    "tpm2-measure-keyslot-nvpcr=cryptsetup", "tcrypt-keyfile=/k",
    "link-volume-key=@u::%user:cryptsetup:v", "fixate-volume-key=abc",
])
def test_documented_options_are_accepted(opt):
    assert "crypttab_bad_option" not in _codes(f"luks,{opt}")


def test_the_line_dasik_composes_for_an_optional_data_volume_passes_check():
    cfg = {"initramfs": "dracut", "disks": {"disks": [
        {"device": "/dev/vda", "partition_table": "gpt", "partitions": [
            {"label": "ROOT", "size": "rest", "filesystem": "btrfs",
             "partition_type": "linux", "mountpoint": "/", "encrypt": True,
             "luks_name": "cryptroot"}]},
        {"device": "/dev/vdb", "partition_table": "gpt", "wipe_disk": False, "partitions": [
            {"label": "Games", "size": "rest", "filesystem": "btrfs", "format": False,
             "partition_type": "linux", "encrypt": True, "luks_name": "games",
             "luks_uuid": "5eed5eed-0000-4000-8000-00000000c0de", "unlock_fido2": 2,
             "luks_options": ["token-timeout=10s", "nofail", "headless=true"],
             "btrfs_subvolumes": [{"name": "@", "mountpoint": "/home/u/Games"}]}]}]}}
    from dasik.lib.actions.initramfs.dracut import DracutBackend
    cfg["files"] = [{"path": "/etc/crypttab", "content": DracutBackend(cfg).crypttab()}]
    assert "crypttab_bad_option" not in {i.code for i in preflight(cfg, efi_boot=True)}

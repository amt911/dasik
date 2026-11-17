"""A hardware-token LUKS unlock needs the library systemd-cryptsetup loads.

systemd only lists libfido2 / tpm2-tss as OPTIONAL dependencies, so nothing
pulls them in: on a VM installed from config/vm-preserved-luks.json the boot
logged `Shared library 'libfido2.so.1' is not available` and the FIDO2 token
was never tried. On the real tower libfido2 was present only because freerdp
happens to require it.
"""
from dasik.lib.expand import expand_config
from dasik.lib.expand.toggles import expand_luks_tokens


def _cfg(**part):
    base = {"label": "root", "encrypt": True, "luks_name": "cryptroot"}
    base.update(part)
    return {"disks": {"disks": [{"device": "/dev/vda", "partitions": [base]}]}}


def test_fido2_unlock_brings_libfido2():
    assert expand_luks_tokens(_cfg(unlock_fido2=2)) == {"packages": ["libfido2"]}


def test_tpm2_unlock_brings_tpm2_tss():
    assert expand_luks_tokens(_cfg(unlock_tpm2=True)) == {"packages": ["tpm2-tss"]}


def test_both_bring_both():
    out = expand_luks_tokens(_cfg(unlock_fido2=True, unlock_tpm2=True))
    assert out == {"packages": ["libfido2", "tpm2-tss"]}


def test_nothing_without_a_token_unlock():
    assert expand_luks_tokens(_cfg()) == {}
    assert expand_luks_tokens(_cfg(unlock_fido2=0, unlock_tpm2=False)) == {}
    assert expand_luks_tokens(_cfg(unlock_fido2=2, encrypt=False)) == {}
    assert expand_luks_tokens({}) == {}


def test_registered_in_expand_config():
    out = expand_config({"packages": ["base"], **_cfg(unlock_fido2=2)})
    assert "libfido2" in out["packages"]

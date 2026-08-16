"""TPM2 / FIDO2 LUKS auto-unlock: systemd-cryptenroll + rd.luks.options.

TPM2 gives passwordless boot (verifiable in QEMU via swtpm); FIDO2 needs a
physical token (enroll + boot). Both enroll a keyslot with systemd-cryptenroll
(authorised by the passphrase via $PASSWORD) and add rd.luks.options=<uuid>=…
for sd-encrypt. These tests pin the command/env + cmdline construction.
"""
from types import SimpleNamespace
from unittest.mock import patch

from dasik.lib.actions.disk_partition_action import DiskPartitionAction
from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction
from dasik.lib.actions.luks_uuid import luks_uuid
from dasik.lib.command_worker.command_worker import Command
from dasik.lib.models.disk_model import Partition


def test_command_execute_merges_env(monkeypatch):
    import dasik.lib.command_worker.command_worker as cw
    cap = {}
    monkeypatch.setattr(cw, "which", lambda n: "/usr/bin/x")
    monkeypatch.setattr(cw.subprocess, "run", lambda a, **k: cap.update(k) or SimpleNamespace(stdout=b""))
    Command.execute("systemd-cryptenroll", ["--tpm2-device=auto", "/dev/vda2"], env={"PASSWORD": "pw"})
    assert cap["env"]["PASSWORD"] == "pw"       # merged over os.environ
    assert "PATH" in cap["env"]


def test_model_accepts_tpm2_fido2():
    p = Partition(label="ROOT", size="rest", filesystem="ext4", encrypt=True,
                  luks_name="cryptroot", luks_password="pw",
                  unlock_tpm2=True, unlock_fido2=True)
    assert p.unlock_tpm2 and p.unlock_fido2


# The enrolment itself moved to LuksTokenAction (issue #242): inside the disk
# action it only ever ran while FORMATTING, so an installed machine could never
# gain a token. What it does with the passphrase is unchanged and asserted in
# tests/lib/actions/test_luks_token_action.py.


def _cmdline(**flags):
    part = {"mountpoint": "/", "filesystem": "ext4", "encrypt": True, "luks_name": "cryptroot", **flags}
    a = KernelCmdlineAction({"bootloader": "sd-boot", "disks": {"disks": [{"partitions": [part]}]}},
                            context=SimpleNamespace(target=None))
    return a._derive_from_disks()


def test_tpm2_emits_rd_luks_options():
    u = luks_uuid("cryptroot")
    assert f"rd.luks.options={u}=tpm2-device=auto" in _cmdline(unlock_tpm2=True)


def test_tpm2_and_fido2_combined_options():
    u = luks_uuid("cryptroot")
    assert f"rd.luks.options={u}=tpm2-device=auto,fido2-device=auto" in _cmdline(unlock_tpm2=True, unlock_fido2=True)


def test_no_hardware_no_rd_luks_options():
    assert not any(t.startswith("rd.luks.options=") for t in _cmdline())


def test_luks_options_appended_after_auto_options():
    # e.g. the user's real cmdline: fido2-device=auto,token-timeout=10s
    u = luks_uuid("cryptroot")
    line = f"rd.luks.options={u}=fido2-device=auto,token-timeout=10s"
    assert line in _cmdline(unlock_fido2=True, luks_options=["token-timeout=10s"])


def test_luks_options_alone_without_hardware():
    u = luks_uuid("cryptroot")
    assert f"rd.luks.options={u}=token-timeout=10s" in _cmdline(luks_options=["token-timeout=10s"])

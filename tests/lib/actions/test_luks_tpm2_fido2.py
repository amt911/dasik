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


def _enroll(kind):
    p = Partition(label="ROOT", size="rest", filesystem="ext4", encrypt=True,
                  luks_name="cryptroot", luks_password="pw")
    a = DiskPartitionAction(config=None)
    with patch("dasik.lib.actions.disk_partition_action.Command.execute") as ex:
        a._enroll_cryptenroll("/dev/vda2", p, kind)
    return ex.call_args


def test_tpm2_enroll_calls_cryptenroll_with_password_env():
    c = _enroll("--tpm2-device=auto")
    assert c.args == ("systemd-cryptenroll", ["--tpm2-device=auto", "/dev/vda2"])
    assert c.kwargs["env"] == {"PASSWORD": "pw"}


def test_enroll_skipped_without_password():
    p = Partition(label="ROOT", size="rest", filesystem="ext4", encrypt=True,
                  luks_name="cryptroot", unlock_tpm2=True)   # no password
    a = DiskPartitionAction(config=None)
    with patch("dasik.lib.actions.disk_partition_action.Command.execute") as ex:
        a._enroll_cryptenroll("/dev/vda2", p, "--tpm2-device=auto")
    ex.assert_not_called()


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

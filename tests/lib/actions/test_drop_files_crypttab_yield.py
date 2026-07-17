"""DropFilesAction yields /etc/crypttab ownership to the dracut backend.

When the initramfs generator is dracut AND encryption is declared, DracutBackend
is the single writer of /etc/crypttab (it composes the derived root entry +
captured non-root lines). DropFilesAction must NOT plan/write/own that file, or
the two actions fight over it on every apply (a non-idempotent oscillation).
"""
from __future__ import annotations

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.drop_files_action import DropFilesAction
from dasik.lib.state.change import Op
from dasik.lib.target.target import Target


def _ctx(root="/"):
    return ActionContext(target=Target(root=root))


def _cfg(initramfs="dracut", encrypt=True, extra_files=None):
    part = {"filesystem": "btrfs", "mountpoint": "/"}
    if encrypt:
        part["encrypt"] = True
        part["luks_name"] = "cryptroot"
    files = [{"path": "/etc/crypttab", "content": "swap LABEL=cryptswap /dev/urandom swap\n"}]
    if extra_files:
        files += extra_files
    return {
        "initramfs": initramfs,
        "disks": {"disks": [{"partitions": [part]}]},
        "files": files,
    }


def test_dracut_encrypted_yields_crypttab_from_desired():
    a = DropFilesAction(_cfg(), _ctx("/"))
    assert "/etc/crypttab" not in a._desired()
    assert "/etc/crypttab" not in a.managed_keys()["files"]


def test_dracut_encrypted_still_owns_other_files():
    extra = [{"path": "/etc/vconsole.conf", "content": "KEYMAP=us\n"}]
    a = DropFilesAction(_cfg(extra_files=extra), _ctx("/"))
    assert "/etc/vconsole.conf" in a._desired()


def test_mkinitcpio_keeps_crypttab_ownership():
    # not dracut -> DropFiles owns /etc/crypttab as usual
    a = DropFilesAction(_cfg(initramfs="mkinitcpio"), _ctx("/"))
    assert "/etc/crypttab" in a._desired()


def test_dracut_without_encryption_keeps_crypttab_ownership():
    # dracut but no declared encryption -> nothing to compose, DropFiles owns it
    a = DropFilesAction(_cfg(encrypt=False), _ctx("/"))
    assert "/etc/crypttab" in a._desired()


def test_dracut_encrypted_plan_does_not_touch_crypttab():
    a = DropFilesAction(_cfg(), _ctx("/"))
    changes = a.plan(managed=[])
    crypttab_changes = [c for c in changes if c.item == "/etc/crypttab"]
    assert crypttab_changes == []

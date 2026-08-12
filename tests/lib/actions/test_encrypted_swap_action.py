"""The two lines nobody else writes for a random-key swap.

`genfstab` runs during the install and can only see what is mounted, and
/dev/mapper/swap does not exist until the FIRST boot creates it from the
crypttab entry — so the fstab line has to be appended afterwards or the swap is
simply never activated. /etc/crypttab is dracut's when dracut is the generator;
with mkinitcpio nobody composes it and this action does.
"""
import os
from types import SimpleNamespace

import pytest

from dasik.lib.actions.encrypted_swap_action import EncryptedSwapAction
from dasik.lib.state.change import Op


class _Target:
    def __init__(self, root):
        self.root = str(root)

    def path(self, canonical):
        return os.path.join(self.root, canonical.lstrip("/"))


@pytest.fixture
def target(tmp_path):
    os.makedirs(tmp_path / "etc", exist_ok=True)
    (tmp_path / "etc" / "fstab").write_text("UUID=abc / btrfs defaults 0 0\n")
    return _Target(tmp_path)


def _cfg(**over):
    cfg = {"disks": {"disks": [{"device": "/dev/vda", "partitions": [
        {"label": "swap", "filesystem": "swap", "swap_encryption": "random"}]}]}}
    cfg.update(over)
    return cfg


def _action(cfg, target):
    return EncryptedSwapAction(cfg, SimpleNamespace(target=target))


def _read(target, canonical):
    path = target.path(canonical)
    return open(path).read() if os.path.exists(path) else ""


def test_plan_installs_a_declared_swap_that_the_target_lacks(target):
    changes = _action(_cfg(), target).plan(managed=[])
    assert [(c.op, c.item) for c in changes] == [(Op.INSTALL, "swap")]


def test_apply_writes_both_lines(target):
    action = _action(_cfg(), target)
    action.apply(action.plan(managed=[]))
    assert "/dev/mapper/swap none swap defaults 0 0" in _read(target, "/etc/fstab")
    assert "swap LABEL=cryptswap /dev/urandom" in _read(target, "/etc/crypttab")


def test_the_existing_fstab_is_kept(target):
    action = _action(_cfg(), target)
    action.apply(action.plan(managed=[]))
    assert "UUID=abc / btrfs defaults 0 0" in _read(target, "/etc/fstab")


def test_a_second_plan_after_apply_is_silent(target):
    action = _action(_cfg(), target)
    action.apply(action.plan(managed=[]))
    assert _action(_cfg(), target).plan(managed=["swap"]) == []


def test_applying_twice_writes_the_line_once(target):
    for _ in range(2):
        action = _action(_cfg(), target)
        action.apply(action.plan(managed=[]))
    assert _read(target, "/etc/fstab").count("/dev/mapper/swap") == 1


_LUKS_ROOT = {"label": "root", "filesystem": "ext4", "mountpoint": "/",
              "encrypt": True, "luks_name": "cryptroot"}


def _cfg_dracut_luks():
    """dracut AND a LUKS volume — the only case where dracut writes crypttab."""
    cfg = _cfg(initramfs="dracut")
    cfg["disks"]["disks"][0]["partitions"].append(_LUKS_ROOT)
    return cfg


def test_dracut_without_encryption_does_not_own_the_crypttab(target):
    """VM-proven: InitramfsAction only runs its backend when the initramfs
    domain plans a change. With no LUKS the dracut config is empty, the action
    no-ops, and nothing writes /etc/crypttab — so yielding the file on
    `initramfs: dracut` alone left the swap with no mapper at all."""
    action = _action(_cfg(initramfs="dracut"), target)
    action.apply(action.plan(managed=[]))

    assert "swap LABEL=cryptswap" in _read(target, "/etc/crypttab")


def test_dracut_owns_the_crypttab_so_this_action_only_writes_fstab(target):
    action = _action(_cfg_dracut_luks(), target)
    action.apply(action.plan(managed=[]))
    assert "/dev/mapper/swap" in _read(target, "/etc/fstab")
    assert not os.path.exists(target.path("/etc/crypttab"))


def test_under_dracut_convergence_does_not_wait_on_a_crypttab_it_does_not_own(target):
    """dracut writes /etc/crypttab later in the run. If this action judged
    itself unconverged by that file's absence it would re-plan forever."""
    action = _action(_cfg_dracut_luks(), target)
    action.apply(action.plan(managed=[]))
    assert _action(_cfg_dracut_luks(), target).plan(managed=["swap"]) == []


def test_an_owned_swap_no_longer_declared_is_removed(target):
    action = _action(_cfg(), target)
    action.apply(action.plan(managed=[]))
    dropped = _action({}, target)
    changes = dropped.plan(managed=["swap"])
    assert [(c.op, c.item) for c in changes] == [(Op.REMOVE, "swap")]
    dropped.apply(changes)
    assert "/dev/mapper/swap" not in _read(target, "/etc/fstab")
    assert "LABEL=cryptswap" not in _read(target, "/etc/crypttab")


def test_a_swap_this_tool_never_owned_is_left_alone(target):
    """Someone else's encrypted swap is not dasik's to delete: ownership, not
    presence, is what authorises a REMOVE."""
    with open(target.path("/etc/fstab"), "a") as f:
        f.write("/dev/mapper/other none swap defaults 0 0\n")
    changes = _action({}, target).plan(managed=[])
    assert changes == []


def test_an_undeclared_config_plans_nothing(target):
    assert _action({}, target).plan(managed=[]) == []


def test_the_manifest_records_the_mapper_name(target):
    assert _action(_cfg(), target).managed_keys() == {"swap_encryption": ["swap"]}


def test_capture_belongs_to_the_disks_block(target):
    # Two actions emitting `disks` would clobber each other — ConfigWriter.merge
    # overwrites a key, it cannot merge two halves of one.
    assert _action(_cfg(), target).import_state() == {}

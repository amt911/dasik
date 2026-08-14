"""A hook dasik added must go when its reason goes.

Dropping the `plymouth` block from a working config produced this plan:

    - [packages] remove plymouth  (no longer declared)
    ~ [initramfs] modify HOOKS=(base btrfs systemd plymouth keyboard …)  (set)

The package leaves and the hook stays. `mkinitcpio -P` then fails —

    ==> ERROR: Hook 'plymouth' cannot be found

— the apply aborts with a partial generation, and the machine is left where
every future kernel update fails the same way, because the hook list on disk
names a hook no package provides any more.

The hook computation only ever ADDED: it starts from what is on disk and layers
what the config asks for. Nothing subtracted what the config had stopped asking
for.
"""
import pytest

from dasik.lib.actions.initramfs.mkinitcpio import MkinitcpioBackend


class _Backend(MkinitcpioBackend):
    """The pure hook computation, with the on-disk HOOKS injected."""

    def __init__(self, current, **flags):
        self._current = current
        for k, v in flags.items():
            setattr(self, k, v)

    def _raw_hooks(self):
        return list(self._current)


def _hooks(current, plymouth):
    b = _Backend(current, has_plymouth=plymouth, has_encryption=False,
                 has_hibernation=False, root_fs="ext4", has_bluetooth=False,
                 plymouth_theme=None, keyboard_early=False)
    return b._compute(b._raw_hooks())


_WITH = ["base", "udev", "plymouth", "autodetect", "modconf", "kms", "block",
         "filesystems", "fsck"]


def test_the_hook_goes_when_the_block_goes():
    assert "plymouth" not in _hooks(_WITH, plymouth=False)


def test_and_the_rest_of_the_list_is_untouched():
    after = _hooks(_WITH, plymouth=False)
    assert after == [h for h in _WITH if h != "plymouth"]


def test_a_declared_plymouth_still_gets_its_hook():
    assert "plymouth" in _hooks(_WITH, plymouth=True)


def test_it_is_still_added_when_missing():
    without = [h for h in _WITH if h != "plymouth"]
    assert "plymouth" in _hooks(without, plymouth=True)


def test_removing_it_twice_is_stable():
    once = _hooks(_WITH, plymouth=False)
    assert _hooks(once, plymouth=False) == once

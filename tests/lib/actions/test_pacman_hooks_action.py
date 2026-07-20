"""PacmanHooksAction — mkinitcpio neutralizers must exist BEFORE the first pacman run.

F-10: the neutralizer hooks were contributed to the `files` domain, written by
DropFilesAction, which the registry runs *after* Packages. So every transaction
that installs a kernel/systemd/DKMS package — including pacstrap — still fired
mkinitcpio's hooks. The 2026-07-19 log shows the consequence: a dracut hook ran,
then mkinitcpio immediately overwrote /boot/initramfs-linux.img with an image
that has no sd-encrypt, i.e. no way to open the LUKS root.
"""
from types import SimpleNamespace

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.pacman_hooks_action import PacmanHooksAction
from dasik.lib.expand.toggles import MKINITCPIO_HOOKS, NEUTRALIZER_MARKER
from dasik.lib.state.change import Op
from dasik.lib.target.target import Target


def _a(cfg, root):
    return PacmanHooksAction(cfg, ActionContext(target=Target(root=str(root))))


def _hook(tmp_path, name):
    return tmp_path / "etc" / "pacman.d" / "hooks" / name


def test_dracut_plans_both_neutralizers(tmp_path):
    a = _a({"initramfs": "dracut"}, tmp_path)
    changes = a.plan(managed=[])
    assert sorted(c.item for c in changes) == sorted(MKINITCPIO_HOOKS)
    assert all(c.op is Op.MODIFY for c in changes)


def test_apply_writes_neutralizers_with_the_marker(tmp_path):
    a = _a({"initramfs": "dracut"}, tmp_path)
    a.apply(a.plan(managed=[]))
    for name in MKINITCPIO_HOOKS:
        assert NEUTRALIZER_MARKER in _hook(tmp_path, name).read_text()


def test_converged_target_plans_nothing(tmp_path):
    a = _a({"initramfs": "dracut"}, tmp_path)
    a.apply(a.plan(managed=[]))
    assert a.plan(managed=list(MKINITCPIO_HOOKS)) == []


def test_mkinitcpio_generator_plans_nothing_on_clean_target(tmp_path):
    a = _a({"initramfs": "mkinitcpio"}, tmp_path)
    assert a.plan(managed=[]) == []


def test_switching_back_to_mkinitcpio_removes_the_neutralizers(tmp_path):
    _a({"initramfs": "dracut"}, tmp_path).apply(
        _a({"initramfs": "dracut"}, tmp_path).plan(managed=[]))
    a = _a({"initramfs": "mkinitcpio"}, tmp_path)
    changes = a.plan(managed=list(MKINITCPIO_HOOKS))
    assert {c.op for c in changes} == {Op.REMOVE}
    a.apply(changes)
    assert not _hook(tmp_path, MKINITCPIO_HOOKS[0]).exists()


def test_foreign_hook_of_the_same_name_is_left_alone(tmp_path):
    hooks = tmp_path / "etc" / "pacman.d" / "hooks"
    hooks.mkdir(parents=True)
    foreign = hooks / MKINITCPIO_HOOKS[0]
    foreign.write_text("[Trigger]\nTarget = linux\n")
    a = _a({"initramfs": "mkinitcpio"}, tmp_path)
    assert a.plan(managed=list(MKINITCPIO_HOOKS)) == []
    assert foreign.read_text() == "[Trigger]\nTarget = linux\n"


def test_import_state_is_empty(tmp_path):
    """The generator round-trips through InitramfsAction; the hooks are derived."""
    assert _a({"initramfs": "dracut"}, tmp_path).import_state() == {}


# --- registry order -------------------------------------------------------- #

def test_registered_before_base_install_and_packages():
    from dasik.lib.actions.action_registry import get_default_registry
    from dasik.lib.actions.actions_handler_v2 import setup_actions
    setup_actions()
    names = [m["class"].__name__ for m in get_default_registry().get_all_actions()]
    assert names.index("PacmanHooksAction") < names.index("BaseInstallAction")
    assert names.index("PacmanHooksAction") < names.index("PackagesAction")
    assert names.index("DiskPartitionAction") < names.index("PacmanHooksAction")

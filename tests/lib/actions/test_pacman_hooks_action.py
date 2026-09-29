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
from dasik.lib.expand.toggles import (DRACUT_FALLBACK_HOOKS, MKINITCPIO_HOOKS,
                                      NEUTRALIZER_MARKER)
from dasik.lib.state.change import Op
from dasik.lib.target.target import Target


def _a(cfg, root):
    return PacmanHooksAction(cfg, ActionContext(target=Target(root=str(root))))


def _hook(tmp_path, name):
    return tmp_path / "etc" / "pacman.d" / "hooks" / name


ALL_DRACUT_HOOKS = sorted(MKINITCPIO_HOOKS + DRACUT_FALLBACK_HOOKS)


def test_dracut_plans_both_neutralizers_and_the_fallback_hooks(tmp_path):
    a = _a({"initramfs": "dracut"}, tmp_path)
    changes = a.plan(managed=[])
    assert sorted(c.item for c in changes) == ALL_DRACUT_HOOKS
    assert all(c.op is Op.MODIFY for c in changes)


def test_apply_writes_neutralizers_with_the_marker(tmp_path):
    a = _a({"initramfs": "dracut"}, tmp_path)
    a.apply(a.plan(managed=[]))
    for name in MKINITCPIO_HOOKS:
        assert NEUTRALIZER_MARKER in _hook(tmp_path, name).read_text()


def test_converged_target_plans_nothing(tmp_path):
    a = _a({"initramfs": "dracut"}, tmp_path)
    a.apply(a.plan(managed=[]))
    assert a.plan(managed=ALL_DRACUT_HOOKS) == []


def test_mkinitcpio_generator_plans_nothing_on_clean_target(tmp_path):
    a = _a({"initramfs": "mkinitcpio"}, tmp_path)
    assert a.plan(managed=[]) == []


def test_switching_back_to_mkinitcpio_removes_the_neutralizers(tmp_path):
    _a({"initramfs": "dracut"}, tmp_path).apply(
        _a({"initramfs": "dracut"}, tmp_path).plan(managed=[]))
    a = _a({"initramfs": "mkinitcpio"}, tmp_path)
    changes = a.plan(managed=ALL_DRACUT_HOOKS)
    assert {c.op for c in changes} == {Op.REMOVE}
    assert sorted(c.item for c in changes) == ALL_DRACUT_HOOKS
    a.apply(changes)
    for name in ALL_DRACUT_HOOKS:
        assert not _hook(tmp_path, name).exists(), name


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


# --- the fallback image is rebuilt by pacman, like the main one ------------ #
#
# dracut's own 90-dracut-install.hook only writes initramfs-<pkgbase>.img. Without
# a hook of our own the fallback dasik builds at apply time goes stale on the
# first kernel upgrade — the exact rot this image exists to avoid.

def _written(tmp_path, name):
    a = _a({"initramfs": "dracut"}, tmp_path)
    a.apply(a.plan(managed=[]))
    return _hook(tmp_path, name).read_text()


def test_fallback_hook_runs_after_dracut_own_hook(tmp_path):
    install = [n for n in DRACUT_FALLBACK_HOOKS if "remove" not in n]
    assert install and all(n > "90-dracut-install.hook" for n in install)


def test_fallback_hook_fires_on_the_same_triggers_as_dracut(tmp_path):
    body = _written(tmp_path, "91-dasik-dracut-fallback.hook")
    for target in ("usr/lib/modules/*/vmlinuz", "usr/lib/modules/*/pkgbase",
                   "usr/lib/dracut/*", "usr/lib/firmware/*",
                   "usr/lib/systemd/systemd", "usr/bin/cryptsetup"):
        assert f"Target = {target}\n" in body, target
    assert "When = PostTransaction" in body


def test_fallback_hook_builds_a_generic_image_per_kernel(tmp_path):
    body = _written(tmp_path, "91-dasik-dracut-fallback.hook")
    exec_line = next(l for l in body.splitlines() if l.startswith("Exec = "))
    assert exec_line.startswith("Exec = /usr/bin/bash -c '")
    assert "--no-hostonly" in exec_line
    assert '/boot/initramfs-$p-fallback.img' in exec_line
    assert "/usr/lib/modules/*/pkgbase" in exec_line


def test_fallback_hook_and_apply_build_the_same_image(tmp_path):
    """One source for the flags: a hook that built the image differently from
    apply would change its contents on the first kernel upgrade, unseen by a
    plan that compares mtimes only."""
    from dasik.lib.expand.toggles import FALLBACK_DRACUT_FLAGS
    body = _written(tmp_path, "91-dasik-dracut-fallback.hook")
    assert f"dracut {' '.join(FALLBACK_DRACUT_FLAGS)} " in body
    assert FALLBACK_DRACUT_FLAGS == ["--force", "--no-hostonly", "--fstab"]


def test_fallback_hooks_are_planned_with_their_own_reason(tmp_path):
    a = _a({"initramfs": "dracut"}, tmp_path)
    reasons = {c.item: c.reason for c in a.plan(managed=[])}
    for name in DRACUT_FALLBACK_HOOKS:
        assert "neutralizer" not in reasons[name], reasons[name]


def test_remove_hook_deletes_the_fallback_of_a_removed_kernel(tmp_path):
    body = _written(tmp_path, "60-dasik-dracut-fallback-remove.hook")
    assert "Operation = Remove" in body and "When = PreTransaction" in body
    assert "NeedsTargets" in body
    assert 'rm -f "/boot/initramfs-$p-fallback.img"' in body


def test_a_foreign_hook_named_like_the_fallback_hook_is_left_alone(tmp_path):
    hooks = tmp_path / "etc" / "pacman.d" / "hooks"
    hooks.mkdir(parents=True)
    foreign = hooks / "91-dasik-dracut-fallback.hook"
    foreign.write_text("[Trigger]\nTarget = linux\n")
    a = _a({"initramfs": "mkinitcpio"}, tmp_path)
    assert a.plan(managed=ALL_DRACUT_HOOKS) == []


def test_fallback_hook_exec_survives_pacman_word_splitting(tmp_path):
    """alpm splits Exec like a shell (quotes, backslashes). Replaying that split
    with shlex must yield exactly bash, -c and ONE script argument, and bash
    must accept the script's syntax."""
    import shlex
    import subprocess
    for name in DRACUT_FALLBACK_HOOKS:
        body = _written(tmp_path, name)
        exec_line = next(l for l in body.splitlines() if l.startswith("Exec = "))
        argv = shlex.split(exec_line[len("Exec = "):])
        assert argv[:2] == ["/usr/bin/bash", "-c"] and len(argv) == 3, argv
        subprocess.run(["bash", "-n", "-c", argv[2]], check=True)


# --- registry order -------------------------------------------------------- #

def test_registered_before_base_install_and_packages():
    from dasik.lib.actions.action_registry import get_default_registry
    from dasik.lib.actions.actions_handler_v2 import setup_actions
    setup_actions()
    names = [m["class"].__name__ for m in get_default_registry().get_all_actions()]
    assert names.index("PacmanHooksAction") < names.index("BaseInstallAction")
    assert names.index("PacmanHooksAction") < names.index("PackagesAction")
    assert names.index("DiskPartitionAction") < names.index("PacmanHooksAction")

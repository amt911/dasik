"""Initramfs images nothing can boot are pruned — visibly, in the plan.

`dracut --regenerate-all` (and a bare `dracut -f`) names images by kver,
initramfs-<kver>.img, which no boot entry references; kernels that were removed
leave theirs behind too. On a 1 GiB ESP four of them (~560 MB) were enough to
make the next image build fail for lack of space. An image is kept when an
installed kernel owns it by the names the entries use, or when any boot entry
references it; everything else named initramfs-*.img is planned as a REMOVE.
"""
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.initramfs_orphans_action import InitramfsOrphansAction
from dasik.lib.state.change import Op
from dasik.lib.target.target import Target


def _kernel(root, kver, pkgbase):
    d = root / "usr/lib/modules" / kver
    d.mkdir(parents=True)
    (d / "pkgbase").write_text(pkgbase + "\n")


def _images(root, *names):
    boot = root / "boot"
    boot.mkdir(exist_ok=True)
    for name in names:
        (boot / name).write_text("img")


def _plan(root, managed=()):
    action = InitramfsOrphansAction({}, ActionContext(target=Target(root=str(root))))
    return action, action.plan(managed=list(managed))


def test_kver_named_and_removed_kernel_images_are_planned(tmp_path):
    _kernel(tmp_path, "7.2.7-arch1-1", "linux")
    _images(tmp_path, "initramfs-linux.img", "initramfs-linux-fallback.img",
            "initramfs-7.2.7-arch1-1.img", "initramfs-6.19.14-arch1-1.img",
            "amd-ucode.img", "vmlinuz-linux")
    _, changes = _plan(tmp_path)
    assert [(c.op, c.item) for c in changes] == [
        (Op.REMOVE, "initramfs-6.19.14-arch1-1.img"),
        (Op.REMOVE, "initramfs-7.2.7-arch1-1.img"),
    ]


def test_apply_deletes_only_the_planned_images_and_converges(tmp_path):
    _kernel(tmp_path, "7.2.7-arch1-1", "linux")
    _images(tmp_path, "initramfs-linux.img", "initramfs-linux-fallback.img",
            "initramfs-7.0.14-arch1-1.img")
    action, changes = _plan(tmp_path)
    action.apply(changes)
    assert sorted(p.name for p in (tmp_path / "boot").iterdir()) == [
        "initramfs-linux-fallback.img", "initramfs-linux.img"]
    assert action.plan(managed=[]) == []


def test_every_installed_kernel_keeps_both_its_images(tmp_path):
    _kernel(tmp_path, "7.2.7-arch1-1", "linux")
    _kernel(tmp_path, "6.12.40-1-lts", "linux-lts")
    _images(tmp_path, "initramfs-linux.img", "initramfs-linux-fallback.img",
            "initramfs-linux-lts.img", "initramfs-linux-lts-fallback.img")
    assert _plan(tmp_path)[1] == []


def test_the_fallback_of_a_removed_kernel_is_planned(tmp_path):
    _kernel(tmp_path, "7.2.7-arch1-1", "linux")
    _images(tmp_path, "initramfs-linux.img",
            "initramfs-linux-zen.img", "initramfs-linux-zen-fallback.img")
    assert sorted(c.item for c in _plan(tmp_path)[1]) == [
        "initramfs-linux-zen-fallback.img", "initramfs-linux-zen.img"]


def test_an_image_a_systemd_boot_entry_loads_is_kept(tmp_path):
    _kernel(tmp_path, "7.2.7-arch1-1", "linux")
    _images(tmp_path, "initramfs-linux.img", "initramfs-custom.img")
    entries = tmp_path / "boot/loader/entries"
    entries.mkdir(parents=True)
    (entries / "custom.conf").write_text(
        "title c\nlinux /vmlinuz-linux\ninitrd /initramfs-custom.img\noptions rw\n")
    assert _plan(tmp_path)[1] == []


def test_an_image_grub_loads_is_kept(tmp_path):
    _kernel(tmp_path, "7.2.7-arch1-1", "linux")
    _images(tmp_path, "initramfs-linux.img", "initramfs-7.2.7-arch1-1.img")
    grub = tmp_path / "boot/grub"
    grub.mkdir(parents=True)
    (grub / "grub.cfg").write_text(
        "menuentry 'x' {\n\tinitrd\t/amd-ucode.img /initramfs-7.2.7-arch1-1.img\n}\n")
    assert _plan(tmp_path)[1] == []


def test_nothing_is_planned_before_a_kernel_is_installed(tmp_path):
    """Pre-pacstrap, or a target whose modules dir is unreadable: without a
    kernel to anchor on, EVERY image would look orphaned."""
    _images(tmp_path, "initramfs-linux.img", "initramfs-linux-fallback.img")
    assert _plan(tmp_path)[1] == []


def test_directories_and_symlinks_are_never_candidates(tmp_path):
    _kernel(tmp_path, "7.2.7-arch1-1", "linux")
    _images(tmp_path, "initramfs-linux.img")
    (tmp_path / "boot/initramfs-dir.img").mkdir()
    (tmp_path / "boot/initramfs-link.img").symlink_to("initramfs-linux.img")
    assert _plan(tmp_path)[1] == []


def test_sync_captures_nothing(tmp_path):
    action, _ = _plan(tmp_path)
    assert action.import_state() == {}


def test_registered_after_the_initramfs_is_built():
    from dasik.lib.actions.action_registry import get_default_registry
    from dasik.lib.actions.actions_handler_v2 import setup_actions
    setup_actions()
    names = [m["class"].__name__ for m in get_default_registry().get_all_actions()]
    assert names.index("InitramfsAction") < names.index("InitramfsOrphansAction")

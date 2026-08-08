"""Destructiveness is a property of the CHANGE, not just of its op.

`Change.destructive` used to be membership in {REMOVE, DISABLE, DELETE}, while
DiskPartitionAction emits its repartition step as `Op.INSTALL`. So the single
most destructive thing dasik does — `wipefs --all` + `sgdisk --zap-all` + mkfs —
sailed past the confirmation prompt that `pacman -R vim` has to pass.
"""
from dasik.lib.state.change import Change, Op, Plan


def test_removing_something_is_still_destructive():
    assert Change("packages", Op.REMOVE, "vim").destructive is True
    assert Change("systemd", Op.DISABLE, "sshd.service").destructive is True
    assert Change("users", Op.DELETE, "bob").destructive is True


def test_an_ordinary_install_is_not_destructive():
    assert Change("packages", Op.INSTALL, "vim").destructive is False


def test_a_change_can_declare_itself_destructive():
    """The disk domain knows it is about to erase a device; the op cannot."""
    wipe = Change("disks", Op.INSTALL, "/dev/nvme0n1",
                  reason="wipe_disk", destructive=True)
    assert wipe.destructive is True


def test_plan_lists_a_declared_destructive_change():
    wipe = Change("disks", Op.INSTALL, "/dev/sda", reason="wipe_disk",
                  destructive=True)
    plan = Plan([Change("packages", Op.INSTALL, "vim"), wipe])
    assert plan.destructive() == [wipe]


def test_declared_flag_cannot_downgrade_a_destructive_op():
    """destructive=False must never make a REMOVE look safe."""
    assert Change("packages", Op.REMOVE, "vim", destructive=False).destructive is True


def test_render_marks_a_declared_destructive_change():
    line = Change("disks", Op.INSTALL, "/dev/sda", reason="wipe_disk",
                  destructive=True).render()
    assert "DESTRUCTIVE" in line
    assert "/dev/sda" in line

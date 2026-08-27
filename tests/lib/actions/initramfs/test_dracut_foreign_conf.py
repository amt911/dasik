"""dracut reads EVERY /etc/dracut.conf.d/*.conf, so every one of them is an
input to the image — not just the one dasik writes.

The backend already counts dasik.conf, and plymouthd.conf when a splash is
declared, precisely because "the config that changed is not the config we
wrote" makes the plan silent while the image keeps the old content. A drop-in
somebody adds through `etc_tree` is the same shape of input and was not counted:
`apply` wrote the file, `plan` then reported converged, and the image kept
whatever it had until an unrelated kernel upgrade happened to run dracut.

That is the dangerous half — you cannot tell "applied" from "ignored" — and it
bites hardest with a file that decides whether the machine can boot at all (a
storage driver, or the keyboard that types the LUKS passphrase).
"""
import os

from dasik.lib.actions.initramfs.dracut import DracutBackend
from dasik.lib.target.target import Target


def _target(tmp_path):
    (tmp_path / "etc/dracut.conf.d").mkdir(parents=True)
    (tmp_path / "usr/lib/modules/6.1.0").mkdir(parents=True)
    (tmp_path / "usr/lib/modules/6.1.0/pkgbase").write_text("linux\n")
    (tmp_path / "boot").mkdir()
    (tmp_path / "boot/initramfs-linux.img").write_text("image")
    return Target(root=str(tmp_path))


def _backend_with_conf(tmp_path):
    """A converged target: dasik.conf written, image built after it."""
    target = _target(tmp_path)
    backend = DracutBackend({}, target)
    conf = tmp_path / "etc/dracut.conf.d/dasik.conf"
    conf.write_text(backend.desired_value())
    os.utime(conf, (1, 1))
    os.utime(tmp_path / "etc/dracut.conf.d", (1, 1))
    os.utime(tmp_path / "boot/initramfs-linux.img", (2, 2))
    return backend, conf


def test_a_foreign_conf_newer_than_the_image_forces_a_rebuild(tmp_path):
    """The reported case: an omit_drivers drop-in dropped by `etc_tree` after
    the image was built. dasik.conf is untouched, so the scalar compare says
    converged — and the initramfs still carries the modules the file exists to
    keep out."""
    backend, _ = _backend_with_conf(tmp_path)
    foreign = tmp_path / "etc/dracut.conf.d/10-no-nvidia-early-kms.conf"
    foreign.write_text('omit_drivers+=" nvidia "\n')
    os.utime(foreign, (3, 3))                    # newer than the image
    os.utime(tmp_path / "etc/dracut.conf.d", (1, 1))   # isolate the file's own mtime

    assert backend.actual_value() is None


def test_an_image_newer_than_every_conf_is_converged(tmp_path):
    """The other direction: once dracut has run, nothing is planned."""
    backend, _ = _backend_with_conf(tmp_path)
    foreign = tmp_path / "etc/dracut.conf.d/10-no-nvidia-early-kms.conf"
    foreign.write_text('omit_drivers+=" nvidia "\n')
    os.utime(foreign, (2, 2))
    os.utime(tmp_path / "etc/dracut.conf.d", (2, 2))
    os.utime(tmp_path / "boot/initramfs-linux.img", (4, 4))

    assert backend.actual_value() == backend.desired_value()


def test_removing_a_foreign_conf_forces_a_rebuild(tmp_path):
    """Deletion cannot be seen by looking at the files that are left — they are
    all older than the image. The DIRECTORY's mtime is what changes, so it is an
    input too. Without this, dropping a block from the config takes the file
    away and leaves the image still built from it."""
    backend, _ = _backend_with_conf(tmp_path)
    foreign = tmp_path / "etc/dracut.conf.d/10-no-nvidia-early-kms.conf"
    foreign.write_text('omit_drivers+=" nvidia "\n')
    os.utime(foreign, (2, 2))
    os.utime(tmp_path / "boot/initramfs-linux.img", (3, 3))
    os.utime(tmp_path / "etc/dracut.conf.d", (3, 3))
    assert backend.actual_value() is not None     # converged before the removal

    foreign.unlink()
    os.utime(tmp_path / "etc/dracut.conf.d", (4, 4))   # deletion bumps the dir

    assert backend.actual_value() is None


def test_a_non_conf_file_is_not_an_input(tmp_path):
    """dracut only reads *.conf. A .pacnew or an editor backup left in that
    directory must not force a rebuild on every plan."""
    backend, _ = _backend_with_conf(tmp_path)
    noise = tmp_path / "etc/dracut.conf.d/dasik.conf.pacnew"
    noise.write_text("# not read by dracut\n")
    os.utime(noise, (9, 9))
    os.utime(tmp_path / "etc/dracut.conf.d", (1, 1))

    assert backend.actual_value() == backend.desired_value()

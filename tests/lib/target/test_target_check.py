"""`plan`/`apply` on an already-installed host must not die on `arch-chroot`.

`plan`/`apply` default to `--target /mnt`, so every command is wrapped in
`arch-chroot`. On a running system that binary usually is not installed (it
ships in `arch-install-scripts`), and the read-only `plan` used to abort with a
bare "Binary not found: arch-chroot" — no hint that `--target /` is what day-2
management wants.
"""
from dasik.lib.target.target import Target
from dasik.lib.target.target_check import check_target


def test_running_host_never_needs_arch_chroot(monkeypatch):
    monkeypatch.setattr("dasik.lib.target.target_check.which", lambda _: None)
    assert check_target(Target(root="/")) is None


def test_chroot_target_with_the_binary_present_is_fine(monkeypatch):
    monkeypatch.setattr("dasik.lib.target.target_check.which",
                        lambda _: "/usr/bin/arch-chroot")
    assert check_target(Target(root="/mnt")) is None


def test_missing_binary_names_the_package_and_the_day2_flag(monkeypatch):
    monkeypatch.setattr("dasik.lib.target.target_check.which", lambda _: None)
    message = check_target(Target(root="/mnt"))
    assert message is not None
    assert "arch-install-scripts" in message
    assert "--target /" in message


def test_message_reports_an_unmounted_target(tmp_path, monkeypatch):
    monkeypatch.setattr("dasik.lib.target.target_check.which", lambda _: None)
    message = check_target(Target(root=str(tmp_path)))
    assert "nothing is mounted" in message


def test_message_omits_the_mount_hint_when_the_target_is_populated(tmp_path,
                                                                   monkeypatch):
    monkeypatch.setattr("dasik.lib.target.target_check.which", lambda _: None)
    (tmp_path / "etc").mkdir()
    message = check_target(Target(root=str(tmp_path)))
    assert "nothing is mounted" not in message

from unittest.mock import mock_open, patch

import pytest

from dasik.lib.actions.base_install_action import BaseInstallAction
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Op


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


def _marker(tmp_path):
    d = tmp_path / "usr" / "bin"
    d.mkdir(parents=True, exist_ok=True)
    (d / "pacman").write_text("")


# --- microcode detection (unchanged behavior) ----------------------------- #

def test_base_packages_without_microcode():
    a = BaseInstallAction({"enable_microcode": False})
    assert a.packages == ["base", "linux", "linux-firmware", "mkinitcpio"]


# --- the initramfs generator is pacstrapped, not left to the default ------- #
#
# `base` depends on the virtual `initramfs`, which THREE packages provide;
# pacstrap picks the first (mkinitcpio) unless told otherwise. On 2026-08-08
# that installed mkinitcpio into a dracut system: its pacman hook ran inside
# pacstrap — where /mnt/etc/pacman.d/hooks (the dasik neutralizers) is not read
# — and failed mid-transaction ("errors were encountered during the build").
# Naming the declared generator removes both the interactive provider prompt and
# the second generator.

def test_pacstrap_installs_the_declared_dracut_generator():
    a = BaseInstallAction({"enable_microcode": False, "initramfs": "dracut"})
    assert "dracut" in a.packages
    assert "mkinitcpio" not in a.packages


def test_pacstrap_defaults_to_mkinitcpio_when_unspecified():
    a = BaseInstallAction({"enable_microcode": False})
    assert "mkinitcpio" in a.packages


def test_generator_comes_before_the_microcode_package():
    with patch("builtins.open", mock_open(read_data="vendor_id : AuthenticAMD")):
        a = BaseInstallAction({"enable_microcode": True, "initramfs": "dracut"})
    assert a.packages == ["base", "linux", "linux-firmware", "dracut", "amd-ucode"]


def test_adds_amd_ucode_on_amd():
    with patch("builtins.open", mock_open(read_data="vendor_id : AuthenticAMD")):
        a = BaseInstallAction({"enable_microcode": True})
    assert "amd-ucode" in a.packages


def test_adds_intel_ucode_on_intel():
    with patch("builtins.open", mock_open(read_data="vendor_id : GenuineIntel")):
        a = BaseInstallAction({"enable_microcode": True})
    assert "intel-ucode" in a.packages


def test_unknown_vendor_exits():
    with patch("builtins.open", mock_open(read_data="vendor_id : Cyrix")):
        with pytest.raises(SystemExit):
            BaseInstallAction({"enable_microcode": True})


# --- v3 contract ---------------------------------------------------------- #

def test_is_v3_true():
    assert BaseInstallAction.is_v3() is True


def test_actual_empty_when_not_installed(tmp_path):
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    assert a.actual() == set()


def test_actual_present_when_marker(tmp_path):
    _marker(tmp_path)
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    assert a.actual() == {"base"}


def test_plan_install_when_absent(tmp_path):
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    changes = a.plan(managed=[])
    assert changes and changes[0].op is Op.INSTALL and changes[0].item == "base"


def test_plan_empty_when_present(tmp_path):
    _marker(tmp_path)
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    assert a.plan(managed=[]) == []


def test_apply_runs_install_when_changes(tmp_path):
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    with patch.object(BaseInstallAction, "_install") as inst:
        a.apply(a.plan(managed=[]))
        inst.assert_called_once()


def test_apply_noop_when_no_changes(tmp_path):
    _marker(tmp_path)
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    with patch.object(BaseInstallAction, "_install") as inst:
        a.apply(a.plan(managed=[]))
        inst.assert_not_called()


def test_is_needed_and_verify_track_marker(tmp_path):
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    assert a.is_needed() is True and a.verify() is False
    _marker(tmp_path)
    assert a.is_needed() is False and a.verify() is True


def test_managed_keys(tmp_path):
    _marker(tmp_path)
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    assert a.managed_keys() == {"base": ["base"]}


def test_import_state_empty(tmp_path):
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    assert a.import_state(managed=[]) == {}


def test_name_and_optional():
    a = BaseInstallAction({"enable_microcode": False})
    assert a.name == "Base Installation"
    assert a.is_optional is False


# --- _install fails loud instead of producing a broken system ------------- #
from types import SimpleNamespace
from dasik.lib.exceptions.exceptions import CommandExecutionError


def _res(returncode=0, stdout=b""):
    return SimpleNamespace(returncode=returncode, stdout=stdout)


def test_install_aborts_when_pacstrap_fails(tmp_path):
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))

    def fake(cmd, args, **kw):
        return _res(returncode=1) if cmd == "pacstrap" else _res(0, b"x / ext4 0 1\n")

    with patch("dasik.lib.actions.base_install_action.Command.execute", side_effect=fake):
        with pytest.raises(CommandExecutionError):
            a._install()
    # no partial fstab written
    assert not (tmp_path / "etc" / "fstab").exists()


@pytest.mark.parametrize("gen", [_res(returncode=1, stdout=b""), _res(0, b""), _res(0, b"   \n")])
def test_install_aborts_when_genfstab_empty_or_failed(tmp_path, gen):
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))

    def fake(cmd, args, **kw):
        return gen if cmd == "genfstab" else _res(0)

    with patch("dasik.lib.actions.base_install_action.Command.execute", side_effect=fake):
        with pytest.raises(CommandExecutionError):
            a._install()
    assert not (tmp_path / "etc" / "fstab").exists()


def test_install_writes_fstab_on_success(tmp_path):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    fstab_line = b"UUID=abc / ext4 rw 0 1\n"

    def fake(cmd, args, **kw):
        return _res(0, fstab_line) if cmd == "genfstab" else _res(0)

    with patch("dasik.lib.actions.base_install_action.Command.execute", side_effect=fake):
        a._install()
    assert (tmp_path / "etc" / "fstab").read_text() == fstab_line.decode()


# --- import_state: capture enable_microcode ------------------------------- #

def _pacman_has(installed):
    def fake(cmd, args=None, *rest, **kw):
        from unittest.mock import MagicMock
        if cmd == "pacman" and args and args[0] == "-Qq":
            return MagicMock(returncode=0 if args[1] in installed else 1)
        return MagicMock(returncode=1)
    return fake


def test_import_state_captures_microcode_when_amd_installed():
    a = BaseInstallAction({}, _ctx("/"))
    with patch("dasik.lib.actions.base_install_action.Command.execute",
               side_effect=_pacman_has({"amd-ucode"})):
        assert a.import_state(managed=[]) == {"enable_microcode": True}


def test_import_state_captures_microcode_when_intel_installed():
    a = BaseInstallAction({}, _ctx("/"))
    with patch("dasik.lib.actions.base_install_action.Command.execute",
               side_effect=_pacman_has({"intel-ucode"})):
        assert a.import_state(managed=[]) == {"enable_microcode": True}


def test_import_state_empty_when_no_microcode():
    a = BaseInstallAction({}, _ctx("/"))
    with patch("dasik.lib.actions.base_install_action.Command.execute",
               side_effect=_pacman_has(set())):
        assert a.import_state(managed=[]) == {}


# --- T2: long installers stream live output ------------------------------- #

def test_install_streams_keyring_and_pacstrap(tmp_path):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    seen = {}

    def fake(cmd, args, **kw):
        seen[cmd] = kw
        return _res(0, b"UUID=abc / ext4 rw 0 1\n") if cmd == "genfstab" else _res(0)

    with patch("dasik.lib.actions.base_install_action.Command.execute", side_effect=fake):
        a._install()

    # pacman -Sy archlinux-keyring and pacstrap both stream (long output)
    assert seen["pacman"].get("stream") is True
    assert seen["pacstrap"].get("stream") is True


# --- hook failures inside pacstrap (F-19) ---------------------------------- #
#
# pacstrap exits 0 even when a hook it ran failed: the 2026-07-19 log shows
# mkinitcpio reporting "the image may not be complete" and "command failed to
# execute correctly" inside pacstrap, and BaseInstallAction only saw rc 0.

_PACSTRAP_WITH_FAILED_HOOK = b"""\
:: Running post-transaction hooks...
( 5/10) Updating linux initcpios...
==> Starting build: '6.9.1-arch1-1'
==> WARNING: consolefont: no font found in configuration
==> ERROR: file not found: `/etc/vconsole.conf'
==> WARNING: errors were encountered during the build. The image may not be complete.
error: command failed to execute correctly
( 6/10) Arming ConditionNeedsUpdate...
"""


def test_pacstrap_hook_failure_is_reported(monkeypatch, tmp_path):
    from unittest.mock import MagicMock, patch
    from dasik.lib.actions.base_install_action import BaseInstallAction

    warnings_seen = []

    class _Logger:
        def warning(self, message, detail=""):
            warnings_seen.append((message, detail))

        def error(self, message, detail=""):
            warnings_seen.append((message, detail))

    def fake_exec(cmd, args, **kw):
        if cmd == "genfstab":
            return MagicMock(returncode=0, stdout=b"UUID=x / ext4 defaults 0 1\n")
        if cmd == "pacstrap":
            return MagicMock(returncode=0, stdout=_PACSTRAP_WITH_FAILED_HOOK)
        return MagicMock(returncode=0, stdout=b"")

    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    (tmp_path / "etc").mkdir(exist_ok=True)
    with patch("dasik.lib.actions.base_install_action.Command.execute",
               side_effect=fake_exec), \
         patch("dasik.lib.actions.base_install_action.run_logger.get",
               return_value=_Logger()):
        a._install()

    assert any("hook" in msg.lower() for msg, _ in warnings_seen), warnings_seen
    assert any("command failed to execute correctly" in detail
               for _, detail in warnings_seen)


def test_clean_pacstrap_output_reports_nothing(monkeypatch, tmp_path):
    from unittest.mock import MagicMock, patch
    from dasik.lib.actions.base_install_action import BaseInstallAction

    seen = []

    class _Logger:
        def warning(self, message, detail=""):
            seen.append(message)

        def error(self, message, detail=""):
            seen.append(message)

    def fake_exec(cmd, args, **kw):
        if cmd == "genfstab":
            return MagicMock(returncode=0, stdout=b"UUID=x / ext4 defaults 0 1\n")
        return MagicMock(returncode=0, stdout=b":: Running post-transaction hooks...\n")

    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    (tmp_path / "etc").mkdir(exist_ok=True)
    with patch("dasik.lib.actions.base_install_action.Command.execute",
               side_effect=fake_exec), \
         patch("dasik.lib.actions.base_install_action.run_logger.get",
               return_value=_Logger()):
        a._install()
    assert seen == []

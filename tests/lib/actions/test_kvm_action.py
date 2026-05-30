from unittest.mock import MagicMock, patch

from dasik.lib.actions.kvm_action import KvmAction


def test_disabled_is_never_needed():
    assert KvmAction({"install": False}).is_needed() is False


def test_needed_when_packages_missing():
    a = KvmAction({"install": True})
    fake = MagicMock(return_value=MagicMock(stdout=b"", returncode=1))
    with patch("dasik.lib.actions.kvm_action.subprocess.run", fake):
        assert a.is_needed() is True
        assert a._missing_pkgs()  # non-empty


def test_needed_when_libvirtd_not_enabled():
    a = KvmAction({"install": True})

    def side(cmd, **kw):
        if "pacman" in cmd:
            return MagicMock(stdout=b"", returncode=0)  # all installed
        return MagicMock(stdout=b"disabled\n", returncode=0)

    with patch("dasik.lib.actions.kvm_action.subprocess.run", side):
        assert a.is_needed() is True


def test_not_needed_when_installed_and_service_enabled():
    a = KvmAction({"install": True})

    def side(cmd, **kw):
        if "pacman" in cmd:
            return MagicMock(stdout=b"", returncode=0)
        return MagicMock(stdout=b"enabled\n", returncode=0)

    with patch("dasik.lib.actions.kvm_action.subprocess.run", side):
        assert a.is_needed() is False
        assert a.verify() is True


def test_setup_nested_virt_writes_intel_module_when_absent():
    from unittest.mock import mock_open
    a = KvmAction({"install": True})
    m = mock_open(read_data="vendor_id\t: GenuineIntel\n")
    with patch("os.path.exists", return_value=False), \
         patch("builtins.open", m):
        a._setup_nested_virt()
    written = "".join(c.args[0] for c in m().write.call_args_list)
    assert "kvm_intel" in written
    assert "nested=1" in written


def test_setup_nested_virt_skips_when_already_correct():
    from unittest.mock import mock_open
    a = KvmAction({"install": True})
    # First open() reads cpuinfo (AMD), second open() reads matching conf.
    handles = [
        mock_open(read_data="vendor_id\t: AuthenticAMD\n").return_value,
        mock_open(read_data="options kvm_amd nested=1\n").return_value,
    ]
    m = MagicMock(side_effect=handles)
    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", m):
        a._setup_nested_virt()
    # Already correct → only the two reads happened, no third (write) open.
    assert m.call_count == 2


def test_name_and_optional():
    a = KvmAction({"install": True})
    assert a.name == "KVM Installation"
    assert a.is_optional is True

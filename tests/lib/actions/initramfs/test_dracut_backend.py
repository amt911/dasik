from unittest.mock import mock_open, patch

from dasik.lib.actions.initramfs.dracut import DracutBackend
from dasik.lib.target.target import Target


def _cfg(encrypt=False, fs="ext4"):
    part = {"mountpoint": "/", "filesystem": fs}
    if encrypt:
        part["encrypt"] = True
    return {"disks": {"disks": [{"partitions": [part]}]}}


def _b(cfg, root="/"):
    return DracutBackend(cfg, Target(root=root))


def test_desired_includes_crypt_when_encrypted():
    assert "crypt" in _b(_cfg(encrypt=True)).desired_value()


def test_desired_includes_btrfs_when_btrfs_root():
    assert "btrfs" in _b(_cfg(fs="btrfs")).desired_value()


def test_desired_empty_when_nothing_to_add():
    assert _b(_cfg()).desired_value() == ""


def test_desired_is_deterministic():
    b = _b(_cfg(encrypt=True, fs="btrfs"))
    assert b.desired_value() == b.desired_value()
    assert "crypt" in b.desired_value() and "btrfs" in b.desired_value()


def test_actual_value_reads_conf():
    with patch("builtins.open", mock_open(read_data="add_dracutmodules+=\" crypt \"\n")):
        assert _b(_cfg(encrypt=True)).actual_value() == "add_dracutmodules+=\" crypt \"\n"


def test_actual_value_none_when_absent():
    with patch("builtins.open", side_effect=FileNotFoundError):
        assert _b(_cfg(encrypt=True)).actual_value() is None


def test_apply_writes_conf_and_regenerates():
    a = _b(_cfg(encrypt=True), root="/")
    m = mock_open()
    with patch("builtins.open", m), \
         patch("dasik.lib.actions.initramfs.dracut.os.makedirs"), \
         patch("dasik.lib.actions.initramfs.dracut.Command.execute") as run:
        a.apply()
    assert m.call_args_list[0].args[0] == "/etc/dracut.conf.d/dasik.conf"
    body = "".join(c.args[0] for c in m().write.call_args_list)
    assert "crypt" in body
    assert (run.call_args.args[0], run.call_args.args[1]) == (
        "dracut", ["--regenerate-all", "--force"])
    assert run.call_args.kwargs["target"].root == "/"

from unittest.mock import patch

from dasik.lib.actions.initramfs_action import InitramfsAction
from dasik.lib.actions.initramfs.mkinitcpio import MkinitcpioBackend
from dasik.lib.actions.initramfs.dracut import DracutBackend
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Op


def _ctx(root="/"):
    return ActionContext(target=Target(root=root))


def test_default_backend_is_mkinitcpio():
    a = InitramfsAction({}, _ctx("/"))
    assert isinstance(a._backend, MkinitcpioBackend)


def test_selects_dracut_backend():
    a = InitramfsAction({"initramfs": "dracut"}, _ctx("/"))
    assert isinstance(a._backend, DracutBackend)


def test_is_v3_true():
    assert InitramfsAction.is_v3() is True


def test_delegates_hooks_to_backend():
    a = InitramfsAction({}, _ctx("/"))
    with patch.object(a._backend, "desired_value", return_value="X"), \
         patch.object(a._backend, "actual_value", return_value="Y"):
        changes = a.plan(managed=[])
    assert [(c.op, c.item) for c in changes] == [(Op.MODIFY, "X")]


def test_plan_empty_when_converged():
    a = InitramfsAction({}, _ctx("/"))
    with patch.object(a._backend, "desired_value", return_value="SAME"), \
         patch.object(a._backend, "actual_value", return_value="SAME"):
        assert a.plan(managed=[]) == []


def test_set_value_calls_backend_apply():
    a = InitramfsAction({}, _ctx("/"))
    with patch.object(a._backend, "apply") as ap:
        a._set_value()
    ap.assert_called_once()


def test_import_fragment_is_empty():
    a = InitramfsAction({}, _ctx("/"))
    assert a._import_fragment("anything") == {}


def test_managed_keys_domain_is_initramfs():
    a = InitramfsAction({}, _ctx("/"))
    with patch.object(a._backend, "desired_value", return_value="X"):
        assert a.managed_keys() == {"initramfs": ["X"]}


from unittest.mock import MagicMock


def _pkg_fake(installed):
    def run(cmd, args=None, *a, **k):
        pkg = args[1] if args and len(args) > 1 else ""
        return MagicMock(returncode=0 if pkg in installed else 1, stdout=b"")
    return run


def test_import_state_detects_dracut():
    with patch("dasik.lib.actions.initramfs_action.Command.execute",
               side_effect=_pkg_fake({"dracut"})):
        a = InitramfsAction({"initramfs": "dracut"}, _ctx("/"))
        assert a.import_state() == {"initramfs": "dracut"}


def test_import_state_detects_mkinitcpio_when_present():
    with patch("dasik.lib.actions.initramfs_action.Command.execute",
               side_effect=_pkg_fake({"dracut", "mkinitcpio"})):
        a = InitramfsAction({}, _ctx("/"))
        assert a.import_state() == {"initramfs": "mkinitcpio"}


def test_import_state_empty_without_target():
    a = InitramfsAction({}, context=None)
    assert a.import_state() == {}

from pathlib import PosixPath
from unittest.mock import MagicMock, patch

from dasik.lib.actions.timezone_action import TimezoneAction


def _link(exists=True, is_symlink=True, target="/usr/share/zoneinfo/Europe/Madrid"):
    m = MagicMock()
    m.exists.return_value = exists
    m.is_symlink.return_value = is_symlink
    m.readlink.return_value = PosixPath(target)
    return m


def _cfg():
    return {"region": "Europe", "city": "Madrid"}


def test_needed_when_link_absent():
    a = TimezoneAction(_cfg())
    with patch("dasik.lib.actions.timezone_action.Path", return_value=_link(exists=False)):
        assert a.is_needed() is True


def test_needed_when_not_a_symlink():
    a = TimezoneAction(_cfg())
    with patch("dasik.lib.actions.timezone_action.Path",
               return_value=_link(is_symlink=False)):
        assert a.is_needed() is True


def test_not_needed_when_link_matches():
    a = TimezoneAction(_cfg())
    with patch("dasik.lib.actions.timezone_action.Path", return_value=_link()):
        assert a.is_needed() is False


def test_needed_when_link_points_elsewhere():
    a = TimezoneAction(_cfg())
    with patch("dasik.lib.actions.timezone_action.Path",
               return_value=_link(target="/usr/share/zoneinfo/America/New_York")):
        assert a.is_needed() is True


def test_verify_true_only_on_exact_match():
    a = TimezoneAction(_cfg())
    with patch("dasik.lib.actions.timezone_action.Path", return_value=_link()):
        assert a.verify() is True
    with patch("dasik.lib.actions.timezone_action.Path",
               return_value=_link(target="/usr/share/zoneinfo/Asia/Tokyo")):
        assert a.verify() is False


def test_needed_when_link_target_too_short():
    a = TimezoneAction(_cfg())
    with patch("dasik.lib.actions.timezone_action.Path",
               return_value=_link(target="/usr/share/zoneinfo")):
        assert a.is_needed() is True


def test_needed_when_readlink_raises():
    a = TimezoneAction(_cfg())
    link = _link()
    link.readlink.side_effect = OSError("nope")
    with patch("dasik.lib.actions.timezone_action.Path", return_value=link):
        assert a.is_needed() is True


def test_verify_false_when_not_symlink():
    a = TimezoneAction(_cfg())
    with patch("dasik.lib.actions.timezone_action.Path",
               return_value=_link(is_symlink=False)):
        assert a.verify() is False


def test_verify_false_when_readlink_raises():
    a = TimezoneAction(_cfg())
    link = _link()
    link.readlink.side_effect = OSError("nope")
    with patch("dasik.lib.actions.timezone_action.Path", return_value=link):
        assert a.verify() is False


def test_name():
    assert TimezoneAction(_cfg()).name == "Timezone Configuration"


# ---------------------------------------------------------------------- #
#  v3 contract (ScalarV3Action) — Plan 9                                  #
# ---------------------------------------------------------------------- #
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Op


def _ctx(root="/"):
    return ActionContext(target=Target(root=root))


def test_is_v3_true():
    assert TimezoneAction(_cfg()).is_v3() is True


def test_desired_value_joins_region_city():
    assert TimezoneAction(_cfg())._desired_value() == "Europe/Madrid"


def test_actual_value_parses_symlink():
    a = TimezoneAction(_cfg(), _ctx("/"))
    with patch("dasik.lib.actions.timezone_action.Path",
               return_value=_link(target="/usr/share/zoneinfo/Asia/Tokyo")):
        assert a._actual_value() == "Asia/Tokyo"


def test_actual_value_none_when_not_symlink():
    a = TimezoneAction(_cfg(), _ctx("/"))
    with patch("dasik.lib.actions.timezone_action.Path",
               return_value=_link(is_symlink=False)):
        assert a._actual_value() is None


def test_plan_modify_when_zone_differs():
    a = TimezoneAction(_cfg(), _ctx("/"))
    with patch("dasik.lib.actions.timezone_action.Path",
               return_value=_link(target="/usr/share/zoneinfo/America/New_York")):
        changes = a.plan(managed=[])
    assert [(c.op, c.item) for c in changes] == [(Op.MODIFY, "Europe/Madrid")]


def test_plan_empty_when_zone_matches():
    a = TimezoneAction(_cfg(), _ctx("/"))
    with patch("dasik.lib.actions.timezone_action.Path", return_value=_link()):
        assert a.plan(managed=[]) == []


def test_set_value_issues_ln_and_hwclock_with_target():
    a = TimezoneAction(_cfg(), _ctx("/"))
    with patch("dasik.lib.actions.timezone_action.Command.execute") as run:
        a._set_value()
    cmds = [(c.args[0], c.args[1]) for c in run.call_args_list]
    assert ("ln", ["-sf", "/usr/share/zoneinfo/Europe/Madrid", "/etc/localtime"]) in cmds
    assert ("hwclock", ["--systohc"]) in cmds
    assert run.call_args_list[0].kwargs["target"].root == "/"


def test_import_fragment_splits_region_city():
    a = TimezoneAction(_cfg())
    assert a._import_fragment("Asia/Tokyo") == {
        "timezone": {"region": "Asia", "city": "Tokyo"}}

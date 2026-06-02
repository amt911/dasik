from dasik.lib.actions.pacman_action import PacmanAction
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Op

_COMMENTED = """\
#ParallelDownloads = 5
#Color
#VerbosePkgLists
#[multilib]
#Include = /etc/pacman.d/mirrorlist
"""

_ACTIVE = """\
ParallelDownloads = 5
Color
VerbosePkgLists
[multilib]
Include = /etc/pacman.d/mirrorlist
"""


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


def _write_conf(tmp_path, text):
    etc = tmp_path / "etc"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "pacman.conf").write_text(text)


def _cfg(parallel=True, color=True, verbose=False, multilib=False):
    return {
        "options": {"Parallel": parallel, "Color": color, "VerbosePkgLists": verbose},
        "multilib": multilib,
    }


def test_is_v3_true():
    assert PacmanAction.is_v3() is True


def test_desired_state():
    a = PacmanAction(_cfg(parallel=True, color=False, verbose=True, multilib=True))
    assert a._desired_state() == {
        "Parallel": True, "Color": False, "VerbosePkgLists": True, "multilib": True,
    }


def test_actual_state_active(tmp_path):
    _write_conf(tmp_path, _ACTIVE)
    a = PacmanAction(_cfg(), _ctx(tmp_path))
    assert a._actual_state() == {
        "Parallel": True, "Color": True, "VerbosePkgLists": True, "multilib": True,
    }


def test_actual_state_commented(tmp_path):
    _write_conf(tmp_path, _COMMENTED)
    a = PacmanAction(_cfg(), _ctx(tmp_path))
    assert a._actual_state() == {
        "Parallel": False, "Color": False, "VerbosePkgLists": False, "multilib": False,
    }


def test_actual_state_none_when_missing(tmp_path):
    a = PacmanAction(_cfg(), _ctx(tmp_path))  # no pacman.conf written
    assert a._actual_state() is None


def test_plan_empty_when_converged(tmp_path):
    _write_conf(tmp_path, _ACTIVE)
    a = PacmanAction(_cfg(parallel=True, color=True, verbose=True, multilib=True), _ctx(tmp_path))
    assert a.plan(managed=[]) == []


def test_plan_modify_when_flag_on_but_commented(tmp_path):
    _write_conf(tmp_path, _COMMENTED)
    a = PacmanAction(_cfg(color=True), _ctx(tmp_path))
    changes = a.plan(managed=[])
    assert changes and changes[0].op is Op.MODIFY and "Color" in changes[0].item


def test_plan_modify_when_flag_off_but_active(tmp_path):
    # bidirectional: Color declared False but active in conf -> MODIFY
    _write_conf(tmp_path, _ACTIVE)
    a = PacmanAction(_cfg(parallel=True, color=False, verbose=True, multilib=True), _ctx(tmp_path))
    changes = a.plan(managed=[])
    assert changes and changes[0].op is Op.MODIFY and "Color" in changes[0].item


def test_set_value_enables_all(tmp_path):
    _write_conf(tmp_path, _COMMENTED)
    a = PacmanAction(_cfg(parallel=True, color=True, verbose=True, multilib=True), _ctx(tmp_path))
    a._set_value()
    assert a._actual_state() == {
        "Parallel": True, "Color": True, "VerbosePkgLists": True, "multilib": True,
    }


def test_set_value_disables_all(tmp_path):
    # bidirectional down: active conf, all flags False -> commented back out
    _write_conf(tmp_path, _ACTIVE)
    a = PacmanAction(_cfg(parallel=False, color=False, verbose=False, multilib=False), _ctx(tmp_path))
    a._set_value()
    assert a._actual_state() == {
        "Parallel": False, "Color": False, "VerbosePkgLists": False, "multilib": False,
    }


def test_set_value_idempotent(tmp_path):
    _write_conf(tmp_path, _COMMENTED)
    cfg = _cfg(parallel=True, color=True, verbose=True, multilib=True)
    a = PacmanAction(cfg, _ctx(tmp_path))
    a._set_value()
    a._set_value()  # second run is a no-op
    assert a.plan(managed=[]) == []


def test_import_fragment_shape(tmp_path):
    _write_conf(tmp_path, _ACTIVE)
    a = PacmanAction(_cfg(), _ctx(tmp_path))
    frag = a.import_state(managed=[])
    assert frag == {"pacman": {
        "options": {"Parallel": True, "Color": True, "VerbosePkgLists": True},
        "multilib": True,
    }}


def test_name_and_optional():
    a = PacmanAction(_cfg())
    assert a.name == "Pacman Configuration"
    assert a.is_optional is True

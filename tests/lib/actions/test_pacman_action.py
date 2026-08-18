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


def _write_db(tmp_path):
    sync = tmp_path / "var" / "lib" / "pacman" / "sync"
    sync.mkdir(parents=True, exist_ok=True)
    (sync / "multilib.db").write_bytes(b"")


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
    # chroot target, conf active, sync DB absent: enabled-but-unsynced.
    assert a._actual_state() == {
        "Parallel": True, "Color": True, "VerbosePkgLists": True, "multilib": True,
        "multilib_synced": False,
    }


def test_actual_state_commented(tmp_path):
    _write_conf(tmp_path, _COMMENTED)
    a = PacmanAction(_cfg(), _ctx(tmp_path))
    assert a._actual_state() == {
        "Parallel": False, "Color": False, "VerbosePkgLists": False, "multilib": False,
        "multilib_synced": False,
    }


def test_actual_state_none_when_missing(tmp_path):
    a = PacmanAction(_cfg(), _ctx(tmp_path))  # no pacman.conf written
    assert a._actual_state() is None


def test_plan_empty_when_converged(tmp_path):
    _write_conf(tmp_path, _ACTIVE)
    _write_db(tmp_path)          # converged now includes a synced multilib DB
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


# --- multilib_synced: enabled-but-unsynced DB (2026-08-18 latent edge) --- #

def test_enabled_multilib_with_missing_db_plans_a_modify(tmp_path):
    """An apply that died between writing pacman.conf and its -Sy leaves
    [multilib] enabled with no sync DB; every later plan said converged while
    the resolver misclassified every multilib package as AUR."""
    _write_conf(tmp_path, _ACTIVE)               # conf enabled, no DB written
    a = PacmanAction(_cfg(parallel=True, color=True, verbose=True, multilib=True),
                     _ctx(tmp_path))
    changes = a.plan(managed=[])
    assert changes and changes[0].op is Op.MODIFY
    assert "multilib_synced" in changes[0].item


def test_apply_after_the_missing_db_plan_converges(tmp_path):
    """plan → apply → plan must end silent: apply's existing -Sy (mocked here
    as pacman writing the DB) is exactly what converges the new key."""
    from unittest.mock import patch

    _write_conf(tmp_path, _ACTIVE)
    a = PacmanAction(_cfg(parallel=True, color=True, verbose=True, multilib=True),
                     _ctx(tmp_path))
    changes = a.plan(managed=[])
    assert changes

    def fake_pacman(cmd, args, **kw):
        assert (cmd, args) == ("pacman", ["-Sy"])
        _write_db(tmp_path)

    with patch("dasik.lib.actions.pacman_action.Command.execute",
               side_effect=fake_pacman) as run:
        a.apply(changes)
    assert run.called
    assert a.plan(managed=[]) == []


def test_live_target_carries_no_synced_key(monkeypatch):
    """--target / (day-2 host): apply's -Sy is chroot-gated, so including the
    key would plan a change apply can never fix — the never-converging plan."""
    from dasik.lib.actions.action_context import ActionContext as Ctx
    a = PacmanAction(_cfg(multilib=True), Ctx(target=Target(root="/")))
    monkeypatch.setattr(a, "_read", lambda: _ACTIVE)
    assert "multilib_synced" not in a._desired_state()
    assert "multilib_synced" not in a._actual_state()


def test_no_context_carries_no_synced_key():
    a = PacmanAction(_cfg(multilib=True))
    assert "multilib_synced" not in a._desired_state()


def test_multilib_off_with_a_leftover_db_is_converged(tmp_path):
    _write_conf(tmp_path, _COMMENTED)
    _write_db(tmp_path)
    a = PacmanAction(_cfg(parallel=False, color=False, verbose=False,
                          multilib=False), _ctx(tmp_path))
    assert a.plan(managed=[]) == []


def test_undeclared_block_still_plans_nothing_despite_a_missing_db(tmp_path):
    """The empty-config trap: a dropped block must not start planning syncs."""
    _write_conf(tmp_path, _ACTIVE)               # enabled, no DB
    a = PacmanAction({}, _ctx(tmp_path))
    assert a.plan(managed=[]) == []


def test_import_fragment_never_captures_the_synced_key(tmp_path):
    """sync capture unchanged: multilib_synced is plan/apply state, not config."""
    _write_conf(tmp_path, _ACTIVE)
    a = PacmanAction(_cfg(), _ctx(tmp_path))
    fragment = a._import_fragment(None)["pacman"]
    assert "multilib_synced" not in fragment
    assert fragment["multilib"] is True


def test_set_value_enables_all(tmp_path):
    _write_conf(tmp_path, _COMMENTED)
    a = PacmanAction(_cfg(parallel=True, color=True, verbose=True, multilib=True), _ctx(tmp_path))
    a._set_value()
    assert a._actual_state() == {
        "Parallel": True, "Color": True, "VerbosePkgLists": True, "multilib": True,
        "multilib_synced": False,     # conf enabled; the -Sy in apply() syncs it
    }


def test_set_value_disables_all(tmp_path):
    # bidirectional down: active conf, all flags False -> commented back out
    _write_conf(tmp_path, _ACTIVE)
    a = PacmanAction(_cfg(parallel=False, color=False, verbose=False, multilib=False), _ctx(tmp_path))
    a._set_value()
    assert a._actual_state() == {
        "Parallel": False, "Color": False, "VerbosePkgLists": False, "multilib": False,
        "multilib_synced": False,
    }


def test_set_value_idempotent(tmp_path):
    _write_conf(tmp_path, _COMMENTED)
    _write_db(tmp_path)      # plan-silence also needs the synced multilib DB
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


# --- an absent section is not "dasik's defaults" ---------------------------

def _conf(tmp_path, text="[options]\nColor\nParallelDownloads = 5\n"
                        "[multilib]\nInclude = /etc/pacman.d/mirrorlist\n"):
    etc = tmp_path / "etc"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "pacman.conf").write_text(text)
    return tmp_path


def test_an_undeclared_pacman_section_plans_nothing(tmp_path):
    """PacmanModel defaults every field, so an empty dict never comes from a
    user config — only from the reconciler handing the empty config for a domain
    a previous generation owned. Planning it as "the defaults" would re-comment
    [multilib] on a machine that depends on it."""
    action = PacmanAction(PacmanAction.empty_config(),
                          _ctx(str(_conf(tmp_path))))

    assert action.plan(managed=["anything"]) == []


def test_an_undeclared_pacman_section_captures_the_machine(tmp_path):
    action = PacmanAction(PacmanAction.empty_config(), _ctx(str(_conf(tmp_path))))

    assert action.import_state(managed=[]) == {"pacman": {
        "options": {"Parallel": True, "Color": True, "VerbosePkgLists": False},
        "multilib": True}}


def test_sync_invents_no_pacman_config_without_a_pacman_conf(tmp_path):
    """No /etc/pacman.conf is an unbuilt target, not a machine whose options
    happen to be dasik's defaults."""
    action = PacmanAction(PacmanAction.empty_config(), _ctx(str(tmp_path)))

    assert action.import_state(managed=[]) == {}


def test_a_declared_pacman_section_still_plans(tmp_path):
    action = PacmanAction({"options": {"Color": False}, "multilib": False},
                          _ctx(str(_conf(tmp_path))))

    assert [c.op for c in action.plan(managed=[])] == [Op.MODIFY]

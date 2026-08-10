"""setup_actions() must be idempotent across repeated calls.

The default registry is process-global. Before the fix, each setup_actions()
call appended every action again, so a second call in one process double-
registered everything (issue #64).
"""
from dasik.lib.actions.actions_handler_v2 import setup_actions
from dasik.lib.actions.action_registry import get_default_registry
from dasik.lib.actions.packages_action import PackagesAction
from dasik.lib.actions.users_action import UsersAction
from dasik.lib.actions.systemd_action import SystemdAction


def _class_order():
    """Registered action classes in execution order (the executor walks this)."""
    setup_actions()
    return [a["class"] for a in get_default_registry().get_all_actions()]


def test_setup_actions_registers_actions():
    setup_actions()
    assert len(get_default_registry().get_all_actions()) > 0


def test_setup_actions_is_idempotent():
    setup_actions()
    first = len(get_default_registry().get_all_actions())

    setup_actions()
    second = len(get_default_registry().get_all_actions())

    assert second == first


def test_users_registered_after_packages():
    # useradd -G docker,libvirt / -s /bin/zsh references groups and shells that
    # the packages create when installed → Users MUST run after Packages.
    order = _class_order()
    assert order.index(PackagesAction) < order.index(UsersAction)


def test_users_registered_before_systemd():
    order = _class_order()
    assert order.index(UsersAction) < order.index(SystemdAction)


def test_capture_only_actions_are_registered():
    """CpuAction/ReflectorAction do nothing on apply, but `sync` only visits
    REGISTERED v3 actions — unregistered, they capture nothing at all."""
    from dasik.lib.actions.cpu_action import CpuAction
    from dasik.lib.actions.reflector_action import ReflectorAction

    order = _class_order()

    assert CpuAction in order
    assert ReflectorAction in order


def test_capture_only_actions_read_the_root_config():
    """Both read root-level keys (`cpu`, `bootloader`, `reflector`), so they
    must be registered against __root__, not a section."""
    setup_actions()
    from dasik.lib.actions.cpu_action import CpuAction
    from dasik.lib.actions.reflector_action import ReflectorAction

    keys = {a["class"]: a["config_key"] for a in get_default_registry().get_all_actions()}

    assert keys[CpuAction] == "__root__"
    assert keys[ReflectorAction] == "__root__"

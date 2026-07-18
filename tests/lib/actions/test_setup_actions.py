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

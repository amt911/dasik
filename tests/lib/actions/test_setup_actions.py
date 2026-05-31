"""setup_actions() must be idempotent across repeated calls.

The default registry is process-global. Before the fix, each setup_actions()
call appended every action again, so a second call in one process double-
registered everything (issue #64).
"""
from dasik.lib.actions.actions_handler_v2 import setup_actions
from dasik.lib.actions.action_registry import get_default_registry


def test_setup_actions_registers_actions():
    setup_actions()
    assert len(get_default_registry().get_all_actions()) > 0


def test_setup_actions_is_idempotent():
    setup_actions()
    first = len(get_default_registry().get_all_actions())

    setup_actions()
    second = len(get_default_registry().get_all_actions())

    assert second == first

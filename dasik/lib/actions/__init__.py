"""Actions module for system configuration.

The idempotent v3 architecture (action registry + reconciler):

    from dasik.lib.actions import setup_actions, execute_installation
"""

# Registry + installation entry points.
from .actions_handler_v2 import (
    setup_actions,
    execute_installation,
    ActionsHandler as ActionsHandlerV2
)

# Core classes for extending
from .abstract_action import AbstractAction
from .action_context import ActionContext
from .action_registry import ActionRegistry, register_action
from .action_executor import ActionExecutor

__all__ = [
    'setup_actions',
    'execute_installation',
    'ActionsHandlerV2',

    # Extension API
    'AbstractAction',
    'ActionContext',
    'ActionRegistry',
    'register_action',
    'ActionExecutor',
]

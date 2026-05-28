from abc import ABC, abstractmethod
from typing import Any, Dict, List, TYPE_CHECKING

if TYPE_CHECKING:
    from .action_context import ActionContext
    from ..state.change import Change

class AbstractAction(ABC):
    """Base class for all system configuration actions.
    
    This class provides the framework for idempotent operations:
    - is_needed(): Check if action needs to run (idempotency check)
    - execute(): Perform the actual configuration changes
    - verify(): Verify the changes were applied correctly
    """
    
    def __init__(self, config: Dict[str, Any], context: 'ActionContext | None' = None):
        """Initialize action with configuration and shared context.
        
        Args:
            config: Configuration dictionary for this action
            context: Shared context between actions (optional)
        """
        self.config = config
        self.context = context
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name for this action."""
        ...
    
    @property
    def is_optional(self) -> bool:
        """Whether this action can be skipped if config is missing.
        
        Override this to return True for optional actions.
        """
        return False
    
    @abstractmethod
    def is_needed(self) -> bool:
        """Check if this action needs to be executed.
        
        This is the idempotency check - return True if the system
        state differs from desired configuration.
        
        Returns:
            True if action needs to run, False if already configured
        """
        ...
    
    @abstractmethod
    def execute(self) -> None:
        """Execute the configuration changes.
        
        This should only be called if is_needed() returns True.
        Raises exception on failure.
        """
        ...
    
    def verify(self) -> bool:
        """Verify the configuration was applied correctly.
        
        Override this to add verification logic after execution.
        
        Returns:
            True if verification passed, False otherwise
        """
        return True
    
    def do_action(self) -> None:
        """Legacy method for backward compatibility.
        
        This method calls execute() directly without idempotency checks.
        New code should use is_needed() + execute() instead.
        """
        self.execute()
    
    # Deprecated methods - kept for backward compatibility
    def _before_check(self) -> bool:
        """Deprecated: Use is_needed() instead."""
        return self.is_needed()
    
    def after_check(self):
        """Deprecated: Use verify() instead."""
        return self.verify()
    
    @property
    def can_incrementally_change(self) -> bool:
        """Deprecated: No longer used in new architecture."""
        return False
    
    @property
    def KEY_NAME(self) -> str:
        """Deprecated: Use name property instead."""
        return self.name

    # ------------------------------------------------------------------
    # v3 interface (spec §3.5) — concrete defaults so legacy actions that
    # only override is_needed/execute keep working unchanged. v3 actions
    # opt in by overriding ``plan``; ``is_v3()`` discriminates the two so
    # the future Reconciler (Plan 3) can pick the right code path.
    # ------------------------------------------------------------------

    def actual(self) -> Any:
        """Read system reality (A) for this action's domain.

        v3 actions override this to query the system (e.g. ``pacman -Qqe``)
        via ``Command.execute(target=self.context.target)``. The default
        returns an empty set so legacy actions can still be introspected
        without error.
        """
        return set()

    def plan(self, managed: Any) -> "List[Change]":
        """Compute the list of Changes needed to converge to the config.

        v3 actions override this with set-math over (D=config, M=managed,
        A=self.actual()) — typically via
        ``dasik.lib.state.set_math.compute_changes``.

        Canonical v3 implementation::

            def plan(self, managed):
                changes, _drift = compute_changes(
                    "packages",
                    desired=self.config.get("packages", []),
                    managed=managed,
                    actual=self.actual(),
                )
                return changes

        The Reconciler (Plan 3) extracts the per-domain managed list from the
        manifest and passes it in: ``ctx.manifest["managed"].get(domain, [])``.

        The default returns an empty list, which makes ``is_v3()`` return False.
        """
        return []

    def apply(self, plan: "List[Change]") -> None:
        """Execute the Changes produced by ``plan``.

        v3 actions override this; the default is a no-op so legacy actions
        keep using their own ``execute()`` path.
        """
        return None

    def import_state(self) -> Dict[str, Any]:
        """Return the config fragment that mirrors A (for ``sync``).

        v3 actions override this to capture drift back into the config
        (e.g. ``{"packages": [...explicitly installed packages...]}``).
        The default returns an empty dict.
        """
        return {}

    def managed_keys(self) -> Dict[str, Any]:
        """Return what this action contributes to the manifest after apply.

        v3 actions override this; the default returns an empty dict.
        """
        return {}

    @classmethod
    def is_v3(cls) -> bool:
        """True if this subclass overrides ``plan`` — i.e. uses the v3 API.

        Used by the Reconciler (Plan 3) to decide between the v3
        ``plan``/``apply`` path and the legacy ``is_needed``/``execute`` path.
        """
        return cls.plan is not AbstractAction.plan

"""Base action for v3 domains whose state is a single value (not a set).

Set-math models a value change as INSTALL(new)+REMOVE(old); a scalar domain
wants one MODIFY instead. ScalarV3Action implements the v3 contract generically
over four subclass hooks. No CREATE/DELETE — a scalar is set or replaced, never
removed.
"""
from __future__ import annotations
from typing import Any, Optional
from .abstract_action import AbstractAction
from ..state.change import Change, Op


class ScalarV3Action(AbstractAction):
    """v3 contract for single-value domains."""

    _DOMAIN: str = ""

    @classmethod
    def empty_config(cls):
        """Scalar domains are dict-shaped; bootstrap from an empty dict."""
        return {}

    # --- subclass hooks ------------------------------------------------ #

    def _desired_value(self) -> Optional[str]:
        raise NotImplementedError

    def _actual_value(self) -> Optional[str]:
        raise NotImplementedError

    def _set_value(self) -> None:
        raise NotImplementedError

    def _import_fragment(self, value: str) -> dict:
        raise NotImplementedError

    # --- generic v3 contract ------------------------------------------ #

    def actual(self) -> set:
        v = self._actual_value()
        return {v} if v else set()

    def plan(self, managed: Any):
        desired = self._desired_value()
        if desired and desired != self._actual_value():
            return [Change(self._DOMAIN, Op.MODIFY, desired, reason="set")]
        return []

    def apply(self, changes) -> None:
        target = getattr(self.context, "target", None) if self.context else None
        if changes and target is not None:
            self._set_value()

    def managed_keys(self) -> dict:
        desired = self._desired_value()
        return {self._DOMAIN: [desired] if desired else []}

    def import_state(self, managed=None) -> dict:
        value = self._actual_value() or self._desired_value()
        return self._import_fragment(value) if value else {}

    # --- legacy executor path (concrete defaults over the same hooks) -- #

    def is_needed(self) -> bool:
        return bool(self.plan(managed=[]))

    def execute(self) -> None:
        self._set_value()

    def verify(self) -> bool:
        return not self.plan(managed=[])

"""Base action for v3 domains whose state is a composite record (a dict).

A composite is compared via a canonical JSON serialization, so a converged
record yields no change (idempotent). It reuses ScalarV3Action's value-based
machinery (actual/managed_keys/import_state/is_needed/execute/verify) and emits
a single MODIFY listing the changed fields.
"""
from __future__ import annotations
import json
from typing import Optional
from .scalar_action import ScalarV3Action
from ..state.change import Change, Op


class CompositeV3Action(ScalarV3Action):
    """v3 contract for multi-field (composite) domains."""

    # --- subclass hooks ------------------------------------------------ #

    def _desired_state(self) -> dict:
        raise NotImplementedError

    def _actual_state(self) -> Optional[dict]:
        raise NotImplementedError

    # --- bridge to ScalarV3Action's value machinery ------------------- #

    @staticmethod
    def _serialize(state: dict) -> str:
        return json.dumps(state, sort_keys=True)

    def _desired_value(self) -> Optional[str]:
        return self._serialize(self._desired_state())

    def _actual_value(self) -> Optional[str]:
        state = self._actual_state()
        return self._serialize(state) if state is not None else None

    # --- field-aware plan (clean render) ------------------------------ #

    def plan(self, managed):
        desired = self._desired_state()
        actual = self._actual_state()
        if actual == desired:
            return []
        if actual is None:
            changed = sorted(desired)
        else:
            changed = sorted(k for k in desired if desired.get(k) != actual.get(k))
        item = ",".join(changed) or self._DOMAIN
        return [Change(self._DOMAIN, Op.MODIFY, item, reason="config")]

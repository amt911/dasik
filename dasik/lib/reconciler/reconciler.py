"""Reconciler — orchestrates v3 actions to produce an aggregate Plan.

This is the pure orchestration layer (spec §3.6). It:
  * walks an action registry,
  * skips actions that are not yet v3 (``cls.is_v3() is False``),
  * extracts each action's config slice and its per-domain managed list
    from the manifest,
  * calls ``action.plan(managed)`` and collects the Changes,
  * returns an aggregate ``Plan`` (for rendering / destructive checks) plus
    a list of ``ActionPlanResult`` (per-action breakdown, needed by the
    apply path in Plan 4).

No I/O. No Command. The caller (CLI) is responsible for loading config,
resolving Target, and loading the manifest dict from StateStore.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional

from ..actions.abstract_action import AbstractAction
from ..actions.action_context import ActionContext
from ..state.change import Change, Plan
from ..state.state_store import Manifest
from ..target.target import Target


@dataclass
class ActionPlanResult:
    """Per-action planning result. Used by Plan 4's apply path."""

    action: AbstractAction
    changes: list[Change] = field(default_factory=list)


class Reconciler:
    """Builds an aggregate Plan by driving v3 actions over a registry.

    Args:
        config: the parsed config dict (root level).
        target: the Target commands will run against.
        manifest: the active manifest dict (``StateStore.load().to_dict()``)
            or ``None`` for first-apply / bootstrap.
        action_metas: iterable of registry entries — each a dict with keys
            ``class``, ``config_key``, ``is_optional``, ``required_fields``,
            ``depends_on``. Matches ``ActionRegistry.get_all_actions()``.
    """

    def __init__(
        self,
        config: dict[str, Any],
        target: Target,
        manifest: Optional[dict[str, Any]],
        action_metas: Iterable[dict[str, Any]],
        state_store: Optional[Any] = None,
        generation_store: Optional[Any] = None,
    ):
        self._config = config
        self._target = target
        self._manifest = manifest
        self._metas = list(action_metas)
        self._state_store = state_store
        self._generation_store = generation_store

    def build_plan(self) -> tuple[Plan, list[ActionPlanResult]]:
        managed_all = (self._manifest or {}).get("managed", {})
        ctx = ActionContext(target=self._target, manifest=self._manifest)

        plan = Plan()
        results: list[ActionPlanResult] = []

        for meta in self._metas:
            cls = meta["class"]
            if not cls.is_v3():
                continue

            config_key = meta["config_key"]
            if config_key == "__root__":
                action_config = self._config
            else:
                action_config = self._config.get(config_key)

            # Optional action whose section is absent AND has no managed
            # entries to clean up → skip; nothing for it to plan.
            if action_config is None:
                domain_managed_any = self._any_managed_for(cls, managed_all)
                if not domain_managed_any:
                    continue
                # If there are owned items but no config slice, default to
                # an empty config so REMOVE = M\D fires.
                action_config = self._empty_config_for(cls)

            action = cls(action_config, ctx)
            managed_for_action = self._managed_for(action, managed_all)
            changes = list(action.plan(managed=managed_for_action))

            plan.extend(changes)
            results.append(ActionPlanResult(action=action, changes=changes))

        return plan, results

    # -- helpers -------------------------------------------------------

    @staticmethod
    def _domain_for(action: AbstractAction) -> Optional[str]:
        """Pick the single domain key from ``managed_keys()``.

        Returns ``None`` if the action declares no managed domain. Raises
        ``ValueError`` if more than one domain is declared — multi-domain
        actions are not supported until Plan 4.
        """
        keys = action.managed_keys()
        if not isinstance(keys, dict) or not keys:
            return None
        if len(keys) > 1:
            raise ValueError(
                f"{type(action).__name__}.managed_keys() returned "
                f"{len(keys)} domains; multi-domain actions are not "
                "supported until Plan 4."
            )
        return next(iter(keys))

    @staticmethod
    def _managed_for(
        action: AbstractAction, managed_all: dict[str, Any]
    ) -> list[Any]:
        domain = Reconciler._domain_for(action)
        if domain is None:
            return []
        return list(managed_all.get(domain, []))

    @staticmethod
    def _any_managed_for(action_cls: type, managed_all: dict[str, Any]) -> bool:
        """Probe (via a no-config instance) whether the class owns any manifest keys.

        Broad ``except`` is intentional: the probe object has no real config or
        context, so ``managed_keys()`` may raise. We treat any error as
        "can't determine ownership → skip safely."
        """
        try:
            probe = action_cls.__new__(action_cls)
            probe.config = None
            probe.context = None
            keys = probe.managed_keys()
        except Exception:
            return False
        if not isinstance(keys, dict):
            return False
        return any(managed_all.get(k) for k in keys)

    @staticmethod
    def _empty_config_for(action_cls: type) -> Any:
        """When config slice is missing but managed has entries, hand the
        action an empty config of the right shape (list/dict) so its plan()
        can run. Defaults to ``[]`` — packages/users/systemd all accept a list.
        """
        return []

    def apply(
        self,
        plan: Plan,
        results: list[ActionPlanResult],
        *,
        assume_yes: bool = False,
        input_fn: Callable[[str], str] = input,
    ) -> Optional[Manifest]:
        """Execute a built plan: gate destructive ops, run each action's
        apply(), then persist the new manifest + generation.

        Args:
            plan: aggregate Plan from build_plan().
            results: per-action breakdown from build_plan().
            assume_yes: if True, skip the destructive-change prompt.
            input_fn: stdin reader (injectable for tests).

        Returns:
            The new ``Manifest`` if anything was applied, else ``None``.
        """
        if plan.is_empty():
            return None

        destructive = plan.destructive()
        if destructive and not assume_yes:
            answer = input_fn(
                f"Apply {len(destructive)} destructive change(s)? [y/N] "
            ).strip().lower()
            if answer not in ("y", "yes"):
                return None

        for result in results:
            result.action.apply(result.changes)

        if self._state_store is None and self._generation_store is None:
            return None

        new_manifest = self._build_new_manifest(results)

        if self._state_store is not None:
            self._state_store.save(new_manifest)
        if self._generation_store is not None:
            self._generation_store.new(self._config, new_manifest.to_dict())

        return new_manifest

    def _build_new_manifest(
        self, results: list[ActionPlanResult]
    ) -> Manifest:
        managed: dict[str, Any] = {}
        for result in results:
            try:
                keys = result.action.managed_keys()
            except Exception:
                keys = {}
            if isinstance(keys, dict):
                managed.update(keys)

        prev_generation = 0
        if isinstance(self._manifest, dict):
            prev_generation = int(self._manifest.get("generation", 0))

        config_hash = hashlib.sha256(
            json.dumps(self._config, sort_keys=True).encode("utf-8")
        ).hexdigest()

        return Manifest(
            generation=prev_generation + 1,
            applied_at=datetime.now(timezone.utc).isoformat(),
            config_hash=config_hash,
            managed=managed,
        )

from dataclasses import dataclass

from dasik.lib.actions.abstract_action import AbstractAction
from dasik.lib.reconciler.reconciler import ActionPlanResult, Reconciler
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target


class _LegacyOnly(AbstractAction):
    @property
    def name(self) -> str: return "legacy"
    def is_needed(self) -> bool: return True
    def execute(self) -> None: pass


class _PkgsV3(AbstractAction):
    """A minimal v3 action: declares packages; reports actual via class attr."""

    actual_set: set[str] = set()

    @property
    def name(self) -> str: return "pkgs"

    def is_needed(self) -> bool: return False

    def execute(self) -> None: pass

    def actual(self):
        return type(self).actual_set

    def plan(self, managed):
        from dasik.lib.state.set_math import compute_changes
        desired = self.config if isinstance(self.config, list) else []
        changes, drift = compute_changes(
            "packages", desired=desired, managed=managed, actual=self.actual()
        )
        type(self).last_drift = drift
        return changes

    def managed_keys(self):
        return {"packages": list(self.config) if isinstance(self.config, list) else []}


def _registry_entry(cls, config_key, is_optional=True):
    return {
        "class": cls,
        "config_key": config_key,
        "is_optional": is_optional,
        "required_fields": [],
        "depends_on": [],
    }


def test_build_plan_returns_empty_when_no_actions():
    r = Reconciler(
        config={},
        target=Target(root="/"),
        manifest={"managed": {}},
        action_metas=[],
    )
    plan, results = r.build_plan()
    assert plan.is_empty()
    assert results == []


def test_build_plan_skips_legacy_actions_silently():
    r = Reconciler(
        config={},
        target=Target(root="/"),
        manifest={"managed": {}},
        action_metas=[_registry_entry(_LegacyOnly, "anything")],
    )
    plan, results = r.build_plan()
    assert plan.is_empty()
    assert results == []


def test_build_plan_calls_v3_action_with_config_slice_and_managed():
    _PkgsV3.actual_set = {"git"}
    r = Reconciler(
        config={"packages": ["git", "htop"]},
        target=Target(root="/"),
        manifest={"managed": {"packages": ["git"]}},
        action_metas=[_registry_entry(_PkgsV3, "packages")],
    )
    plan, results = r.build_plan()
    items = [(c.op, c.item) for c in plan.changes]
    assert items == [(Op.INSTALL, "htop")]
    assert len(results) == 1
    res = results[0]
    assert isinstance(res, ActionPlanResult)
    assert res.changes == [Change("packages", Op.INSTALL, "htop")]
    assert res.action.config == ["git", "htop"]


def test_build_plan_uses_empty_managed_when_manifest_missing_domain():
    """First-apply: manifest has no entry for this domain → managed=[]."""
    _PkgsV3.actual_set = set()
    r = Reconciler(
        config={"packages": ["git"]},
        target=Target(root="/"),
        manifest={"managed": {}},   # no "packages" key
        action_metas=[_registry_entry(_PkgsV3, "packages")],
    )
    plan, _ = r.build_plan()
    assert [(c.op, c.item) for c in plan.changes] == [(Op.INSTALL, "git")]


def test_build_plan_uses_empty_managed_when_manifest_is_none():
    """No manifest at all (e.g., bootstrap before any apply) → managed=[]."""
    _PkgsV3.actual_set = set()
    r = Reconciler(
        config={"packages": ["git"]},
        target=Target(root="/"),
        manifest=None,
        action_metas=[_registry_entry(_PkgsV3, "packages")],
    )
    plan, _ = r.build_plan()
    assert [(c.op, c.item) for c in plan.changes] == [(Op.INSTALL, "git")]


def test_build_plan_skips_action_when_optional_section_missing():
    """Optional v3 action with no config slice and no managed entries → skip."""
    r = Reconciler(
        config={},  # no "packages"
        target=Target(root="/"),
        manifest={"managed": {}},
        action_metas=[_registry_entry(_PkgsV3, "packages", is_optional=True)],
    )
    plan, results = r.build_plan()
    assert plan.is_empty()
    assert results == []


def test_build_plan_runs_action_when_optional_section_missing_but_managed_has_entries():
    """Pure REMOVE case: config dropped 'packages' but manifest still owns some."""
    _PkgsV3.actual_set = {"vim"}
    r = Reconciler(
        config={},  # no "packages"
        target=Target(root="/"),
        manifest={"managed": {"packages": ["vim"]}},
        action_metas=[_registry_entry(_PkgsV3, "packages", is_optional=True)],
    )
    plan, results = r.build_plan()
    items = [(c.op, c.item) for c in plan.changes]
    assert items == [(Op.REMOVE, "vim")]
    assert results[0].action.config == []   # empty desired set


def test_action_context_passed_to_v3_action_carries_target_and_manifest():
    """Verify the ActionContext seen by the v3 action has target + manifest."""
    captured = {}

    class _Capture(_PkgsV3):
        def plan(self, managed):
            captured["target"] = self.context.target
            captured["manifest"] = self.context.manifest
            return []

    t = Target(root="/")
    manifest = {"managed": {"packages": []}}
    r = Reconciler(
        config={"packages": []},
        target=t,
        manifest=manifest,
        action_metas=[_registry_entry(_Capture, "packages")],
    )
    r.build_plan()
    assert captured["target"] is t
    assert captured["manifest"] is manifest

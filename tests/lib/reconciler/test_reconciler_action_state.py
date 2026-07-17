"""Reconciler merges each action's state_metadata() into the new manifest
(PLAN v3 §10.3): apply and sync both persist action_state."""
from unittest.mock import MagicMock

from dasik.lib.actions.abstract_action import AbstractAction
from dasik.lib.reconciler.reconciler import ActionPlanResult, Reconciler
from dasik.lib.state.change import Change, Op, Plan
from dasik.lib.target.target import Target


class _StatefulV3(AbstractAction):
    @property
    def name(self) -> str: return "stateful"
    def is_needed(self) -> bool: return False
    def execute(self) -> None: pass
    def plan(self, managed): return []
    def apply(self, changes): pass
    def managed_keys(self):
        return {"packages": list(self.config) if isinstance(self.config, list) else []}
    def state_metadata(self):
        return {"packages": {"source_refs": {"config-saver": "a" * 40}}}


def test_default_state_metadata_is_empty():
    assert AbstractAction.state_metadata(MagicMock()) == {}


def test_apply_merges_action_state_into_manifest():
    store = MagicMock()
    r = Reconciler(
        config={"packages": ["config-saver"]},
        target=Target(root="/"),
        manifest={"managed": {}},
        action_metas=[],
        state_store=store,
        generation_store=None,
    )
    a = _StatefulV3(config=["config-saver"], context=None)
    change = Change("packages", Op.INSTALL, "config-saver")
    plan = Plan()
    plan.add(change)
    new = r.apply(plan, [ActionPlanResult(action=a, changes=[change])], assume_yes=True)
    assert new.action_state == {"packages": {"source_refs": {"config-saver": "a" * 40}}}
    # persisted
    assert store.save.call_args.args[0].action_state == new.action_state


def test_sync_preserves_prior_action_state_without_fabricating():
    store = MagicMock()
    prior = {"packages": {"source_refs": {"config-saver": "c" * 40}}}
    r = Reconciler(
        config={"packages": ["config-saver"],
                "package_sources": {"config-saver": {"type": "pkgbuild-git",
                                                      "url": "https://github.com/x/y.git",
                                                      "ref": "d" * 40}}},
        target=Target(root="/"),
        manifest={"managed": {}, "action_state": prior},
        action_metas=[{"class": _StatefulV3, "config_key": "packages",
                       "is_optional": True, "required_fields": [], "depends_on": []}],
        state_store=store,
    )
    new_config, new_manifest = r.sync()
    # prior applied SHA preserved (NOT overwritten with the desired 'd'*40)
    assert new_manifest.action_state == prior
    # sibling package_sources survives the sync merge
    assert new_config["package_sources"]["config-saver"]["ref"] == "d" * 40

"""Property-based idempotency at the Reconciler (orchestration) level.

Per-action idempotency is proven elsewhere; this drives the real
``Reconciler.build_plan`` over a v3 action to assert the top-level invariants
CLAUDE.md names directly: ``reconcile(current, current)`` yields an empty plan,
applying then re-reconciling is a no-op, and drift the reconciler does not own
is never removed. A tiny fake domain supplies controllable "actual" state so the
whole plan pipeline (registry walk → ActionContext → managed lookup → set-math)
runs for real without touching a system.
"""
import pytest
from hypothesis import given
from hypothesis import strategies as st

from dasik.lib.actions.abstract_action import AbstractAction
from dasik.lib.reconciler.reconciler import Reconciler
from dasik.lib.state.change import Op
from dasik.lib.target.target import Target


class _FakeDomain(AbstractAction):
    """A v3 action whose 'actual' state is a controllable class attribute."""

    _actual: set = set()

    @property
    def name(self) -> str:
        return "fake"

    def is_needed(self) -> bool:
        return False

    def execute(self) -> None:
        pass

    def actual(self):
        return set(type(self)._actual)

    def plan(self, managed):
        from dasik.lib.state.set_math import compute_changes
        desired = self.config if isinstance(self.config, list) else []
        changes, _drift = compute_changes(
            "pkg", desired=desired, managed=managed, actual=self.actual()
        )
        return changes

    def managed_keys(self):
        return {"pkg": list(self.config) if isinstance(self.config, list) else []}


def _meta(cls, key):
    return {"class": cls, "config_key": key, "is_optional": True,
            "required_fields": [], "depends_on": []}


def _reconciler(desired, managed):
    return Reconciler(
        config={"pkg": sorted(desired)},
        target=Target(root="/mnt"),
        manifest={"managed": {"pkg": sorted(managed)}},
        action_metas=[_meta(_FakeDomain, "pkg")],
    )


@pytest.fixture(autouse=True)
def _reset():
    _FakeDomain._actual = set()
    yield
    _FakeDomain._actual = set()


_names = st.sets(st.text(alphabet="abcde", min_size=1, max_size=3), max_size=6)


@given(pkgs=_names)
def test_reconcile_current_current_is_empty_plan(pkgs):
    """desired == managed == actual ⇒ build_plan is empty (re-run is a no-op)."""
    _FakeDomain._actual = set(pkgs)
    plan, _ = _reconciler(desired=pkgs, managed=pkgs).build_plan()
    assert plan.is_empty()


@given(pkgs=_names)
def test_apply_then_reconcile_is_a_noop(pkgs):
    """First reconcile on a fresh system installs; after that state lands and
    dasik owns it, a second reconcile is empty — one apply converges."""
    _FakeDomain._actual = set()
    plan1, _ = _reconciler(desired=pkgs, managed=set()).build_plan()
    assert plan1.is_empty() == (len(pkgs) == 0)   # installs iff something declared

    _FakeDomain._actual = set(pkgs)               # simulate the apply landing
    plan2, _ = _reconciler(desired=pkgs, managed=pkgs).build_plan()
    assert plan2.is_empty()


@given(desired=_names, extra=_names)
def test_reconciler_never_removes_unowned_drift(desired, extra):
    """A package present on the system but not declared and not owned by dasik
    is drift — the reconciler never emits a REMOVE for it."""
    strangers = set(extra) - set(desired)
    _FakeDomain._actual = set(desired) | strangers
    plan, _ = _reconciler(desired=desired, managed=desired).build_plan()
    removed = {c.item for c in plan.changes if c.op is Op.REMOVE}
    assert removed.isdisjoint(strangers)

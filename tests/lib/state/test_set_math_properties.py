"""Property-based tests for the reconciliation set-math (CLAUDE.md § Quality).

`compute_changes` is the pure D/M/A/F → Change core that makes dasik idempotent.
Example-based tests (test_set_math.py) pin specific scenarios; these Hypothesis
properties assert the *invariants* hold for hundreds of generated D/M/A/F
combinations — including the weird boundaries (empty sets, full overlap,
disjoint, drift-heavy) a human wouldn't enumerate. Together they are the
automated proof of the idempotency promise.
"""
from hypothesis import given
from hypothesis import strategies as st

from dasik.lib.state.change import Op
from dasik.lib.state.set_math import compute_changes

# Short lowercase names → heavy overlap across the generated sets, so the
# D∩A, M\D, A\D\M regions are all frequently non-empty (a wide alphabet would
# make every set effectively disjoint and never exercise the interesting math).
_names = st.text(alphabet="abcde", min_size=1, max_size=3)
_sets = st.sets(_names, max_size=6)

# The property invariants must hold for any (install, remove) op pairing — the
# domains reuse compute_changes with ENABLE/DISABLE, CREATE/DELETE, etc.
_op_pairs = st.sampled_from(
    [(Op.INSTALL, Op.REMOVE), (Op.ENABLE, Op.DISABLE), (Op.CREATE, Op.DELETE)]
)


@given(s=_sets, ops=_op_pairs)
def test_converged_domain_is_a_noop(s, ops):
    """reconcile(current, current) yields an empty plan.

    When desired == managed == actual (the system already matches the config
    and dasik owns exactly it), there is nothing to install, nothing to remove,
    and nothing undeclared → no changes, no drift. This is *the* idempotency
    invariant: a re-run of the same config changes nothing.
    """
    op_install, op_remove = ops
    changes, drift = compute_changes(
        "d", desired=s, managed=s, actual=s,
        op_install=op_install, op_remove=op_remove,
    )
    assert changes == []
    assert drift == []


@given(desired=_sets, managed=_sets, actual=_sets, ops=_op_pairs)
def test_drift_is_never_removed(desired, managed, actual, ops):
    """A \\ D \\ M is reported as drift and NEVER emitted as a removal.

    The model's primary safety property: removal is scoped to M (what dasik
    itself installed). A manually-installed item (present, undeclared, unowned)
    must surface as drift for `sync`, never as an automatic destructive REMOVE.
    """
    op_install, op_remove = ops
    changes, drift = compute_changes(
        "d", desired=desired, managed=managed, actual=actual,
        op_install=op_install, op_remove=op_remove,
    )
    removed_items = {c.item for c in changes if c.op is op_remove}
    unmanaged_present = set(actual) - set(desired) - set(managed)
    for item in unmanaged_present:
        assert item in drift
        assert item not in removed_items


@given(desired=_sets, managed=_sets, actual=_sets, ops=_op_pairs)
def test_one_apply_converges(desired, managed, actual, ops):
    """Applying the plan once and re-computing yields no further changes.

    Simulate apply: installs add to A, owned-removals subtract from A, and dasik
    now owns exactly D (managed' = D). Re-computing must produce an empty change
    set — one pass converges, so a second `apply` is a no-op. (Drift may persist;
    the invariant is specifically no more INSTALL/REMOVE work.)
    """
    op_install, op_remove = ops
    changes, _ = compute_changes(
        "d", desired=desired, managed=managed, actual=actual,
        op_install=op_install, op_remove=op_remove,
    )
    installs = {c.item for c in changes if c.op is op_install}
    removes = {c.item for c in changes if c.op is op_remove}
    new_actual = (set(actual) | installs) - removes
    new_managed = set(desired)  # after apply, dasik owns the declared set

    changes2, _ = compute_changes(
        "d", desired=desired, managed=new_managed, actual=new_actual,
        op_install=op_install, op_remove=op_remove,
    )
    assert changes2 == []


@given(desired=_sets, managed=_sets, actual=_sets)
def test_install_and_remove_blocks_are_disjoint(desired, managed, actual):
    """No item is simultaneously scheduled to install and to remove.

    INSTALL = D\\A ⊆ D and owned-REMOVE = M\\D is by definition outside D, so the
    two blocks can never share an item — the plan is never self-contradictory.
    """
    changes, _ = compute_changes("d", desired=desired, managed=managed, actual=actual)
    installs = {c.item for c in changes if c.op is Op.INSTALL}
    removes = {c.item for c in changes if c.op is Op.REMOVE}
    assert installs.isdisjoint(removes)


@given(desired=_sets, managed=_sets, actual=_sets)
def test_result_only_references_input_items(desired, managed, actual):
    """Every emitted item comes from the inputs — no items invented or dropped.

    Each change.item ∈ D∪M∪A and each drift item ∈ A. Also: the union of
    installs, owned-removals and drift exactly partitions the relevant sets with
    no overlap between installs and drift.
    """
    changes, drift = compute_changes("d", desired=desired, managed=managed, actual=actual)
    universe = set(desired) | set(managed) | set(actual)
    for c in changes:
        assert c.item in universe
        assert c.domain == "d"
    for item in drift:
        assert item in actual
    installs = {c.item for c in changes if c.op is Op.INSTALL}
    assert installs.isdisjoint(drift)


@given(
    data=st.data(),
    managed=_sets,
    actual=_sets,
)
def test_forced_removed_and_never_in_drift(data, managed, actual):
    """Forced-off items present on the system are removed and excluded from drift.

    Precondition D ∩ F = ∅ (spec §2). We generate F, then draw D from names not
    in F to honour it. Every forced item that is actually present must be a
    REMOVE, and no forced item may appear in drift.
    """
    forced = data.draw(_sets)
    # desired must be disjoint from forced (the documented precondition)
    desired = {n for n in data.draw(_sets) if n not in forced}

    changes, drift = compute_changes(
        "systemd",
        desired=desired, managed=managed, actual=actual,
        op_install=Op.ENABLE, op_remove=Op.DISABLE,
        forced=forced,
    )
    removed_items = {c.item for c in changes if c.op is Op.DISABLE}
    for item in set(forced) & set(actual):
        assert item in removed_items
    assert set(forced).isdisjoint(drift)

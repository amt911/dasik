"""Pure D/M/A → Change set-math (spec §2).

No I/O, no Command, no Target — this is the heart of the reconciliation model
and the highest-value unit to test exhaustively (spec §8).
"""
from typing import Iterable

from .change import Change, Op


def compute_changes(
    domain: str,
    *,
    desired: Iterable[str],
    managed: Iterable[str],
    actual: Iterable[str],
    op_install: Op = Op.INSTALL,
    op_remove: Op = Op.REMOVE,
) -> tuple[list[Change], list[str]]:
    """Compute the (changes, drift) tuple for one domain.

    Set semantics (spec §2):
        INSTALL = D \\ A          declared, absent          → add / enable / create
        REMOVE  = M \\ D          owned, no longer declared → DESTRUCTIVE
        DRIFT   = A \\ D \\ M     present, neither declared nor owned → REPORTED, UNTOUCHED

    The primary safety property of this model: removal is scoped to M (what
    dasik itself applied). Manually-installed items appear as drift and become
    candidates for `sync`, never for automatic removal.

    Args:
        domain: domain label embedded into each Change (e.g. "packages").
        desired: D — the set the config declares.
        managed: M — the set the manifest records as owned by dasik.
        actual:  A — the set actually present on the system.
        op_install: Change op for D \\ A. Defaults to INSTALL; pass ENABLE for
            systemd, CREATE for files, etc.
        op_remove: Change op for M \\ D. Defaults to REMOVE; pass DISABLE for
            systemd, DELETE for files, etc.

    Returns:
        (changes, drift) — the install-block first (sorted by item), then the
        remove-block (sorted by item); drift sorted alphabetically. Changes carry
        ``reason="no longer declared"`` for removals so plan rendering explains
        the destructive op.
    """
    D, M, A = set(desired), set(managed), set(actual)

    changes: list[Change] = []
    for item in sorted(D - A):
        changes.append(Change(domain, op_install, item))
    for item in sorted(M - D):
        changes.append(Change(domain, op_remove, item, reason="no longer declared"))

    drift = sorted(A - D - M)
    return changes, drift

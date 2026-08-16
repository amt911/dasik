"""Generations are kept for ever unless somebody asks for a prune.

Measured before this existed: 25 applies against a scratch root left 25
directories, each holding a full config plus its manifest. On a machine that
applies daily that is ~365 a year, and `dasik generations` prints all of them.

The policy is explicit-only — `dasik generations --prune N` — so a config change
can never delete history behind your back. Two invariants hold whatever N says:
the **current** generation is never pruned, and a **partial** one is never left
as the newest survivor (rollback already refuses to restore one, so a prune that
left only partials would leave nothing to roll back to).
"""
import json

import pytest

from dasik.lib.state.generation_store import GenerationStore


class _Target:
    def __init__(self, root):
        self.root = str(root)

    def path(self, canonical):
        return f"{self.root}{canonical}"


def _store(tmp_path):
    return GenerationStore(_Target(tmp_path))


def _fill(store, count, partial_numbers=()):
    for n in range(1, count + 1):
        store.new({"hostname": f"gen{n}"},
                  {"generation": n, "partial": n in partial_numbers})
    return store


def _numbers(store):
    return [g.number for g in store.list()]


def test_pruning_keeps_the_n_most_recent(tmp_path):
    store = _fill(_store(tmp_path), 6)

    removed = store.prune(keep=2)

    assert _numbers(store) == [5, 6]
    assert removed == [1, 2, 3, 4]


def test_pruning_reports_what_it_deleted(tmp_path):
    store = _fill(_store(tmp_path), 3)

    assert store.prune(keep=1) == [1, 2]


def test_nothing_to_prune_is_not_an_error(tmp_path):
    store = _fill(_store(tmp_path), 2)

    assert store.prune(keep=5) == []
    assert _numbers(store) == [1, 2]


def test_an_empty_store_prunes_nothing(tmp_path):
    assert _store(tmp_path).prune(keep=3) == []


def test_the_directory_is_really_gone(tmp_path):
    store = _fill(_store(tmp_path), 3)

    store.prune(keep=1)

    assert not (store.base_dir / "1").exists()
    assert (store.base_dir / "3" / "config.json").exists()


def test_the_current_generation_is_never_pruned(tmp_path):
    """Rolled back to 1, then prune hard: 1 must survive even though it is the
    oldest, because it is what the machine is running."""
    store = _fill(_store(tmp_path), 5)
    store.restore(1)                     # current -> 1

    removed = store.prune(keep=1)

    assert 1 in _numbers(store)
    assert 1 not in removed
    assert store.current_link.readlink().name == "1"


def test_a_partial_generation_is_never_the_newest_survivor(tmp_path):
    """Rollback refuses to restore a partial one, so a prune that left only
    partials would leave nothing to roll back to. The newest COMPLETE
    generation is kept as well."""
    store = _fill(_store(tmp_path), 4, partial_numbers={3, 4})

    store.prune(keep=1)

    kept = _numbers(store)
    assert 2 in kept, "the newest complete generation must survive"


def test_keep_must_be_at_least_one(tmp_path):
    store = _fill(_store(tmp_path), 3)

    with pytest.raises(ValueError):
        store.prune(keep=0)


def test_pruning_does_not_disturb_what_it_keeps(tmp_path):
    store = _fill(_store(tmp_path), 4)

    store.prune(keep=2)

    config = json.loads((store.base_dir / "4" / "config.json").read_text())
    assert config == {"hostname": "gen4"}


def test_a_later_apply_still_numbers_upwards(tmp_path):
    """Numbering comes from the highest existing directory, so pruning must not
    make the next apply reuse a number a rollback could still name."""
    store = _fill(_store(tmp_path), 5)
    store.prune(keep=1)

    store.new({"hostname": "next"}, {"generation": 6})

    assert _numbers(store)[-1] == 6

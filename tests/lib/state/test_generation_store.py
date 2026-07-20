import pytest

from dasik.lib.state.generation_store import GenerationStore
from dasik.lib.target.target import Target


def test_no_generations_lists_empty(tmp_target):
    assert GenerationStore(tmp_target).list() == []


def test_new_creates_generation_one_and_current(tmp_target):
    store = GenerationStore(tmp_target)
    n = store.new({"hostname": "box"}, {"generation": 1})
    assert n == 1
    gens = store.list()
    assert len(gens) == 1
    assert gens[0].number == 1
    assert gens[0].is_current is True


def test_second_new_increments_and_moves_current(tmp_target):
    store = GenerationStore(tmp_target)
    store.new({"a": 1}, {"generation": 1})
    n2 = store.new({"a": 2}, {"generation": 2})
    assert n2 == 2
    by_num = {g.number: g for g in store.list()}
    assert by_num[1].is_current is False
    assert by_num[2].is_current is True


def test_restore_switches_current_and_returns_snapshot(tmp_target):
    store = GenerationStore(tmp_target)
    store.new({"a": 1}, {"generation": 1})
    store.new({"a": 2}, {"generation": 2})

    config, manifest = store.restore(1)
    assert config == {"a": 1}
    assert manifest == {"generation": 1}
    by_num = {g.number: g for g in store.list()}
    assert by_num[1].is_current is True
    assert by_num[2].is_current is False


def test_restore_unknown_raises(tmp_target):
    store = GenerationStore(tmp_target)
    store.new({"a": 1}, {"generation": 1})
    with pytest.raises(FileNotFoundError):
        store.restore(99)


def test_list_is_numerically_ordered_past_ten(tmp_target):
    store = GenerationStore(tmp_target)
    for i in range(1, 12):  # creates generations 1..11
        store.new({"gen": i}, {"generation": i})
    assert [g.number for g in store.list()] == list(range(1, 12))


# --- partial generations (F-01) -------------------------------------------- #

def test_list_marks_a_partial_generation(tmp_path):
    store = GenerationStore(Target(root=str(tmp_path)))
    store.new({"packages": []}, {"generation": 1, "partial": False})
    store.new({"packages": []}, {"generation": 2, "partial": True})
    gens = {g.number: g for g in store.list()}
    assert gens[1].partial is False
    assert gens[2].partial is True


def test_restore_refuses_a_partial_generation(tmp_path):
    """A partial generation records progress, not a state you can return TO."""
    store = GenerationStore(Target(root=str(tmp_path)))
    store.new({"packages": []}, {"generation": 1, "partial": True})
    with pytest.raises(ValueError, match="partial"):
        store.restore(1)


def test_restore_still_works_for_a_complete_generation(tmp_path):
    store = GenerationStore(Target(root=str(tmp_path)))
    store.new({"packages": ["git"]}, {"generation": 1})
    config, _manifest = store.restore(1)
    assert config == {"packages": ["git"]}

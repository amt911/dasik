"""The manifest must survive a power cut mid-write.

dasik already met one: #214 came from an apply interrupted by a power cut, which
left a pacman lock behind. The same cut lands on `state.json` — written with a
plain `write_text`, so the window between truncate and flush leaves a half file.
That file is dasik's record of what it owns; losing it means the next plan
proposes to re-own the machine, and reading it raised a bare JSONDecodeError:

    json.decoder.JSONDecodeError: Unterminated string starting at: line 1 column 28

Two things are asserted here: the write goes through a temporary file and lands
with a rename (so a reader sees either the old manifest or the new one, never a
half one), and a manifest that IS corrupt — from a cut before this fix, or a full
disk — says so in a sentence that names the file and the way out.
"""
import json

import pytest

from dasik.lib.state.state_store import Manifest, StateStore
from dasik.lib.target.target import Target


def _store(tmp_path):
    (tmp_path / "var/lib/dasik").mkdir(parents=True, exist_ok=True)
    return StateStore(Target(root=str(tmp_path)))


def test_the_write_lands_with_a_rename(tmp_path, monkeypatch):
    """A reader sees the old file or the new one — never a truncated one."""
    store = _store(tmp_path)
    store.save(Manifest(generation=1))

    seen = {}
    real_replace = __import__("os").replace

    def spy(src, dst):
        # at this instant the destination must still hold the OLD manifest
        seen["dst_before"] = json.loads(open(dst).read())["generation"]
        return real_replace(src, dst)

    monkeypatch.setattr("dasik.lib.state.state_store.os.replace", spy)
    store.save(Manifest(generation=2))

    assert seen["dst_before"] == 1
    assert json.loads(store.state_path.read_text())["generation"] == 2


def test_no_temporary_file_is_left_behind(tmp_path):
    store = _store(tmp_path)
    store.save(Manifest(generation=1))

    assert [p.name for p in store.state_path.parent.iterdir()] == ["state.json"]


def test_a_corrupt_manifest_is_explained_not_dumped(tmp_path):
    store = _store(tmp_path)
    store.state_path.write_text('{"version": 1, "domains": {"packa')

    with pytest.raises(ValueError) as exc:
        store.load()

    message = str(exc.value)
    assert str(store.state_path) in message
    assert "sync" in message


def test_an_absent_manifest_is_still_just_an_empty_one(tmp_path):
    assert _store(tmp_path).load().generation == 0


def test_a_good_manifest_round_trips(tmp_path):
    store = _store(tmp_path)
    store.save(Manifest(generation=7, managed={"packages": ["git"]}, partial=True))

    loaded = store.load()

    assert (loaded.generation, loaded.managed, loaded.partial) == (
        7, {"packages": ["git"]}, True)


def test_a_generation_is_written_atomically_too(tmp_path, monkeypatch):
    """A generation half-written by a cut is a config `rollback` would restore."""
    from dasik.lib.state.generation_store import GenerationStore

    store = GenerationStore(Target(root=str(tmp_path)))
    renames = []
    real_replace = __import__("os").replace
    monkeypatch.setattr("dasik.lib.state.generation_store.os.replace",
                        lambda src, dst: (renames.append(str(dst)), real_replace(src, dst))[1])

    number = store.new({"packages": ["git"]}, Manifest(generation=1).to_dict())

    assert [p.name for p in sorted((tmp_path / "var/lib/dasik/generations"
                                    / str(number)).iterdir())] == ["config.json", "state.json"]
    assert len(renames) == 2
    restored_config, restored_manifest = store.restore(number)
    assert restored_config == {"packages": ["git"]}
    assert restored_manifest["generation"] == 1


def test_rollback_says_the_link_is_missing_rather_than_blaming_the_generations(tmp_path):
    """The other half of a damaged state dir: `current` is a symlink, repointed
    by unlink-then-symlink, so a cut can leave it absent. dasik then refused with
    "no earlier generation to roll back to" — which is false; there are plenty,
    it just does not know which one it is standing on."""
    from dasik.__main__ import _cmd_rollback
    from dasik.lib.state.generation_store import GenerationStore

    store = GenerationStore(Target(root=str(tmp_path)))
    store.new({"packages": ["git"]}, Manifest(generation=1).to_dict())
    store.new({"packages": ["git", "htop"]}, Manifest(generation=2).to_dict())
    store.current_link.unlink()

    import io, contextlib
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = _cmd_rollback(str(tmp_path), None, assume_yes=True)

    assert rc == 1
    assert "current" in err.getvalue()
    assert "dasik rollback <number>" in err.getvalue()


def test_two_writers_do_not_fight_over_one_temporary(tmp_path):
    """The temporary must be per-process.

    Both writers used `<path>.tmp`, so one renamed it away while the other was
    still about to — and the loser died on the rename:

        FileNotFoundError: '…/state.json.tmp' -> '…/state.json'

    Interleaved content was the old failure; a crash inside `_persist`, half way
    through an apply that has already changed the machine, is a worse one.
    """
    import threading

    from dasik.lib.state.state_store import StateStore

    store = _store(tmp_path)
    errors = []

    def writer(generation):
        try:
            for _ in range(40):
                store.save(Manifest(generation=generation))
        except Exception as exc:               # noqa: BLE001 - the point of the test
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert store.load().generation in (1, 2)   # one of them won, cleanly
    assert [p.name for p in store.state_path.parent.iterdir()] == ["state.json"]

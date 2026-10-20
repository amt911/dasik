"""tree_fingerprint: did a step change what is on disk, when no version says so."""
from dasik.lib.upgrade.fingerprint import tree_fingerprint


def _tree(root, files):
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    return root


def test_none_when_no_path_exists(tmp_path):
    assert tree_fingerprint([str(tmp_path / "nope")]) is None
    assert tree_fingerprint([]) is None


def test_identical_trees_match(tmp_path):
    a = _tree(tmp_path / "a", {"SKILL.md": "x", "sub/f": "y"})
    b = _tree(tmp_path / "b", {"SKILL.md": "x", "sub/f": "y"})
    assert tree_fingerprint([str(a)]) == tree_fingerprint([str(b)])


def test_content_change_changes_it(tmp_path):
    a = _tree(tmp_path / "a", {"SKILL.md": "x"})
    before = tree_fingerprint([str(a)])
    (a / "SKILL.md").write_text("x2")
    assert tree_fingerprint([str(a)]) != before


def test_new_file_changes_it(tmp_path):
    a = _tree(tmp_path / "a", {"SKILL.md": "x"})
    before = tree_fingerprint([str(a)])
    (a / "extra").write_text("")
    assert tree_fingerprint([str(a)]) != before


def test_rename_changes_it(tmp_path):
    a = _tree(tmp_path / "a", {"one": "x"})
    before = tree_fingerprint([str(a)])
    (a / "one").rename(a / "two")
    assert tree_fingerprint([str(a)]) != before


def test_git_metadata_is_ignored(tmp_path):
    a = _tree(tmp_path / "a", {"SKILL.md": "x"})
    before = tree_fingerprint([str(a)])
    _tree(a, {".git/FETCH_HEAD": "fetched just now"})
    assert tree_fingerprint([str(a)]) == before


def test_missing_paths_among_present_ones_are_skipped(tmp_path):
    a = _tree(tmp_path / "a", {"f": "x"})
    assert tree_fingerprint([str(a), str(tmp_path / "gone")]) \
        == tree_fingerprint([str(a)])


def test_which_path_holds_a_file_matters(tmp_path):
    a = _tree(tmp_path / "a", {"f": "x"})
    b = tmp_path / "b"
    b.mkdir()
    first = tree_fingerprint([str(a), str(b)])
    (a / "f").rename(b / "f")
    assert tree_fingerprint([str(a), str(b)]) != first


def test_symlink_is_hashed_by_target_not_followed(tmp_path):
    a = _tree(tmp_path / "a", {"real/f": "x"})
    (a / "link").symlink_to("real")
    before = tree_fingerprint([str(a)])
    (a / "link").unlink()
    (a / "link").symlink_to("elsewhere")
    assert tree_fingerprint([str(a)]) != before


def test_symlinked_top_is_followed(tmp_path):
    # A marketplace or plugin dir that is itself a link: its contents count.
    real = _tree(tmp_path / "real", {"f": "x"})
    top = tmp_path / "top"
    top.symlink_to(real)
    before = tree_fingerprint([str(top)])
    (real / "f").write_text("x2")
    assert tree_fingerprint([str(top)]) != before


def test_unreadable_file_gives_none_not_an_exception(tmp_path, monkeypatch):
    # A measurement error is not an upgrade failure: the step falls back to
    # "refreshed" instead of turning FAILED and skipping its dependants.
    a = _tree(tmp_path / "a", {"f": "x"})

    def denied(*args, **kwargs):
        raise PermissionError("denied")
    monkeypatch.setattr("builtins.open", denied)
    assert tree_fingerprint([str(a)]) is None

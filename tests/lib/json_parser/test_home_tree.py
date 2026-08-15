"""`home_tree`: a directory that mirrors users' homes, like `etc_tree` does /etc.

A captured `home_files` entry is a file body inside a JSON string — a YAML
document with its comments escaped onto one line, which is exactly what
`etc_tree` exists to avoid on the `/etc` side. The tree is the same idea with
one extra level, because a home file is addressed as (user, path):

    home/
    └── andres/
        └── .config/config-saver/configs.d/zsh.yaml

The user directory is a **user name**, not a path: the machine decides where a
home lives, and dasik reads its /etc/passwd to find it.
"""
import pytest

from dasik.lib.json_parser.home_tree import (
    ConfigTreeError,
    expand_home_tree,
    extract_to_home_tree,
)


def _tree(tmp_path, files):
    for relative, content in files.items():
        path = tmp_path / "home" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return tmp_path


def _by_path(config):
    return {(e["user"], e["path"]): e for e in config["home_files"]}


# --- expansion --------------------------------------------------------------- #

def test_no_home_tree_key_changes_nothing(tmp_path):
    config = {"home_files": [{"user": "a", "path": ".zshrc", "content": "x"}]}
    assert expand_home_tree(config, tmp_path) == config


def test_each_file_becomes_an_entry_for_its_user(tmp_path):
    _tree(tmp_path, {"andres/.config/config-saver/configs.d/zsh.yaml":
                     "# why\ndirectories: []\n",
                     "root/.bashrc": "umask 077\n"})

    entries = _by_path(expand_home_tree({"home_tree": "home"}, tmp_path))

    assert set(entries) == {("andres", ".config/config-saver/configs.d/zsh.yaml"),
                            ("root", ".bashrc")}
    assert entries[("andres", ".config/config-saver/configs.d/zsh.yaml")]["content"] \
        == "# why\ndirectories: []\n"


def test_an_executable_file_gets_0755(tmp_path):
    _tree(tmp_path, {"andres/.local/bin/hook": "#!/bin/sh\n"})
    (tmp_path / "home" / "andres" / ".local" / "bin" / "hook").chmod(0o755)

    entries = _by_path(expand_home_tree({"home_tree": "home"}, tmp_path))

    assert entries[("andres", ".local/bin/hook")]["mode"] == "0755"


def test_a_declared_mode_wins(tmp_path):
    _tree(tmp_path, {"andres/.ssh/config": "Host *\n"})

    entries = _by_path(expand_home_tree(
        {"home_tree": "home", "home_tree_modes": {"andres/.ssh/config": "0600"}},
        tmp_path))

    assert entries[("andres", ".ssh/config")]["mode"] == "0600"


def test_an_explicit_entry_wins_over_the_tree(tmp_path):
    _tree(tmp_path, {"andres/.zshrc": "from-tree\n"})

    entries = _by_path(expand_home_tree(
        {"home_tree": "home",
         "home_files": [{"user": "andres", "path": ".zshrc", "content": "explicit\n"}]},
        tmp_path))

    assert entries[("andres", ".zshrc")]["content"] == "explicit\n"
    assert len(entries) == 1


def test_a_symlink_is_refused(tmp_path):
    _tree(tmp_path, {"andres/.zshrc": "x\n"})
    (tmp_path / "secret").write_text("not yours\n")
    (tmp_path / "home" / "andres" / "leak").symlink_to(tmp_path / "secret")

    with pytest.raises(ConfigTreeError, match="symlink"):
        expand_home_tree({"home_tree": "home"}, tmp_path)


def test_a_file_directly_in_the_tree_root_is_refused(tmp_path):
    """The first level is a USER, so a loose file there has no owner."""
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "stray.yaml").write_text("x\n")

    with pytest.raises(ConfigTreeError, match="stray.yaml"):
        expand_home_tree({"home_tree": "home"}, tmp_path)


# --- extraction (the sync side) ---------------------------------------------- #

def test_a_captured_entry_leaves_the_json(tmp_path):
    (tmp_path / "home").mkdir()
    config = {"home_tree": "home",
              "home_files": [{"user": "andres",
                              "path": ".config/config-saver/configs.d/zsh.yaml",
                              "content": "# why\ndirectories: []\n"}]}

    result = extract_to_home_tree(config, tmp_path)

    assert result.config["home_files"] == []
    assert result.writes == {
        tmp_path / "home" / "andres" / ".config/config-saver/configs.d/zsh.yaml":
            "# why\ndirectories: []\n"}


def test_a_mode_git_cannot_carry_is_declared(tmp_path):
    (tmp_path / "home").mkdir()
    config = {"home_tree": "home",
              "home_files": [{"user": "andres", "path": ".ssh/config",
                              "content": "Host *\n", "mode": "0600"}]}

    result = extract_to_home_tree(config, tmp_path)

    assert result.config["home_tree_modes"] == {"andres/.ssh/config": "0600"}
    assert result.modes == {tmp_path / "home" / "andres" / ".ssh/config": 0o600}


def test_a_file_the_machine_dropped_leaves_the_tree(tmp_path):
    stale = tmp_path / "home" / "andres" / ".gone"
    stale.parent.mkdir(parents=True)
    stale.write_text("old\n")
    config = {"home_tree": "home",
              "home_files": [{"user": "andres", "path": ".zshrc", "content": "x\n"}]}

    assert stale in extract_to_home_tree(config, tmp_path).deletions

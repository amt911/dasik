"""Capturing the config-saver documents a user wrote by hand.

They are the one thing in `$HOME` that is pure declarative policy: a short list
of what to back up, in a directory nobody else writes. Everything else in a home
— ssh keys, browser profiles, gigabytes of state — is why `home_files` refuses
to scan, and that refusal stays.

Capturing them is what removes the last duplication: you edit the YAML, `sync`
puts it in the config, and a machine dasik installs has the documents **before
anyone logs in** — instead of only after an archive is restored.
"""
from pathlib import Path

import pytest

from dasik.lib.actions.home_files_action import HomeFilesAction


class _Target:
    is_chroot = False

    def __init__(self, root: Path):
        self.root = str(root)

    def path(self, canonical: str) -> str:
        return f"{self.root}{canonical}"


class _Context:
    def __init__(self, target):
        self.target = target


@pytest.fixture
def machine(tmp_path):
    """A target with one real user and a config-saver documents directory."""
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "passwd").write_text(
        "root:x:0:0::/root:/bin/bash\n"
        "andres:x:1000:1000::/home/andres:/bin/zsh\n"
        "nobody:x:65534:65534::/:/usr/bin/nologin\n")
    docs = tmp_path / "home" / "andres" / ".config" / "config-saver" / "configs.d"
    docs.mkdir(parents=True)
    return tmp_path, docs


def _capture(root, config=None):
    action = HomeFilesAction(config or {}, context=_Context(_Target(root)))
    return action.import_state().get("home_files", [])


def test_a_document_the_user_wrote_is_captured(machine):
    root, docs = machine
    (docs / "zsh.yaml").write_text("directories:\n  - \"$HOME/.zshrc\"\n")

    captured = _capture(root)

    assert captured == [{"user": "andres",
                         "path": ".config/config-saver/configs.d/zsh.yaml",
                         "content": "directories:\n  - \"$HOME/.zshrc\"\n"}]


def test_comments_survive_because_the_file_is_taken_verbatim(machine):
    """The whole reason not to convert them to JSON."""
    root, docs = machine
    body = "# why this path is here\ndirectories:\n  - \"$HOME/.zshrc\"\n"
    (docs / "zsh.yaml").write_text(body)

    assert _capture(root)[0]["content"] == body


def test_the_rest_of_the_home_is_still_not_scanned(machine):
    root, docs = machine
    (docs / "zsh.yaml").write_text("directories: []\n")
    ssh = root / "home" / "andres" / ".ssh"
    ssh.mkdir()
    (ssh / "id_ed25519").write_text("PRIVATE KEY")

    paths = [e["path"] for e in _capture(root)]

    assert paths == [".config/config-saver/configs.d/zsh.yaml"]


def test_a_symlink_is_skipped(machine):
    root, docs = machine
    (root / "elsewhere.yaml").write_text("directories: []\n")
    (docs / "link.yaml").symlink_to(root / "elsewhere.yaml")

    assert _capture(root) == []


def test_a_declared_entry_is_not_duplicated(machine):
    root, docs = machine
    (docs / "zsh.yaml").write_text("new\n")
    config = {"home_files": [{"user": "andres",
                              "path": ".config/config-saver/configs.d/zsh.yaml",
                              "content": "declared\n"}]}

    captured = _capture(root, config)

    assert len(captured) == 1, "the declared entry and the discovered one are one"


def test_a_system_user_is_not_scanned(machine):
    """uid < 1000 is not somebody who writes backup documents."""
    root, _docs = machine
    system_docs = root / "root" / ".config" / "config-saver" / "configs.d"
    system_docs.mkdir(parents=True)
    (system_docs / "root.yaml").write_text("directories: []\n")

    assert _capture(root) == []

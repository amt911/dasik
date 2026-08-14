"""`sync` extracts captured /etc files into the tree instead of inlining them.

The other half of the writeback. Without it a capture undoes the split from the
other direction: every PAM snippet and udev rule comes back as an escaped
one-line string in the JSON, which is exactly what the tree exists to avoid.
"""
from pathlib import Path

from dasik.lib.json_parser.etc_tree import extract_to_etc_tree


def test_without_a_tree_nothing_is_extracted(tmp_path):
    config = {"files": [{"path": "/etc/pam.d/sudo", "content": "x\n"}]}

    result = extract_to_etc_tree(config, tmp_path)

    assert result.config == config
    assert result.writes == {}


def test_a_captured_etc_file_leaves_the_json(tmp_path):
    (tmp_path / "etc").mkdir()
    config = {"etc_tree": "etc",
              "files": [{"path": "/etc/pam.d/sudo", "content": "auth required\n"}]}

    result = extract_to_etc_tree(config, tmp_path)

    assert result.config["files"] == []
    assert result.writes == {tmp_path / "etc" / "pam.d" / "sudo": "auth required\n"}


def test_a_path_outside_etc_stays_inline(tmp_path):
    (tmp_path / "etc").mkdir()
    config = {"etc_tree": "etc",
              "files": [{"path": "/var/lib/x.conf", "content": "x\n"}]}

    result = extract_to_etc_tree(config, tmp_path)

    assert result.config["files"] == [{"path": "/var/lib/x.conf", "content": "x\n"}]
    assert result.writes == {}


def test_an_executable_mode_becomes_a_chmod_not_a_declaration(tmp_path):
    (tmp_path / "etc").mkdir()
    config = {"etc_tree": "etc",
              "files": [{"path": "/etc/profile.d/d.sh", "content": "x\n",
                         "mode": "0755"}]}

    result = extract_to_etc_tree(config, tmp_path)

    assert result.modes == {tmp_path / "etc" / "profile.d" / "d.sh": 0o755}
    assert "etc_tree_modes" not in result.config


def test_any_other_mode_is_declared_because_git_cannot_carry_it(tmp_path):
    (tmp_path / "etc").mkdir()
    config = {"etc_tree": "etc",
              "files": [{"path": "/etc/wireguard/wg0.conf",
                         "content": "[Interface]\n", "mode": "0600"}]}

    result = extract_to_etc_tree(config, tmp_path)

    assert result.config["etc_tree_modes"] == {"wireguard/wg0.conf": "0600"}
    assert result.modes == {tmp_path / "etc" / "wireguard" / "wg0.conf": 0o600}


def test_a_stale_mode_declaration_is_dropped(tmp_path):
    """The capture is the truth: a mode for a file the machine no longer has
    would fail the load next time (`etc_tree_modes` names an absent file)."""
    (tmp_path / "etc").mkdir()
    config = {"etc_tree": "etc",
              "etc_tree_modes": {"gone.conf": "0600"},
              "files": [{"path": "/etc/pam.d/sudo", "content": "x\n"}]}

    result = extract_to_etc_tree(config, tmp_path)

    assert "gone.conf" not in result.config.get("etc_tree_modes", {})


def test_a_file_the_machine_dropped_is_removed_from_the_tree(tmp_path):
    """The tree is a declaration, not a pile: a file nobody captured goes."""
    (tmp_path / "etc" / "pam.d").mkdir(parents=True)
    stale = tmp_path / "etc" / "pam.d" / "gone"
    stale.write_text("old\n")
    config = {"etc_tree": "etc",
              "files": [{"path": "/etc/pam.d/sudo", "content": "x\n"}]}

    result = extract_to_etc_tree(config, tmp_path)

    assert stale in result.deletions

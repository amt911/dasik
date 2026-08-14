"""`etc_tree`: a directory that mirrors /etc, instead of bodies in the JSON.

`$include_text` already moves one body out of the config. A real machine's /etc
is not one body, and the sections (`udev_rules`, `sysctl_d`, …) do not cover
`/etc/pam.d/sudo` at all — that has to be a `files` entry. The tree turns the
whole thing into a directory that reads like the /etc it produces.

Expanded in the loader, because only the loader knows where the config file is:
after it, every action sees ordinary `files` entries.
"""
import pytest

from dasik.lib.json_parser.etc_tree import ConfigTreeError, expand_etc_tree


def _tree(tmp_path, files):
    for name, content in files.items():
        path = tmp_path / "etc" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return tmp_path


def _by_path(config):
    return {entry["path"]: entry for entry in config["files"]}


def test_no_etc_tree_key_changes_nothing(tmp_path):
    config = {"files": [{"path": "/etc/hostname", "content": "torre\n"}]}
    assert expand_etc_tree(config, tmp_path) == config


def test_every_file_becomes_a_files_entry_under_etc(tmp_path):
    _tree(tmp_path, {"pam.d/sudo": "auth required pam_unix.so\n",
                     "profile.d/dasik.sh": "export EDITOR=vim\n"})

    files = _by_path(expand_etc_tree({"etc_tree": "etc"}, tmp_path))

    assert set(files) == {"/etc/pam.d/sudo", "/etc/profile.d/dasik.sh"}
    assert files["/etc/pam.d/sudo"]["content"] == "auth required pam_unix.so\n"


def test_an_executable_file_gets_0755(tmp_path):
    _tree(tmp_path, {"profile.d/dasik.sh": "export EDITOR=vim\n",
                     "pam.d/sudo": "auth required pam_unix.so\n"})
    (tmp_path / "etc" / "profile.d" / "dasik.sh").chmod(0o755)

    files = _by_path(expand_etc_tree({"etc_tree": "etc"}, tmp_path))

    assert files["/etc/profile.d/dasik.sh"]["mode"] == "0755"
    assert "mode" not in files["/etc/pam.d/sudo"]


def test_a_declared_mode_wins_over_the_executable_bit(tmp_path):
    _tree(tmp_path, {"wireguard/wg0.conf": "[Interface]\n"})

    files = _by_path(expand_etc_tree(
        {"etc_tree": "etc", "etc_tree_modes": {"wireguard/wg0.conf": "0600"}},
        tmp_path))

    assert files["/etc/wireguard/wg0.conf"]["mode"] == "0600"


def test_an_explicit_files_entry_wins_over_the_tree(tmp_path):
    _tree(tmp_path, {"hostname": "from-tree\n"})

    files = _by_path(expand_etc_tree(
        {"etc_tree": "etc",
         "files": [{"path": "/etc/hostname", "content": "explicit\n"}]},
        tmp_path))

    assert files["/etc/hostname"]["content"] == "explicit\n"
    assert len(files) == 1


def test_an_empty_directory_is_ignored(tmp_path):
    _tree(tmp_path, {"pam.d/sudo": "x\n"})
    (tmp_path / "etc" / "empty").mkdir()

    assert len(expand_etc_tree({"etc_tree": "etc"}, tmp_path)["files"]) == 1


# --- refusals, all at load time --------------------------------------------- #

def test_a_symlink_in_the_tree_is_refused(tmp_path):
    _tree(tmp_path, {"pam.d/sudo": "x\n"})
    (tmp_path / "secret").write_text("not yours\n")
    (tmp_path / "etc" / "leak").symlink_to(tmp_path / "secret")

    with pytest.raises(ConfigTreeError, match="symlink"):
        expand_etc_tree({"etc_tree": "etc"}, tmp_path)


def test_a_binary_file_is_refused_by_name(tmp_path):
    _tree(tmp_path, {"pam.d/sudo": "x\n"})
    (tmp_path / "etc" / "blob.bin").write_bytes(b"\xff\xfe\x00")

    with pytest.raises(ConfigTreeError, match="blob.bin"):
        expand_etc_tree({"etc_tree": "etc"}, tmp_path)


def test_a_tree_outside_the_config_directory_is_refused(tmp_path):
    (tmp_path / "sub").mkdir()

    with pytest.raises(ConfigTreeError, match=r"\.\."):
        expand_etc_tree({"etc_tree": "../etc"}, tmp_path / "sub")


def test_a_missing_tree_is_refused(tmp_path):
    with pytest.raises(ConfigTreeError, match="etc"):
        expand_etc_tree({"etc_tree": "etc"}, tmp_path)


def test_a_mode_for_a_file_the_tree_does_not_hold_is_refused(tmp_path):
    """A mode nobody applies is a typo — and this one guards a secret."""
    _tree(tmp_path, {"pam.d/sudo": "x\n"})

    with pytest.raises(ConfigTreeError, match="wireguard/wg0.conf"):
        expand_etc_tree(
            {"etc_tree": "etc", "etc_tree_modes": {"wireguard/wg0.conf": "0600"}},
            tmp_path)

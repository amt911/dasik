"""The loader reads each tunnel's file, because only it knows where that is.

Same reason `etc_tree` expands in the loader: a path relative to the config is
meaningless anywhere else. The refusals matter more than the happy path — a
tunnel file holds a private key, so a symlink is not followed and a missing
file is named rather than silently skipped.
"""
import pytest

from dasik.lib.json_parser.etc_tree import ConfigTreeError
from dasik.lib.json_parser.wireguard_source import expand_wireguard_sources

WG = "[Interface]\nAddress = 10.0.0.2/24\nPrivateKey = SECRET\n"


def _cfg(**kw):
    tunnel = {"name": "eu-mad", "source": "wg/eu-mad.conf"}
    tunnel.update(kw)
    return {"hostname": "box", "wireguard": [tunnel]}


def _place(tmp_path, text=WG, rel="wg/eu-mad.conf"):
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_content_is_read_from_the_file(tmp_path):
    _place(tmp_path)
    out = expand_wireguard_sources(_cfg(), tmp_path)
    assert out["wireguard"][0]["content"] == WG


def test_the_declaration_is_not_mutated(tmp_path):
    _place(tmp_path)
    config = _cfg()
    expand_wireguard_sources(config, tmp_path)
    assert "content" not in config["wireguard"][0]


def test_a_missing_file_names_the_tunnel_and_the_path(tmp_path):
    with pytest.raises(ConfigTreeError) as e:
        expand_wireguard_sources(_cfg(), tmp_path)
    assert "eu-mad" in str(e.value) and "wg/eu-mad.conf" in str(e.value)


def test_a_symlink_is_refused(tmp_path):
    (tmp_path / "wg").mkdir()
    real = tmp_path / "secret"
    real.write_text(WG)
    (tmp_path / "wg" / "eu-mad.conf").symlink_to(real)
    with pytest.raises(ConfigTreeError):
        expand_wireguard_sources(_cfg(), tmp_path)


def test_a_binary_file_is_refused(tmp_path):
    (tmp_path / "wg").mkdir()
    (tmp_path / "wg" / "eu-mad.conf").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ConfigTreeError):
        expand_wireguard_sources(_cfg(), tmp_path)


def test_a_tunnel_without_a_source_is_refused(tmp_path):
    config = {"hostname": "box", "wireguard": [{"name": "eu-mad"}]}
    with pytest.raises(ConfigTreeError):
        expand_wireguard_sources(config, tmp_path)


def test_two_tunnels_are_both_read(tmp_path):
    _place(tmp_path)
    _place(tmp_path, "[connection]\ntype=wireguard\n", "wg/work.nmconnection")
    config = {"hostname": "box", "wireguard": [
        {"name": "eu-mad", "source": "wg/eu-mad.conf"},
        {"name": "work", "source": "wg/work.nmconnection"}]}
    out = expand_wireguard_sources(config, tmp_path)
    assert [t["content"] for t in out["wireguard"]] == [
        WG, "[connection]\ntype=wireguard\n"]


def test_content_already_present_is_left_alone(tmp_path):
    # The capture path hands the config back with bodies in place; re-reading
    # would need the files to exist on a machine that only just captured them.
    out = expand_wireguard_sources(_cfg(content="already"), tmp_path)
    assert out["wireguard"][0]["content"] == "already"


def test_no_block_is_a_no_op(tmp_path):
    config = {"hostname": "box"}
    assert expand_wireguard_sources(config, tmp_path) == config

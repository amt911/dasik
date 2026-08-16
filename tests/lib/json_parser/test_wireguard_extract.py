"""The capture writes the tunnel file next to the config, not into the JSON.

The mirror of `wireguard_source`, and the same reason `extract_to_etc_tree`
exists: a capture must not undo the split from the other side. Inline in JSON a
tunnel is an escaped one-liner holding a private key — unreviewable in a diff,
and impossible to keep at 0600 once it is a JSON string.
"""
from dasik.lib.json_parser.wireguard_extract import extract_to_wireguard_dir

WGQ = "[Interface]\nPrivateKey = SECRET\n"
NMC = "[connection]\nid=work\ntype=wireguard\n"


def _captured(**kw):
    tunnel = {"name": "eu-mad", "source": "wg/eu-mad.conf", "backend": "wg-quick",
              "enable": True, "content": WGQ}
    tunnel.update(kw)
    return {"hostname": "box", "wireguard": [tunnel]}


def test_the_body_leaves_the_json_and_becomes_a_file(tmp_path):
    out = extract_to_wireguard_dir(_captured(), tmp_path)
    assert "content" not in out.config["wireguard"][0]
    assert out.writes == {tmp_path / "wg" / "eu-mad.conf": WGQ}


def test_the_file_is_written_at_0600(tmp_path):
    out = extract_to_wireguard_dir(_captured(), tmp_path)
    assert out.modes == {tmp_path / "wg" / "eu-mad.conf": 0o600}


def test_the_declaration_that_stays_still_names_the_file(tmp_path):
    out = extract_to_wireguard_dir(_captured(), tmp_path)
    assert out.config["wireguard"][0]["source"] == "wg/eu-mad.conf"


def test_a_declared_source_decides_where_it_lands(tmp_path):
    out = extract_to_wireguard_dir(_captured(source="tunnels/mad.conf"), tmp_path)
    assert list(out.writes) == [tmp_path / "tunnels" / "mad.conf"]


def test_an_nm_tunnel_without_a_source_defaults_to_its_own_suffix(tmp_path):
    captured = {"wireguard": [{"name": "work", "backend": "networkmanager",
                               "enable": True, "content": NMC}]}
    out = extract_to_wireguard_dir(captured, tmp_path)
    assert list(out.writes) == [tmp_path / "wg" / "work.nmconnection"]
    assert out.config["wireguard"][0]["source"] == "wg/work.nmconnection"


def test_a_tunnel_the_machine_no_longer_has_is_deleted(tmp_path):
    (tmp_path / "wg").mkdir()
    (tmp_path / "wg" / "old.conf").write_text(WGQ)
    out = extract_to_wireguard_dir(_captured(), tmp_path)
    assert (tmp_path / "wg" / "old.conf") in out.deletions


def test_a_tunnel_kept_outside_the_capture_dir_is_never_deleted(tmp_path):
    # `source` can point anywhere at or below the config; sweeping there would
    # be a config file deleting its neighbours.
    (tmp_path / "tunnels").mkdir()
    (tmp_path / "tunnels" / "other.conf").write_text(WGQ)
    out = extract_to_wireguard_dir(_captured(), tmp_path)
    assert not out.deletions


def test_the_input_config_is_not_mutated(tmp_path):
    config = _captured()
    extract_to_wireguard_dir(config, tmp_path)
    assert config["wireguard"][0]["content"] == WGQ


def test_no_block_is_a_no_op(tmp_path):
    config = {"hostname": "box"}
    out = extract_to_wireguard_dir(config, tmp_path)
    assert out.config == config and not out.writes and not out.deletions

import json
from unittest.mock import patch, MagicMock

from dasik import __main__ as cli


def _write_config(tmp_path, payload):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(payload))
    return p


def _patches():
    return (
        patch("dasik.__main__.Reconciler"),
        patch("dasik.__main__.setup_actions", lambda: None),
        patch("dasik.__main__.get_default_registry"),
        patch("dasik.__main__.StateStore"),
    )


def test_sync_writes_new_config_and_backup(tmp_path, capsys):
    cfg = _write_config(tmp_path, {"packages": ["git"]})
    p_recon, _, p_reg, p_store = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store:
        Reg.return_value.get_all_actions.return_value = []
        recon = Recon.return_value
        recon.sync.return_value = ({"packages": ["git", "htop"]}, MagicMock())
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}

        rc = cli.main(["sync", str(cfg)])

    assert rc == 0
    assert json.loads(cfg.read_text()) == {"packages": ["git", "htop"]}
    bak = tmp_path / "config.json.bak"
    assert bak.exists()
    assert json.loads(bak.read_text()) == {"packages": ["git"]}
    assert "Synced" in capsys.readouterr().out


def test_sync_no_change_does_not_write_or_backup(tmp_path, capsys):
    cfg = _write_config(tmp_path, {"packages": ["git"]})
    p_recon, _, p_reg, p_store = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store:
        Reg.return_value.get_all_actions.return_value = []
        recon = Recon.return_value
        recon.sync.return_value = ({"packages": ["git"]}, MagicMock())
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}

        rc = cli.main(["sync", str(cfg)])

    assert rc == 0
    assert not (tmp_path / "config.json.bak").exists()
    assert "already matches" in capsys.readouterr().out.lower()


def test_sync_no_v3_actions_writes_nothing(tmp_path, capsys):
    cfg = _write_config(tmp_path, {"packages": ["git"]})
    p_recon, _, p_reg, p_store = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store:
        Reg.return_value.get_all_actions.return_value = []
        recon = Recon.return_value
        recon.sync.return_value = ({"packages": ["git"]}, None)
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}

        rc = cli.main(["sync", str(cfg)])

    assert rc == 0
    assert not (tmp_path / "config.json.bak").exists()
    assert json.loads(cfg.read_text()) == {"packages": ["git"]}  # untouched


def test_sync_default_target_is_root(tmp_path):
    cfg = _write_config(tmp_path, {"packages": []})
    p_recon, _, p_reg, p_store = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store:
        Reg.return_value.get_all_actions.return_value = []
        Recon.return_value.sync.return_value = ({"packages": []}, None)
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}
        cli.main(["sync", str(cfg)])

    assert Recon.call_args.kwargs["target"].root == "/"


def test_sync_explicit_target_mnt(tmp_path):
    cfg = _write_config(tmp_path, {"packages": []})
    p_recon, _, p_reg, p_store = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store:
        Reg.return_value.get_all_actions.return_value = []
        Recon.return_value.sync.return_value = ({"packages": []}, None)
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}
        cli.main(["sync", str(cfg), "--target", "/mnt"])

    assert Recon.call_args.kwargs["target"].root == "/mnt"
    assert Store.call_args.args[0].root == "/mnt"


def test_sync_missing_config_exits_nonzero(tmp_path, capsys):
    missing = tmp_path / "nope.json"
    rc = cli.main(["sync", str(missing)])
    assert rc != 0
    assert "does not exist" in capsys.readouterr().err


def test_sync_bootstrap_empty_domain_is_noop(tmp_path, capsys):
    """A config that omits a domain + nothing captured (empty) is no change:
    sync must not rewrite the file just to add an empty 'packages': []."""
    cfg = _write_config(tmp_path, {"metadata": {"name": "fresh"}})
    p_recon, _, p_reg, p_store = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store:
        Reg.return_value.get_all_actions.return_value = []
        recon = Recon.return_value
        recon.sync.return_value = (
            {"metadata": {"name": "fresh"}, "packages": []},
            MagicMock(),
        )
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}

        rc = cli.main(["sync", str(cfg)])

    assert rc == 0
    assert not (tmp_path / "config.json.bak").exists()
    assert json.loads(cfg.read_text()) == {"metadata": {"name": "fresh"}}  # untouched
    assert "already matches" in capsys.readouterr().out.lower()

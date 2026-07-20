"""plan/apply/sync must validate before they can reach a mutating action (F-15/F-16).

Before this, only the separate `dasik check` verb ran pydantic; `dasik apply`
went json.loads → expand_config → actions, so a config that pydantic would
reject — or one whose fields are individually valid but mutually incoherent
(user in a group no package creates) — reached the disk actions and failed only
after the target was already partitioned.
"""
import json
from unittest.mock import MagicMock, patch

from dasik import __main__ as cli


def _write(tmp_path, payload):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(payload))
    return p


def _patches():
    return (
        patch("dasik.__main__.Reconciler"),
        patch("dasik.__main__.setup_actions", lambda: None),
        patch("dasik.__main__.get_default_registry"),
        patch("dasik.__main__.StateStore"),
        patch("dasik.__main__.GenerationStore"),
    )


_SCHEMA_INVALID = {"users": [{"username": "andres", "hashed_password": "plaintext"}]}
_GROUP_INCOHERENT = {
    "packages": ["podman-docker"],
    "users": [{"username": "andres", "hashed_password": "$6$x$y",
               "groups": ["docker"]}],
}


def test_apply_rejects_schema_invalid_config_before_building_actions(tmp_path, capsys):
    cfg = _write(tmp_path, _SCHEMA_INVALID)
    p_recon, p_setup, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_setup, p_reg, p_store, p_gen:
        rc = cli.main(["apply", str(cfg), "--yes"])
    assert rc == 1
    Recon.assert_not_called()
    assert "hashed_password" in capsys.readouterr().err


def test_plan_rejects_schema_invalid_config(tmp_path):
    cfg = _write(tmp_path, _SCHEMA_INVALID)
    p_recon, p_setup, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_setup, p_reg, p_store, p_gen:
        rc = cli.main(["plan", str(cfg)])
    assert rc == 1
    Recon.assert_not_called()


def test_apply_rejects_group_without_provider_before_mutating(tmp_path, capsys):
    cfg = _write(tmp_path, _GROUP_INCOHERENT)
    p_recon, p_setup, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_setup, p_reg, p_store, p_gen:
        rc = cli.main(["apply", str(cfg), "--yes"])
    assert rc == 1
    Recon.assert_not_called()
    err = capsys.readouterr().err
    assert "docker" in err and "group_without_provider" in err


def test_plan_reports_preflight_errors(tmp_path, capsys):
    cfg = _write(tmp_path, _GROUP_INCOHERENT)
    p_recon, p_setup, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_setup, p_reg, p_store, p_gen:
        rc = cli.main(["plan", str(cfg)])
    assert rc == 1
    Recon.assert_not_called()


def test_warnings_do_not_block_plan(tmp_path, capsys):
    cfg = _write(tmp_path, {"users": [{"username": "a", "hashed_password": "$6$x$y",
                                       "groups": ["somecustomgroup"]}]})
    p_recon, p_setup, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_setup, p_reg as Reg, p_store, p_gen:
        Reg.return_value.get_all_actions.return_value = []
        Recon.return_value.build_plan.return_value = (MagicMock(), [])
        rc = cli.main(["plan", str(cfg)])
    assert rc == 0
    Recon.assert_called_once()
    assert "unknown_group" in capsys.readouterr().out


def test_check_reports_preflight_errors(tmp_path, capsys):
    cfg = _write(tmp_path, _GROUP_INCOHERENT)
    rc = cli.main(["check", str(cfg)])
    assert rc == 1
    assert "group_without_provider" in capsys.readouterr().err


def test_check_accepts_coherent_config(tmp_path, capsys):
    cfg = _write(tmp_path, {"packages": ["docker"],
                            "users": [{"username": "a", "hashed_password": "$6$x$y",
                                       "groups": ["docker", "wheel"]}]})
    assert cli.main(["check", str(cfg)]) == 0


def test_sync_rejects_schema_invalid_config(tmp_path):
    cfg = _write(tmp_path, _SCHEMA_INVALID)
    p_recon, p_setup, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_setup, p_reg, p_store, p_gen:
        rc = cli.main(["sync", str(cfg)])
    assert rc == 1
    Recon.assert_not_called()

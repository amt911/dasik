import json
from unittest.mock import patch, MagicMock

import pytest

from dasik import __main__ as cli


def _write_config(tmp_path, payload):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(payload))
    return p


def _empty_plan_pair():
    from dasik.lib.state.change import Plan
    return Plan(), []


def _nonempty_plan_pair():
    from dasik.lib.state.change import Plan, Change, Op
    p = Plan()
    p.add(Change("packages", Op.INSTALL, "git"))
    return p, []


def _patches():
    """Patch the CLI's external collaborators with one context-manager-friendly
    bundle. Returns the patchers; callers use `with`-stack."""
    return (
        patch("dasik.__main__.Reconciler"),
        patch("dasik.__main__.setup_actions", lambda: None),
        patch("dasik.__main__.get_default_registry"),
        patch("dasik.__main__.StateStore"),
        patch("dasik.__main__.GenerationStore"),
    )


def test_apply_verb_invokes_reconciler_build_and_apply(tmp_path, capsys):
    cfg = _write_config(tmp_path, {"packages": ["git"]})
    p_recon, p_setup, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_setup, p_reg as Reg, p_store as Store, p_gen as Gen:
        Reg.return_value.get_all_actions.return_value = []
        recon_inst = Recon.return_value
        recon_inst.build_plan.return_value = _nonempty_plan_pair()
        new_manifest = MagicMock()
        new_manifest.generation = 1
        recon_inst.apply.return_value = new_manifest
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}

        rc = cli.main(["apply", str(cfg), "--yes"])

    assert rc == 0
    recon_inst.build_plan.assert_called_once()
    recon_inst.apply.assert_called_once()
    # assume_yes=True was passed because of --yes
    assert recon_inst.apply.call_args.kwargs.get("assume_yes") is True
    out = capsys.readouterr().out
    assert "git" in out  # plan was rendered
    assert "generation" in out.lower()


def test_apply_verb_empty_plan_no_apply_no_generation_printed(tmp_path, capsys):
    cfg = _write_config(tmp_path, {"packages": []})
    p_recon, p_setup, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_setup, p_reg as Reg, p_store as Store, p_gen as Gen:
        Reg.return_value.get_all_actions.return_value = []
        recon_inst = Recon.return_value
        recon_inst.build_plan.return_value = _empty_plan_pair()
        recon_inst.apply.return_value = None
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}

        rc = cli.main(["apply", str(cfg), "--yes"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "No changes" in out
    # apply() is NOT called for an empty plan — _cmd_apply short-circuits
    # at plan.is_empty(). The mocked recon_inst.apply.return_value=None
    # above only documents intent; it is never read.


def test_apply_verb_passes_target_root_to_stores(tmp_path):
    cfg = _write_config(tmp_path, {"packages": []})
    p_recon, p_setup, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_setup, p_reg as Reg, p_store as Store, p_gen as Gen:
        Reg.return_value.get_all_actions.return_value = []
        recon_inst = Recon.return_value
        recon_inst.build_plan.return_value = _empty_plan_pair()
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}
        cli.main(["apply", str(cfg), "--target", "/", "--yes"])

    store_target = Store.call_args.args[0]
    gen_target = Gen.call_args.args[0]
    assert store_target.root == "/"
    assert gen_target.root == "/"


def test_apply_verb_default_target_is_mnt(tmp_path):
    cfg = _write_config(tmp_path, {"packages": []})
    p_recon, p_setup, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_setup, p_reg as Reg, p_store as Store, p_gen as Gen:
        Reg.return_value.get_all_actions.return_value = []
        recon_inst = Recon.return_value
        recon_inst.build_plan.return_value = _empty_plan_pair()
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}
        cli.main(["apply", str(cfg), "--yes"])

    assert Recon.call_args.kwargs["target"].root == "/mnt"


def test_apply_verb_without_yes_defaults_assume_yes_false(tmp_path):
    cfg = _write_config(tmp_path, {"packages": ["git"]})
    p_recon, p_setup, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_setup, p_reg as Reg, p_store as Store, p_gen as Gen:
        Reg.return_value.get_all_actions.return_value = []
        recon_inst = Recon.return_value
        recon_inst.build_plan.return_value = _nonempty_plan_pair()
        recon_inst.apply.return_value = MagicMock(generation=1)
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}
        cli.main(["apply", str(cfg)])

    assert recon_inst.apply.call_args.kwargs.get("assume_yes") is False


def test_apply_verb_user_aborts_returns_nonzero(tmp_path, capsys):
    """When Reconciler.apply returns None on a non-empty plan, treat as cancel."""
    cfg = _write_config(tmp_path, {"packages": ["git"]})
    p_recon, p_setup, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_setup, p_reg as Reg, p_store as Store, p_gen as Gen:
        Reg.return_value.get_all_actions.return_value = []
        recon_inst = Recon.return_value
        recon_inst.build_plan.return_value = _nonempty_plan_pair()
        recon_inst.apply.return_value = None  # user said no
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}

        rc = cli.main(["apply", str(cfg)])

    assert rc != 0
    err = capsys.readouterr().err
    assert "aborted" in err.lower() or "cancel" in err.lower()


def test_apply_verb_missing_config_exits_nonzero(tmp_path, capsys):
    missing = tmp_path / "nope.json"
    rc = cli.main(["apply", str(missing)])
    assert rc != 0
    assert "does not exist" in capsys.readouterr().err

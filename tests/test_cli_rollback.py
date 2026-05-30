from unittest.mock import patch, MagicMock

from dasik import __main__ as cli
from dasik.lib.state.change import Plan, Change, Op
from dasik.lib.state.generation_store import GenInfo


def _nonempty_plan_pair():
    p = Plan()
    p.add(Change("packages", Op.INSTALL, "git"))
    return p, []


def _empty_plan_pair():
    return Plan(), []


def _patches():
    return (
        patch("dasik.__main__.Reconciler"),
        patch("dasik.__main__.setup_actions", lambda: None),
        patch("dasik.__main__.get_default_registry"),
        patch("dasik.__main__.StateStore"),
        patch("dasik.__main__.GenerationStore"),
    )


def test_rollback_restores_given_generation_and_applies(capsys):
    p_recon, _, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store, p_gen as Gen:
        Reg.return_value.get_all_actions.return_value = []
        Gen.return_value.restore.return_value = ({"packages": ["git"]}, {"managed": {}})
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {"packages": []}}
        recon = Recon.return_value
        recon.build_plan.return_value = _nonempty_plan_pair()
        recon.apply.return_value = MagicMock(generation=5)

        rc = cli.main(["rollback", "2", "--yes"])

    assert rc == 0
    Gen.return_value.restore.assert_called_once_with(2)
    # Desired state for apply is the restored config.
    assert Recon.call_args.kwargs["config"] == {"packages": ["git"]}
    recon.apply.assert_called_once()
    assert recon.apply.call_args.kwargs.get("assume_yes") is True
    assert "generation 5" in capsys.readouterr().out


def test_rollback_default_n_uses_previous_generation(capsys):
    p_recon, _, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store, p_gen as Gen:
        Reg.return_value.get_all_actions.return_value = []
        Gen.return_value.list.return_value = [
            GenInfo(number=1, is_current=False),
            GenInfo(number=2, is_current=False),
            GenInfo(number=3, is_current=True),
        ]
        Gen.return_value.restore.return_value = ({"packages": []}, {"managed": {}})
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}
        recon = Recon.return_value
        recon.build_plan.return_value = _empty_plan_pair()

        rc = cli.main(["rollback", "--yes"])

    assert rc == 0
    Gen.return_value.restore.assert_called_once_with(2)  # current(3) - 1


def test_rollback_no_previous_generation_errors(capsys):
    p_recon, _, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store, p_gen as Gen:
        Reg.return_value.get_all_actions.return_value = []
        Gen.return_value.list.return_value = [GenInfo(number=1, is_current=True)]

        rc = cli.main(["rollback"])

    assert rc != 0
    assert "roll back" in capsys.readouterr().err.lower()


def test_rollback_default_n_with_no_current_marker_errors(capsys):
    """_previous_generation returns None when no generation is marked current."""
    p_recon, _, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store, p_gen as Gen:
        Reg.return_value.get_all_actions.return_value = []
        Gen.return_value.list.return_value = [
            GenInfo(number=1, is_current=False),
            GenInfo(number=2, is_current=False),
        ]

        rc = cli.main(["rollback"])

    assert rc != 0
    assert "roll back" in capsys.readouterr().err.lower()
    Gen.return_value.restore.assert_not_called()


def test_rollback_missing_generation_errors(capsys):
    p_recon, _, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store, p_gen as Gen:
        Reg.return_value.get_all_actions.return_value = []
        Gen.return_value.restore.side_effect = FileNotFoundError("Generation 9 not found")

        rc = cli.main(["rollback", "9"])

    assert rc != 0
    assert "not found" in capsys.readouterr().err.lower()


def test_rollback_empty_plan_reports_and_exits_zero(capsys):
    p_recon, _, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store, p_gen as Gen:
        Reg.return_value.get_all_actions.return_value = []
        Gen.return_value.restore.return_value = ({"packages": []}, {"managed": {}})
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}
        recon = Recon.return_value
        recon.build_plan.return_value = _empty_plan_pair()

        rc = cli.main(["rollback", "1", "--yes"])

    assert rc == 0
    recon.apply.assert_not_called()


def test_rollback_user_abort_returns_nonzero(capsys):
    p_recon, _, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store, p_gen as Gen:
        Reg.return_value.get_all_actions.return_value = []
        Gen.return_value.restore.return_value = ({"packages": ["git"]}, {"managed": {}})
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}
        recon = Recon.return_value
        recon.build_plan.return_value = _nonempty_plan_pair()
        recon.apply.return_value = None  # user said no

        rc = cli.main(["rollback", "2"])

    assert rc != 0
    assert "aborted" in capsys.readouterr().err.lower()


def test_rollback_default_target_is_root():
    p_recon, _, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store, p_gen as Gen:
        Reg.return_value.get_all_actions.return_value = []
        Gen.return_value.restore.return_value = ({"packages": []}, {"managed": {}})
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}
        Recon.return_value.build_plan.return_value = _empty_plan_pair()
        cli.main(["rollback", "1"])

    assert Gen.call_args.args[0].root == "/"
    assert Recon.call_args.kwargs["target"].root == "/"

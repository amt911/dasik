import json
import sys
from unittest.mock import patch, MagicMock

import pytest

from dasik import __main__ as cli


def _write_config(tmp_path, payload):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(payload))
    return p


def _empty_plan():
    from dasik.lib.state.change import Plan
    return Plan(), []


def test_plan_verb_invokes_reconciler_and_prints_no_changes(tmp_path, capsys):
    cfg = _write_config(tmp_path, {"packages": []})
    fake_reconciler = MagicMock()
    fake_reconciler.return_value.build_plan.return_value = _empty_plan()

    with patch("dasik.__main__.Reconciler", fake_reconciler), \
         patch("dasik.__main__.setup_actions", lambda: None), \
         patch("dasik.__main__.get_default_registry") as reg:
        reg.return_value.get_all_actions.return_value = []
        rc = cli.main(["plan", str(cfg)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "No changes" in out


def test_plan_verb_renders_changes(tmp_path, capsys):
    from dasik.lib.state.change import Plan, Change, Op
    p = Plan()
    p.add(Change("packages", Op.INSTALL, "git"))

    cfg = _write_config(tmp_path, {"packages": ["git"]})
    fake_reconciler = MagicMock()
    fake_reconciler.return_value.build_plan.return_value = (p, [])

    with patch("dasik.__main__.Reconciler", fake_reconciler), \
         patch("dasik.__main__.setup_actions", lambda: None), \
         patch("dasik.__main__.get_default_registry") as reg:
        reg.return_value.get_all_actions.return_value = []
        rc = cli.main(["plan", str(cfg)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "git" in out
    assert "+" in out  # INSTALL renders with "+"


def test_plan_verb_passes_target_flag_through(tmp_path):
    cfg = _write_config(tmp_path, {"packages": []})
    fake_reconciler = MagicMock()
    fake_reconciler.return_value.build_plan.return_value = _empty_plan()

    with patch("dasik.__main__.Reconciler", fake_reconciler), \
         patch("dasik.__main__.setup_actions", lambda: None), \
         patch("dasik.__main__.get_default_registry") as reg:
        reg.return_value.get_all_actions.return_value = []
        rc = cli.main(["plan", str(cfg), "--target", "/"])

    assert rc == 0
    target_passed = fake_reconciler.call_args.kwargs["target"]
    assert target_passed.root == "/"


def test_plan_verb_default_target_is_mnt(tmp_path):
    cfg = _write_config(tmp_path, {"packages": []})
    fake_reconciler = MagicMock()
    fake_reconciler.return_value.build_plan.return_value = _empty_plan()

    with patch("dasik.__main__.Reconciler", fake_reconciler), \
         patch("dasik.__main__.setup_actions", lambda: None), \
         patch("dasik.__main__.get_default_registry") as reg:
        reg.return_value.get_all_actions.return_value = []
        cli.main(["plan", str(cfg)])

    assert fake_reconciler.call_args.kwargs["target"].root == "/mnt"


def test_plan_verb_missing_config_exits_nonzero(tmp_path, capsys):
    missing = tmp_path / "nope.json"
    rc = cli.main(["plan", str(missing)])
    assert rc != 0
    assert "does not exist" in capsys.readouterr().err


def test_no_verb_form_still_works_with_deprecation_warning(tmp_path, capsys):
    cfg = _write_config(tmp_path, {"packages": []})
    with patch("dasik.__main__.ActionsHandler") as handler:
        rc = cli.main([str(cfg)])
    assert rc == 0
    err = capsys.readouterr().err
    assert "deprecated" in err.lower()
    handler.assert_called_once_with(str(cfg))

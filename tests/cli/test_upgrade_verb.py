"""Journey tests for `dasik upgrade`: main([...]) against a tmp_path fake root.

`Command.execute` is mocked and records every `su` call, so nothing real runs;
the uv and claude installs are laid out on disk the way the real tools leave
them, which is what the steps' `actual()` reads.
"""
import json
import shutil
from unittest.mock import MagicMock, patch

import pytest

from dasik.__main__ import main
from dasik.lib.state.generation_store import GenerationStore
from dasik.lib.target.target import Target
from tests.lib.actions.test_ai_skills_plan import (_install_claude_plugin,
                                                   _passwd as _ai_passwd)
from tests.lib.actions.test_uv_tools_action import _install

MKT = {"name": "caveman", "source": "JuliusBrussee/caveman"}
PLUGIN = {"name": "superpowers", "method": "claude-plugin", "marketplace": MKT}


@pytest.fixture(autouse=True)
def _as_root(monkeypatch):
    """`upgrade` refuses to run unprivileged; these journeys are root's."""
    monkeypatch.setattr("dasik.__main__.os.geteuid", lambda: 0)


def _cfg(**blocks):
    cfg = {"users": [{"username": "andres",
                       "hashed_password": "$6$x$y"}]}
    cfg.update(blocks)
    return cfg


def _full_cfg():
    return _cfg(uv_tools={"tools": ["graphifyy"]},
                ai_skills={"entries": [PLUGIN]})


def _root(tmp_path, cfg):
    _ai_passwd(tmp_path)
    _install(tmp_path)
    _install_claude_plugin(tmp_path)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg))
    return path


def _upgrade(tmp_path, path, fail_on=None, verb="upgrade"):
    """Run the verb; return (rc, scripts of every `su` call)."""
    scripts = []

    def run(cmd, args=None, *a, **k):
        if cmd == "su":
            script = args[args.index("-c") + 1]   # su - USER -c SCRIPT -- sh ARGS
            scripts.append(script)
            rc = 1 if fail_on and fail_on in " ".join(map(str, args)) else 0
            return MagicMock(stdout=b"", stderr=b"boom\n", returncode=rc)
        return MagicMock(stdout=b"", stderr=b"", returncode=0)

    with patch("dasik.lib.command_worker.command_worker.Command.execute",
               side_effect=run):
        rc = main([verb, str(path), "--target", str(tmp_path), "--no-log"])
    return rc, scripts


def test_upgrade_runs_uv_before_ai_skills(tmp_path):
    path = _root(tmp_path, _full_cfg())
    rc, scripts = _upgrade(tmp_path, path)
    assert rc == 0
    assert scripts == ['uv tool install --upgrade "$1"',
                       'claude plugin marketplace update "$1"',
                       'claude plugin update "$1" -s user -y']


def test_upgrade_then_plan_is_silent(tmp_path, capsys):
    path = _root(tmp_path, _full_cfg())
    _upgrade(tmp_path, path)
    capsys.readouterr()
    rc, _ = _upgrade(tmp_path, path, verb="plan")
    out = capsys.readouterr().out
    assert rc == 0
    assert "[base]" in out                      # plan really printed its diff
    assert "[uv_tools]" not in out and "[ai_skills]" not in out


def test_plan_positive_control_sees_a_missing_uv_tool(tmp_path, capsys):
    path = _root(tmp_path, _full_cfg())
    shutil.rmtree(tmp_path / "home/andres/.local/share/uv")
    rc, _ = _upgrade(tmp_path, path, verb="plan")
    assert rc == 0
    assert "[uv_tools]" in capsys.readouterr().out


def test_upgrade_writes_no_generation(tmp_path):
    path = _root(tmp_path, _full_cfg())
    _upgrade(tmp_path, path)
    assert not (tmp_path / "var/lib/dasik/generations").exists()
    assert GenerationStore(Target(root=str(tmp_path))).list() == []


def test_upgrade_without_ai_blocks(tmp_path, capsys):
    path = _root(tmp_path, _cfg())
    rc, scripts = _upgrade(tmp_path, path)
    assert rc == 0
    assert scripts == []
    assert "Nothing to upgrade." in capsys.readouterr().out


def test_upgrade_failure_exit_1(tmp_path, capsys):
    path = _root(tmp_path, _full_cfg())
    rc, _ = _upgrade(tmp_path, path, fail_on="claude plugin update")
    assert rc == 1
    assert "FAILED" in capsys.readouterr().out


def test_upgrade_notes_mcp_servers_and_rollback(tmp_path, capsys):
    cfg = _full_cfg()
    cfg["mcp_servers"] = {"users": ["andres"]}
    path = _root(tmp_path, cfg)
    _upgrade(tmp_path, path)
    out = capsys.readouterr().out
    assert "mcp_servers: nothing to upgrade" in out
    assert "dasik rollback does not revert an upgrade." in out


def test_upgrade_refuses_without_root(tmp_path, monkeypatch, capsys):
    # Unprivileged, `su - andres` asks for a password into a pipe nobody shows,
    # and the run hangs with a blank screen. Refuse before the first step.
    monkeypatch.setattr("dasik.__main__.os.geteuid", lambda: 1000)
    path = _root(tmp_path, _full_cfg())
    rc, scripts = _upgrade(tmp_path, path)
    err = capsys.readouterr().err
    assert rc == 1
    assert scripts == []
    assert "root" in err and "sudo dasik upgrade" in err


def test_upgrade_shows_each_step_live(tmp_path, capsys):
    path = _root(tmp_path, _full_cfg())
    rc, _ = _upgrade(tmp_path, path)
    lines = capsys.readouterr().out.splitlines()
    assert rc == 0
    start = lines.index("→ andres:graphifyy")
    done = next(i for i, l in enumerate(lines) if l.startswith("andres:graphifyy: "))
    nxt = lines.index("→ andres:claude-code:marketplace:caveman")
    assert start < done < nxt
    assert any(l.endswith("0 failed") for l in lines[nxt:])

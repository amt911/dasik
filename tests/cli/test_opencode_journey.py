"""The opencode agent through every CLI verb, against a tmp_path fake root.

Why a journey: unit tests prove each reader/writer; this proves the verbs agree
about them (plan -> apply -> plan silent; sync -> check -> plan silent; the block
removed -> DELETEs, cleared by apply; generations; rollback). Every command is
mocked: nothing here ran against a real system.
"""
import json
import os
from unittest.mock import MagicMock, patch

from dasik.__main__ import main
from tests.cli.test_verbs_integration import _fake_exec

PLUGIN = "pj@1.0.0"
MCP = {"name": "inkscape_mcp", "command": "uvx",
       "args": ["inkscape_mcp"], "agents": ["opencode"]}
PLUGIN_ENTRY = {"name": "pj", "method": "opencode-plugin", "source": PLUGIN}
USERS = [{"username": "andres", "hashed_password": "$6$salt$hash"}]
BLOCKS = {"mcp_servers": {"entries": [MCP]},
          "ai_skills": {"entries": [PLUGIN_ENTRY]}}


def _prepare(tmp_path):
    for d in ("etc", "usr/bin", "home/andres", "boot/grub"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/passwd").write_text(
        f"andres:x:{os.getuid()}:{os.getgid()}::/home/andres:/bin/zsh\n")
    (tmp_path / "usr/bin/opencode").write_text("")
    (tmp_path / "usr/bin/pacman").write_text("")
    (tmp_path / "boot/grub/grub.cfg").write_text("")


def _cfg(tmp_path, name="config.json", **blocks):
    p = tmp_path / name
    p.write_text(json.dumps({"users": USERS, **blocks}))
    return p


def _oc_json(tmp_path):
    return tmp_path / "home/andres/.config/opencode/opencode.json"


def _fake_opencode_cli(tmp_path):
    """`su - andres -c 'opencode plugin add|remove "$1"' -- sh <spec>` the way the
    real CLI behaves: it edits only opencode.json's `plugins` array."""
    base = _fake_exec({("pacman", "-Qqe"): b"", ("pacman", "-Qq"): b""})

    def run(cmd, args=None, *a, **k):
        script = args[3] if cmd == "su" and args and len(args) > 3 else ""
        if script.startswith("opencode plugin "):
            verb, spec = script.split()[2], args[-1]
            path = _oc_json(tmp_path)
            data = json.loads(path.read_text()) if path.exists() else {}
            plugins = [s for s in data.get("plugins", []) if s != spec]
            if verb == "add":
                plugins.append(spec)
            data["plugins"] = plugins
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data))
            return MagicMock(stdout="", stderr="", returncode=0)
        return base(cmd, args, *a, **k)
    return run


def _run(tmp_path, argv):
    fake = _fake_opencode_cli(tmp_path)
    with patch("dasik.lib.command_worker.command_worker.Command.execute",
               side_effect=fake), \
         patch("dasik.lib.actions.ai_skills_action.Command.execute",
               side_effect=fake), \
         patch("subprocess.run", side_effect=fake):
        extra = [] if argv[0] == "check" else ["--target", str(tmp_path)]
        return main(argv + extra)


def _plan_lines(tmp_path, capsys, p):
    capsys.readouterr()
    assert _run(tmp_path, ["plan", str(p)]) == 0
    return capsys.readouterr().out


def _assert_silent(out):
    assert "opencode" not in out
    for line in out.splitlines():
        assert not line.lstrip().startswith(
            ("+ [mcp_servers]", "- [mcp_servers]", "~ [mcp_servers]",
             "+ [ai_skills]", "- [ai_skills]", "~ [ai_skills]")), line


def test_opencode_journey(tmp_path, capsys):
    _prepare(tmp_path)
    p = _cfg(tmp_path, **BLOCKS)

    # plan -> apply -> plan: the first sees both, the last is silent
    out = _plan_lines(tmp_path, capsys, p)
    assert "andres:opencode:inkscape_mcp" in out
    assert f"andres:opencode:plugin:{PLUGIN}" in out
    assert _run(tmp_path, ["apply", str(p), "--yes"]) == 0
    oc = json.loads(_oc_json(tmp_path).read_text())
    assert oc["mcp"]["servers"]["inkscape_mcp"]["command"][0] == "uvx"
    assert oc["plugins"] == [PLUGIN]
    _assert_silent(_plan_lines(tmp_path, capsys, p))

    # generations lists the apply
    capsys.readouterr()
    assert _run(tmp_path, ["generations"]) == 0
    assert "Generation 1" in capsys.readouterr().out

    # sync -> check -> plan: capture reality into a config with no blocks
    q = tmp_path / "captured.json"
    q.write_text(json.dumps({"users": USERS}))
    assert _run(tmp_path, ["sync", str(q)]) == 0
    captured = json.loads(q.read_text())
    assert captured["mcp_servers"]["entries"][0]["agents"] == ["opencode"]
    assert captured["ai_skills"]["entries"][0]["source"] == PLUGIN
    # sync never captures password hashes; put the declared user back
    captured["users"] = USERS
    q.write_text(json.dumps(captured))
    assert _run(tmp_path, ["check", str(q)]) == 0
    _assert_silent(_plan_lines(tmp_path, capsys, q))

    # the block removed: plan shows both DELETEs, apply clears them
    r = _cfg(tmp_path, "removed.json")
    out = _plan_lines(tmp_path, capsys, r)
    assert "- [mcp_servers] delete andres:opencode:inkscape_mcp" in out
    assert f"- [ai_skills] delete andres:opencode:plugin:{PLUGIN}" in out
    assert _run(tmp_path, ["apply", str(r), "--yes"]) == 0
    oc = json.loads(_oc_json(tmp_path).read_text())
    assert "inkscape_mcp" not in json.dumps(oc.get("mcp", {}))
    assert oc["plugins"] == []
    _assert_silent(_plan_lines(tmp_path, capsys, r))

    # rollback restores the previous generation (the one holding the blocks)
    capsys.readouterr()
    assert _run(tmp_path, ["generations"]) == 0
    assert "Generation 2" in capsys.readouterr().out
    assert _run(tmp_path, ["rollback", "1", "--yes"]) == 0
    oc = json.loads(_oc_json(tmp_path).read_text())
    assert oc["mcp"]["servers"]["inkscape_mcp"]["command"][0] == "uvx"
    assert oc["plugins"] == [PLUGIN]
    _assert_silent(_plan_lines(tmp_path, capsys, p))

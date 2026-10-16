"""`uv_tools` upgrade steps — what `dasik upgrade` runs for each declared tool."""
from tests.lib.actions.test_uv_tools_action import _act, _install, _passwd

UPGRADE = 'uv tool install --upgrade "$1"'
UPGRADE_PY = 'uv tool install --upgrade --python "$1" "$2"'


def _cfg(*tools, users=("andres",)):
    return {"users": [{"username": u} for u in users],
            "uv_tools": {"tools": list(tools)}}


def _steps(tmp_path, *tools, installed=("graphifyy",), users=("andres",)):
    _passwd(tmp_path, users)
    for tool in installed:
        _install(tmp_path, tool)
    return _act(tmp_path, _cfg(*tools, users=users)).upgrade_steps()


def test_installed_unpinned_gets_upgrade_command(tmp_path):
    (step,) = _steps(tmp_path, "graphifyy")
    assert step.domain == "uv_tools"
    assert step.item == "andres:graphifyy"
    assert step.user == "andres"
    assert step.skip is None
    assert step.commands == ((UPGRADE, ("graphifyy",)),)


def test_declared_python_is_kept(tmp_path):
    (step,) = _steps(tmp_path, {"name": "inkscape_mcp", "python": "3.13"},
                     installed=("inkscape-mcp",))
    assert step.commands == ((UPGRADE_PY, ("3.13", "inkscape_mcp")),)


def test_exact_pin_skipped(tmp_path):
    (step,) = _steps(tmp_path, "graphifyy==0.9.53")
    assert step.skip == "pinned"
    assert step.commands == ()


def test_triple_equals_pin_skipped_even_with_extras(tmp_path):
    (step,) = _steps(tmp_path, "semgrep[all]===1.2.3", installed=("semgrep",))
    assert step.skip == "pinned"


def test_range_not_skipped(tmp_path):
    (step,) = _steps(tmp_path, "graphifyy>=0.9")
    assert step.skip is None
    assert step.commands == ((UPGRADE, ("graphifyy>=0.9",)),)


def test_missing_tool_skipped(tmp_path):
    (step,) = _steps(tmp_path, "graphifyy", installed=())
    assert step.skip == "not installed — run dasik apply"
    assert step.commands == ()


def test_steps_follow_desired_order(tmp_path):
    steps = _steps(tmp_path, "semgrep", "graphifyy",
                   installed=("semgrep", "graphifyy"))
    assert [s.item for s in steps] == ["andres:semgrep", "andres:graphifyy"]


def test_no_target_returns_nothing():
    from dasik.lib.actions.uv_tools_action import UvToolsAction
    assert UvToolsAction(_cfg("graphifyy")).upgrade_steps() == []


def test_unknown_user_does_not_raise(tmp_path):
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/passwd").write_text("root:x:0:0::/root:/bin/bash\n")
    steps = _act(tmp_path, _cfg("graphifyy", users=("ghost",))).upgrade_steps()
    assert steps and all(s.skip for s in steps)
    assert all(s.version() is None for s in steps)


def test_is_exact_pin():
    from dasik.lib.actions.uv_tools_action import UvToolsAction as U
    assert U.is_exact_pin("a==1") and U.is_exact_pin("a[x]===1")
    assert not U.is_exact_pin("a>=1") and not U.is_exact_pin("a")
    assert not U.is_exact_pin("a~=1.0") and not U.is_exact_pin("a!=1")


def _dist_info(tmp_path, tool_dir, name, user="andres"):
    d = (tmp_path / "home" / user / ".local/share/uv/tools" / tool_dir
         / "lib/python3.13/site-packages" / name)
    d.mkdir(parents=True)


def test_installed_version_normalises(tmp_path):
    _passwd(tmp_path)
    _dist_info(tmp_path, "inkscape-mcp", "Inkscape_MCP-1.2.0.dist-info")
    _dist_info(tmp_path, "semgrep", "semgrep-1.5.0.dist-info")
    _dist_info(tmp_path, "semgrep", "semgrep_extra-9.9.dist-info")
    action = _act(tmp_path, _cfg("inkscape_mcp", "semgrep[all]"))
    assert action.installed_version("andres", "inkscape-mcp") == "1.2.0"
    assert action.installed_version("andres", "semgrep") == "1.5.0"
    assert action.installed_version("andres", "nothing") is None


def test_step_version_reads_dist_info(tmp_path):
    _passwd(tmp_path)
    _install(tmp_path, "graphifyy")
    _dist_info(tmp_path, "graphifyy", "graphifyy-0.6.7.dist-info")
    (step,) = _act(tmp_path, _cfg("graphifyy")).upgrade_steps()
    assert step.version() == "0.6.7"

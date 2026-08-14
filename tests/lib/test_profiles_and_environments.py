"""`profile` and `environment`: /etc/profile.d and /etc/environment.

Issue #173 listed "profiles y environments" as a block still to define. It turns
out to name two things dasik already has — `profile_d` and `etc_environment` —
so what was missing was not code but the evidence that both directions work.
This file is that evidence: planned when absent, silent when present, removed
when dasik owned them and the config stopped declaring them, and captured back
by `sync`.

/etc/profile itself is deliberately NOT managed: pacman owns it (`filesystem`),
it is sourced by every login shell, and the supported way to add to it is a
snippet in /etc/profile.d — which is what `profile_d` writes.
"""
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.drop_files_action import DropFilesAction
from dasik.lib.target.target import Target


_SNIPPET = {"name": "dasik-editor.sh", "content": "export EDITOR=nvim\n"}
_PATH = "/etc/profile.d/dasik-editor.sh"
_ENV = "/etc/environment"


def _action(root, config):
    return DropFilesAction(config, ActionContext(target=Target(root=str(root))))


def _plan(root, config, managed=()):
    return [(c.op.name, c.item) for c in _action(root, config).plan(managed=list(managed))]


def _with_file(tmp_path, path, content):
    full = tmp_path / path.lstrip("/")
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    return tmp_path


# --- /etc/profile.d --------------------------------------------------------- #

def test_a_missing_profile_snippet_is_planned(tmp_path):
    assert _plan(tmp_path, {"profile_d": [_SNIPPET]}) == [("CREATE", _PATH)]


def test_a_profile_snippet_already_there_plans_nothing(tmp_path):
    root = _with_file(tmp_path, _PATH, _SNIPPET["content"])
    assert _plan(root, {"profile_d": [_SNIPPET]}) == []


def test_an_edited_profile_snippet_is_planned_as_drift(tmp_path):
    root = _with_file(tmp_path, _PATH, "export EDITOR=vi\n")
    assert _plan(root, {"profile_d": [_SNIPPET]}) == [("MODIFY", _PATH)]


def test_dropping_the_snippet_deletes_the_one_dasik_owns(tmp_path):
    root = _with_file(tmp_path, _PATH, _SNIPPET["content"])
    assert _plan(root, {}, managed=[_PATH]) == [("DELETE", _PATH)]


def test_a_profile_snippet_dasik_never_wrote_is_left_alone(tmp_path):
    root = _with_file(tmp_path, _PATH, "export EDITOR=vi\n")
    assert _plan(root, {}) == []


def test_sync_captures_a_local_profile_snippet(tmp_path):
    root = _with_file(tmp_path, _PATH, _SNIPPET["content"])
    action = _action(root, {})
    action._pkg_owned = lambda _p: False          # nothing owns it: it is local

    assert _SNIPPET in action.import_state([])["profile_d"]


# --- /etc/environment -------------------------------------------------------- #

def test_a_missing_environment_line_is_planned(tmp_path):
    assert _plan(tmp_path, {"etc_environment": ["EDITOR=nvim"]}) == [("CREATE", _ENV)]


def test_the_environment_file_already_written_plans_nothing(tmp_path):
    root = _with_file(tmp_path, _ENV, "EDITOR=nvim\n")
    assert _plan(root, {"etc_environment": ["EDITOR=nvim"]}) == []


def test_an_environment_line_that_drifted_is_planned(tmp_path):
    root = _with_file(tmp_path, _ENV, "EDITOR=vi\n")
    assert _plan(root, {"etc_environment": ["EDITOR=nvim"]}) == [("MODIFY", _ENV)]


def test_dropping_the_environment_lines_deletes_the_file_dasik_owns(tmp_path):
    root = _with_file(tmp_path, _ENV, "EDITOR=nvim\n")
    assert _plan(root, {}, managed=[_ENV]) == [("DELETE", _ENV)]


def test_sync_captures_the_environment_lines(tmp_path):
    root = _with_file(tmp_path, _ENV, "EDITOR=nvim\nMOZ_ENABLE_WAYLAND=1\n")

    assert _action(root, {"etc_environment": ["EDITOR=nvim"]}).import_state([])[
        "etc_environment"] == ["EDITOR=nvim", "MOZ_ENABLE_WAYLAND=1"]

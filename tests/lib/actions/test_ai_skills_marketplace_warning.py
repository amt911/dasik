"""A codex plugin whose marketplace is not in scope cannot converge — say so.

`codex plugin add superpowers@openai-curated` fails with

    Error: plugin `superpowers` was not found in marketplace `openai-curated`

on a machine where codex has never been signed in, because `openai-curated` is
fetched by codex itself and does not exist until then. dasik does the right
thing already — the command fails, the item is not owned, the next plan asks
again — but the reason is a red line somewhere around 24000 of the run log,
while the plan itself says only `+ [ai_skills] create ...`. This makes `plan`
say it up front.

Measured against codex-cli 0.151.0: `codex plugin marketplace list` exits 0 both
ways, printing a `MARKETPLACE ROOT` table or `No plugin marketplaces in scope.`,
so the exit code cannot be the signal and the output has to be read.
"""
from unittest.mock import MagicMock

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.ai_skills_action import AiSkillsAction
from dasik.lib.target.target import Target


_CODEX_ENTRY = {"name": "superpowers", "method": "codex-plugin",
                "marketplace": {"name": "openai-curated"}}
_CLAUDE_ENTRY = {"name": "superpowers", "method": "claude-plugin",
                 "marketplace": {"name": "caveman",
                                 "source": "JuliusBrussee/caveman"}}

_IN_SCOPE = "MARKETPLACE     ROOT\nopenai-curated  /home/andres/.codex/.tmp/plugins\n"
_EMPTY = "No plugin marketplaces in scope.\n"


@pytest.fixture
def warnings(monkeypatch):
    """Collect the warnings this action emits, and keep the process-wide
    logger out of it — its stream may already be closed by another test."""
    logger = MagicMock()
    monkeypatch.setattr("dasik.lib.actions.ai_skills_action.run_logger.get",
                        lambda: logger)
    return logger.warning


def _machine(tmp_path, user="andres"):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/passwd").write_text(
        "root:x:0:0::/root:/bin/bash\n"
        f"{user}:x:1000:1000::/home/{user}:/bin/bash\n")
    # codex is present (its home marks the agent as installed) but signed out.
    (tmp_path / "home" / user / ".codex").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _probe(monkeypatch, stdout, returncode=0):
    """Stub Command.execute, recording what it was asked to run."""
    calls = []

    def fake(binary, args, **kwargs):
        calls.append((binary, args))
        return MagicMock(returncode=returncode,
                         stdout=stdout.encode(), stderr=b"")

    monkeypatch.setattr(
        "dasik.lib.actions.ai_skills_action.Command.execute", staticmethod(fake))
    return calls


def _plan(root, entries):
    action = AiSkillsAction(
        {"users": [{"username": "andres"}], "ai_skills": {"entries": entries}},
        ActionContext(target=Target(root=str(root))))
    return action.plan(managed=[])


def _said(warn, *needles):
    text = " ".join(str(a) for call in warn.call_args_list
                    for a in list(call.args) + list(call.kwargs.values()))
    return all(n in text for n in needles)


def test_a_codex_marketplace_out_of_scope_is_warned_about(tmp_path, monkeypatch,
                                                          warnings):
    _probe(monkeypatch, _EMPTY)
    plan = _plan(_machine(tmp_path), [_CODEX_ENTRY])
    assert plan, "the change must still be planned — the warning explains it"
    assert _said(warnings, "openai-curated", "codex login")


def test_a_marketplace_in_scope_warns_about_nothing(tmp_path, monkeypatch,
                                                    warnings):
    _probe(monkeypatch, _IN_SCOPE)
    _plan(_machine(tmp_path), [_CODEX_ENTRY])
    assert warnings.call_count == 0


def test_nothing_planned_asks_codex_nothing(tmp_path, monkeypatch, warnings):
    """The probe costs a process. It must only run when a codex plugin is
    actually being proposed."""
    calls = _probe(monkeypatch, _EMPTY)
    _plan(_machine(tmp_path), [])
    assert calls == []


def test_a_claude_plugin_does_not_ask_codex(tmp_path, monkeypatch, warnings):
    calls = _probe(monkeypatch, _IN_SCOPE)
    _plan(_machine(tmp_path), [_CLAUDE_ENTRY])
    assert not any("marketplace list" in " ".join(map(str, args))
                   for _binary, args in calls)


def test_a_probe_that_fails_says_nothing(tmp_path, monkeypatch, warnings):
    """No codex binary, a broken su, a sandbox: 'cannot tell' is not 'missing',
    and a warning nobody can act on is worse than silence."""
    _probe(monkeypatch, "su: command not found\n", returncode=127)
    _plan(_machine(tmp_path), [_CODEX_ENTRY])
    assert warnings.call_count == 0


def test_the_warning_names_the_entry_that_cannot_converge(tmp_path, monkeypatch,
                                                          warnings):
    _probe(monkeypatch, _EMPTY)
    _plan(_machine(tmp_path), [_CODEX_ENTRY])
    assert _said(warnings, "superpowers@openai-curated")


def test_an_unreadable_answer_says_nothing(tmp_path, monkeypatch, warnings):
    """Empty output with exit 0 is not evidence of anything.

    Real codex either prints the `MARKETPLACE ROOT` table or the sentence
    `No plugin marketplaces in scope.` Nothing at all means the probe did not
    answer the question — a stub, a wrapper, a locale — and inventing "there
    are none" from it warns about a machine that may be perfectly fine.
    """
    _probe(monkeypatch, "")
    _plan(_machine(tmp_path), [_CODEX_ENTRY])
    assert warnings.call_count == 0


def test_only_the_header_says_nothing(tmp_path, monkeypatch, warnings):
    _probe(monkeypatch, "MARKETPLACE     ROOT\n")
    _plan(_machine(tmp_path), [_CODEX_ENTRY])
    assert warnings.call_count == 0

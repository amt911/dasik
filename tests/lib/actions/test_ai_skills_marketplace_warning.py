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


# --- a REMOTE catalog is in scope too -------------------------------------- #
#
# Measured on the tower against codex-cli 0.154.0, 2026-09-20: the curated
# catalog is now served remotely and named `openai-curated-remote`. It carries
# superpowers (6.4.1) and is NOT printed by `codex plugin marketplace list` —
# only `codex plugin list` names it, under a `Marketplace \`<name>\`` heading.
# Warning that it is "not in scope" on a machine that can install from it is a
# false alarm that sends the user to `codex login` for nothing.

_REMOTE_ENTRY = {"name": "superpowers", "method": "codex-plugin",
                 "marketplace": {"name": "openai-curated-remote"}}

_PLUGIN_LIST = (
    "Marketplace `21st-dev`\n"
    "/home/andres/.codex/.tmp/marketplaces/21st-dev/.agents/plugins/marketplace.json\n"
    "\n"
    "PLUGIN         STATUS         VERSION  SOURCE\n"
    "21st@21st-dev  not installed           /home/andres/.codex/.tmp/marketplaces/21st-dev\n"
    "\n"
    "Marketplace `openai-curated-remote`\n"
    "Remote catalog\n"
    "\n"
    "PLUGIN                             STATUS         VERSION  SOURCE\n"
    "superpowers@openai-curated-remote  not installed  6.4.1    plugins~Plugin_60ae\n")


def _two_probes(monkeypatch, marketplace_out, plugin_out):
    """Stub the two probes separately: they answer different questions."""
    calls = []

    def fake(binary, args, **kwargs):
        calls.append((binary, args))
        script = " ".join(str(a) for a in args)
        out = plugin_out if "plugin list" in script else marketplace_out
        return MagicMock(returncode=0, stdout=out.encode(), stderr=b"")

    monkeypatch.setattr(
        "dasik.lib.actions.ai_skills_action.Command.execute", staticmethod(fake))
    return calls


def test_a_remote_catalog_counts_as_in_scope(tmp_path, monkeypatch, warnings):
    _two_probes(monkeypatch, _EMPTY, _PLUGIN_LIST)
    _plan(_machine(tmp_path), [_REMOTE_ENTRY])
    assert warnings.call_count == 0


def test_a_marketplace_in_neither_listing_is_still_warned_about(
        tmp_path, monkeypatch, warnings):
    _two_probes(monkeypatch, _EMPTY, _PLUGIN_LIST)
    _plan(_machine(tmp_path), [_CODEX_ENTRY])     # plain `openai-curated`
    assert _said(warnings, "openai-curated")


def test_the_remote_catalog_is_only_asked_for_when_the_cheap_probe_misses(
        tmp_path, monkeypatch, warnings):
    """`codex plugin list` reaches the network. It must not run for a
    marketplace the local listing already confirmed."""
    calls = _two_probes(monkeypatch, _IN_SCOPE, _PLUGIN_LIST)
    _plan(_machine(tmp_path), [_CODEX_ENTRY])
    assert not any("plugin list" in " ".join(map(str, args))
                   for _binary, args in calls)


# --- a marketplace codex can no longer load -------------------------------- #
#
# Measured in a guest, 2026-09-20: `~/.codex/.tmp/marketplaces/<name>` is a
# CACHE. Delete it and `[marketplaces.<name>]` survives in config.toml, so a
# reader that trusts the registry alone reports the marketplace as present —
# `dasik plan` says "No changes" while codex itself answers
#
#     Error: failed to load marketplace(s):
#     - `21st-dev` at /home/test/.codex/.tmp/marketplaces/21st-dev:
#       marketplace root does not contain a supported manifest
#
# Two stores disagreeing, which is the family of bug this repo keeps shipping:
# each side is individually consistent and the pair is broken.

_BROKEN = ("Error: failed to load marketplace(s):\n"
           "- `21st-dev` at /home/test/.codex/.tmp/marketplaces/21st-dev: "
           "marketplace root does not contain a supported manifest\n")

_REGISTERED = ('[marketplaces.21st-dev]\nsource_type = "git"\n'
               'source = "https://github.com/21st-dev/magic-mcp.git"\n')

_MARKET_ENTRY = {"name": "21st", "method": "codex-plugin",
                 "marketplace": {"name": "21st-dev",
                                 "source": "21st-dev/magic-mcp"}}


def _registered(root, user="andres", text=_REGISTERED):
    codex = root / "home" / user / ".codex"
    codex.mkdir(parents=True, exist_ok=True)
    (codex / "config.toml").write_text(text)


def test_a_marketplace_codex_cannot_load_is_not_present(tmp_path, monkeypatch,
                                                        warnings):
    """The registry says yes, codex says no. dasik must believe codex, or it
    reports a converged machine whose plugin manager is broken."""
    _machine(tmp_path)
    _registered(tmp_path)
    _probe(monkeypatch, _BROKEN, returncode=1)
    plan = _plan(tmp_path, [_MARKET_ENTRY])
    assert [(c.op.name, c.item) for c in plan if "marketplace" in c.item], \
        "a marketplace codex refuses to load has to be planned again"


def test_a_marketplace_codex_lists_is_present(tmp_path, monkeypatch, warnings):
    _machine(tmp_path)
    _registered(tmp_path)
    _probe(monkeypatch,
           "MARKETPLACE  ROOT\n21st-dev     /home/andres/.codex/.tmp/marketplaces/21st-dev\n")
    plan = _plan(tmp_path, [_MARKET_ENTRY])
    assert not [c for c in plan if "marketplace" in c.item]


def test_an_unaskable_codex_falls_back_to_the_registry(tmp_path, monkeypatch,
                                                       warnings):
    """No codex, no su, a sandbox: the registry is the only evidence there is,
    and inventing "missing" from silence would re-add on every run."""
    _machine(tmp_path)
    _registered(tmp_path)
    _probe(monkeypatch, "su: command not found\n", returncode=127)
    plan = _plan(tmp_path, [_MARKET_ENTRY])
    assert not [c for c in plan if "marketplace" in c.item]


def test_no_login_advice_for_a_marketplace_this_very_plan_creates(
        tmp_path, monkeypatch, warnings):
    """Seen on a real apply: dasik proposed `create ...:marketplace:21st-dev`
    and, in the same breath, warned that the marketplace was not in scope and
    the user should run `codex login`. It is not in scope because it has not
    been added YET — by the line above. A marketplace dasik registers itself
    needs no login, and the advice sends the user chasing a non-problem."""
    _probe(monkeypatch, _EMPTY)
    plan = _plan(_machine(tmp_path), [_MARKET_ENTRY])
    assert any("marketplace" in c.item for c in plan), "the create is the point"
    assert warnings.call_count == 0


def test_the_broken_marketplace_report_is_read_from_stderr_too(
        tmp_path, monkeypatch, warnings):
    """Measured in a guest: codex prints `failed to load marketplace(s)` on
    STDERR and exits non-zero. A probe that reads only stdout sees an empty
    answer, decides nothing is broken, and `plan` reports a converged machine —
    which is exactly what the first guest run showed."""
    _machine(tmp_path)
    _registered(tmp_path)

    def fake(binary, args, **kwargs):
        return MagicMock(returncode=1, stdout=b"", stderr=_BROKEN.encode())

    monkeypatch.setattr(
        "dasik.lib.actions.ai_skills_action.Command.execute", staticmethod(fake))
    plan = _plan(tmp_path, [_MARKET_ENTRY])
    assert [c for c in plan if "marketplace" in c.item]

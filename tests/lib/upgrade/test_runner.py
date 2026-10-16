from types import SimpleNamespace

from dasik.lib.command_worker.command_worker import Command
from dasik.lib.upgrade.runner import exit_code, render, run_steps
from dasik.lib.upgrade.steps import UpgradeOutcome, UpgradeStep


def _versions(*values):
    it = iter(values)
    return lambda: next(it)


def _stub(monkeypatch, rcs, stdout="", stderr=""):
    calls = []
    rcs = list(rcs)

    def fake(cmd, args, **kw):
        calls.append((cmd, args, kw))
        return SimpleNamespace(returncode=rcs.pop(0), stdout=stdout,
                               stderr=stderr)
    monkeypatch.setattr(Command, "execute", staticmethod(fake))
    return calls


def _step(item="x", commands=(("true", ()),), **kw):
    return UpgradeStep(domain="d", item=item, user="u",
                       commands=commands, **kw)


def test_skip_runs_nothing(monkeypatch):
    calls = _stub(monkeypatch, [])
    out = run_steps([_step(skip="pinned")], None)
    assert out == [UpgradeOutcome("x", "skipped", None, None, "pinned")]
    assert calls == []


def test_versions_differ_is_updated(monkeypatch):
    calls = _stub(monkeypatch, [0])
    [o] = run_steps([_step(version=_versions("1.0", "1.1"))], None)
    assert (o.status, o.before, o.after) == ("updated", "1.0", "1.1")
    cmd, args, kw = calls[0]
    assert cmd == "su" and args == ["-", "u", "-c", "true", "--", "sh"]
    assert kw["check"] is False and kw["stream"] is True
    assert kw["label"] == "upgrade: x"


def test_same_version_is_current(monkeypatch):
    _stub(monkeypatch, [0])
    [o] = run_steps([_step(version=_versions("1.0", "1.0"))], None)
    assert o.status == "current"


def test_unknown_version_is_refreshed(monkeypatch):
    _stub(monkeypatch, [0])
    [o] = run_steps([_step()], None)
    assert o.status == "refreshed"
    _stub(monkeypatch, [0])
    [o] = run_steps([_step(version=_versions("1.0", None))], None)
    assert o.status == "refreshed"


def test_first_failure_stops_the_step_not_the_run(monkeypatch):
    calls = _stub(monkeypatch, [1, 0], stderr="boom\nmore")
    a = _step("A", commands=(("one", ()), ("two", ())))
    b = _step("B", version=_versions("1", "2"))
    out = run_steps([a, b], None)
    assert [o.status for o in out] == ["failed", "updated"]
    assert out[0].detail == "boom\nmore"
    assert len(calls) == 2
    assert exit_code(out) == 1


def test_all_fail_offline(monkeypatch):
    _stub(monkeypatch, [1, 1, 1], stdout="offline")
    out = run_steps([_step(f"s{i}") for i in range(3)], None)
    assert [o.status for o in out] == ["failed"] * 3
    assert out[0].detail == "offline"
    assert exit_code(out) == 1


def test_exit_code_zero_when_no_failure():
    assert exit_code([UpgradeOutcome("a", "skipped", None, None, "p")]) == 0


def test_render_lines():
    outs = [
        UpgradeOutcome("a", "updated", "1", "2"),
        UpgradeOutcome("b", "current", "3", "3"),
        UpgradeOutcome("c", "refreshed", None, None),
        UpgradeOutcome("d", "skipped", None, None, "pinned"),
        UpgradeOutcome("e", "failed", None, None, "bad\nrest"),
    ]
    assert render(outs).splitlines() == [
        "a: updated 1 → 2",
        "b: already latest (3)",
        "c: refreshed (version unknown)",
        "d: skipped (pinned)",
        "e: FAILED — bad",
        "1 updated, 1 already latest, 1 refreshed, 1 skipped, 1 failed",
    ]


# --- dependent steps and failure text -------------------------------------- #

def test_step_after_a_failed_step_is_skipped_without_running(monkeypatch):
    calls = _stub(monkeypatch, [1, 0], stderr="offline")
    mkt = _step("M")
    plug = _step("P", after="M", version=_versions("1", "1"))
    out = run_steps([mkt, plug], None)
    assert [o.status for o in out] == ["failed", "skipped"]
    assert out[1].detail == "marketplace refresh failed"
    assert len(calls) == 1


def test_step_after_a_good_step_runs(monkeypatch):
    calls = _stub(monkeypatch, [0, 0])
    out = run_steps([_step("M"), _step("P", after="M")], None)
    assert [o.status for o in out] == ["refreshed", "refreshed"]
    assert len(calls) == 2


def test_after_unknown_item_runs_normally(monkeypatch):
    _stub(monkeypatch, [0])
    [o] = run_steps([_step("P", after="nope")], None)
    assert o.status == "refreshed"


def test_failure_text_in_stdout_fails_a_zero_exit(monkeypatch):
    _stub(monkeypatch, [0], stdout="a\n\x1b[1m✗ Failed to check skills\x1b[0m\nb")
    [o] = run_steps([_step(failure_text=("Failed to check",))], None)
    assert o.status == "failed"
    assert o.detail == "✗ Failed to check skills"


def test_failure_text_in_stderr_also_counts(monkeypatch):
    _stub(monkeypatch, [0], stderr="Failed to check")
    [o] = run_steps([_step(failure_text=("Failed to check",))], None)
    assert o.status == "failed"


def test_failure_text_absent_keeps_the_zero_exit_good(monkeypatch):
    _stub(monkeypatch, [0], stdout="all good")
    [o] = run_steps([_step(failure_text=("Failed to check",))], None)
    assert o.status == "refreshed"


def test_no_failure_text_ignores_output(monkeypatch):
    _stub(monkeypatch, [0], stdout="Failed to check")
    [o] = run_steps([_step()], None)
    assert o.status == "refreshed"


def test_exception_from_command_is_a_failed_outcome_and_run_continues(monkeypatch):
    calls = []

    def fake(cmd, args, **kw):
        calls.append(args)
        if len(calls) == 1:
            raise OSError("su: not found\nsecond line")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(Command, "execute", staticmethod(fake))
    out = run_steps([_step("a"), _step("b")], None)
    assert [(o.item, o.status) for o in out] == [("a", "failed"), ("b", "refreshed")]
    assert out[0].detail == "su: not found"


def test_exception_from_version_is_a_failed_outcome(monkeypatch):
    _stub(monkeypatch, [0])

    def boom():
        raise RuntimeError("bad record")
    out = run_steps([_step(version=boom)], None)
    assert out[0].status == "failed" and out[0].detail == "bad record"

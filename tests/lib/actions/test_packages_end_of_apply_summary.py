"""The packages that an apply did NOT install are repeated at its very end.

``package_policy.unknown: warn-and-skip`` (the default) and
``build_failure: warn-and-continue`` both let an apply finish with exit 0
while leaving declared packages off the machine. Each is warned about when it
happens — in the middle of an apply that can print thousands of lines of
pacman and makepkg output, where the warning scrolls away and the run then
ends on a success line. That is how a VM install reported rc=0 with
``llama.cpp`` silently missing (2026-09-19).

``finalize_apply()`` runs once, after every action of a successful apply, so
it is where the summary belongs: the last thing the user reads names what is
not installed.
"""
from __future__ import annotations

import io
import re

import pytest

import dasik.lib.logging.run_logger as rl
from dasik.lib.actions.package_resolver import PackageResolution
from dasik.lib.actions.packages_action import PackagesAction


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


@pytest.fixture
def console(monkeypatch):
    stream = io.StringIO()
    logger = rl.RunLogger(log_path=None, verbose=False, color=False, stream=stream)
    monkeypatch.setattr("dasik.lib.actions.packages_action.run_logger.get",
                        lambda: logger)
    return stream


def _action(**policy):
    return PackagesAction({"packages": ["base", "ghost", "broken"],
                           "package_policy": policy})


def test_skipped_unknown_package_is_named_at_the_end(console):
    action = _action()
    action._handle_unknown(PackageResolution(repo=["base"], unknown=["ghost"]))
    console.truncate(0)
    console.seek(0)

    action.finalize_apply()

    out = _plain(console.getvalue())
    assert "warning:" in out
    assert "ghost" in out
    assert "no source" in out


def test_failed_build_is_named_at_the_end(console):
    action = _action(build_failure="warn-and-continue")
    action._record_failure("broken", RuntimeError("makepkg failed"))

    action.finalize_apply()

    out = _plain(console.getvalue())
    assert "warning:" in out
    assert "broken" in out
    assert "failed to build" in out


def test_both_kinds_are_reported_together(console):
    action = _action(build_failure="warn-and-continue")
    action._handle_unknown(PackageResolution(repo=["base"], unknown=["ghost"]))
    action._record_failure("broken", RuntimeError("makepkg failed"))
    console.truncate(0)
    console.seek(0)

    action.finalize_apply()

    out = _plain(console.getvalue())
    assert "ghost" in out and "broken" in out


def test_nothing_missing_prints_nothing(console):
    action = _action()
    action._handle_unknown(PackageResolution(repo=["base", "ghost", "broken"]))

    action.finalize_apply()

    assert console.getvalue() == ""

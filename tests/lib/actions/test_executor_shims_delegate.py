"""`is_needed()` / `execute()` are two lines over the v3 contract, and no more.

`ActionExecutor` and a number of tests still enter through the pre-v3 pair, so
it stays — but as a *shim*, never as a second implementation. It had become one:

* `PackagesAction.execute()` installed with
  `Command.execute("pacman", ["--noconfirm", "--needed", "-S"] + missing)` and
  **no `check=True`**, so a failed pacman was silent, while its `apply()` had
  grown the reason probes, the removability guard and the lock detection;
* `SystemdAction.execute()` called
  `subprocess.run(["arch-chroot", "/mnt", "systemctl", …])` directly — a
  hardcoded `/mnt`, ignoring the target a `--target /` run is driving;
* `UsersAction.is_needed()` asked whether the declared groups were a SUBSET of
  the real ones, while `plan()` compares the sets, so an extra group on the
  machine was drift to one and invisible to the other.

Two implementations of one job, one of them a decade behind and still
importable, is how a refactor accidentally ships the old behaviour (issue #238).
This file is the guard that keeps them one.
"""
import ast
import pathlib

import pytest

from dasik.lib.actions.abstract_action import AbstractAction

_ACTIONS_DIR = pathlib.Path("dasik/lib/actions")
_SHIMS = ("is_needed", "execute")


def _overrides():
    """(file, method) for every action that defines a shim of its own."""
    found = []
    for path in sorted(_ACTIONS_DIR.glob("*.py")):
        if path.name == "abstract_action.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.FunctionDef) and node.name in _SHIMS:
                found.append((path.name, node.name))
    return found


def test_no_action_writes_its_own_shim():
    """The base class answers both, so an override is a second implementation.

    If you are here because you added one: put the behaviour in `plan()` /
    `apply()` instead. That is the path the CLI runs; a shim that disagrees with
    it is a bug nobody sees until an old entry point is used again.
    """
    assert _overrides() == []


def test_the_base_shims_delegate_to_the_v3_contract():
    calls = []

    class _Probe(AbstractAction):
        _DOMAIN = "probe"

        @property
        def name(self):
            return "probe"

        def plan(self, managed):
            calls.append(("plan", tuple(managed)))
            return ["a change"]

        def apply(self, changes):
            calls.append(("apply", tuple(changes)))

    probe = _Probe({}, None)

    assert probe.is_needed() is True
    probe.execute()

    assert calls == [("plan", ()), ("plan", ()), ("apply", ("a change",))]


def test_is_needed_is_false_when_the_plan_is_empty():
    class _Converged(AbstractAction):
        _DOMAIN = "converged"

        @property
        def name(self):
            return "converged"

        def plan(self, managed):
            return []

    assert _Converged({}, None).is_needed() is False


def test_an_action_needs_neither_shim_to_be_instantiable():
    """They used to be @abstractmethod, which is what forced 25 classes to
    write one."""
    class _Minimal(AbstractAction):
        _DOMAIN = "minimal"

        @property
        def name(self):
            return "minimal"

    _Minimal({}, None)      # would raise TypeError while they were abstract

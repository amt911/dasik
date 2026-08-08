"""SystemdAction: one unresolvable unit must not hide the others.

The 2026-08-08 install died after 10 minutes on
``systemctl enable coolercontrold.service`` (exit 1, empty stderr — the console
said only ``systemctl failed (exit 1):``, naming neither the unit nor the
reason). Four MORE units in the same config were equally orphaned
(``smb``/``nmb``/``ollama``/``tailscaled``): each would have surfaced one apply
at a time.

So ``apply()``:

* labels every call with the unit, so a failure is self-describing even when
  systemctl prints nothing;
* attempts EVERY unit and raises ONCE, listing all of them.

It still raises — a unit that cannot be enabled is a real divergence and must
never be recorded as converged (F-06).
"""
from unittest.mock import patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.systemd_action import SystemdAction
from dasik.lib.exceptions.exceptions import CommandExecutionError
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target


def _ctx(root="/mnt"):
    return ActionContext(target=Target(root=root))


def _enable(*units):
    return [Change("systemd", Op.ENABLE, u) for u in units]


def _failing(*bad):
    """Command.execute side effect: raise for the units in *bad*."""
    def side(cmd, args, **kw):
        if args[-1] in bad:
            raise CommandExecutionError(f"systemctl failed (exit 1): {args[-1]}")
        return None
    return side


def test_every_unit_is_attempted_even_after_a_failure():
    """The units after the broken one still get enabled — progress is kept."""
    a = SystemdAction({}, _ctx())
    changes = _enable("a.service", "broken.service", "z.service")
    with patch("dasik.lib.actions.systemd_action.Command.execute",
               side_effect=_failing("broken.service")) as run:
        with pytest.raises(CommandExecutionError):
            a.apply(changes)
    attempted = [c.args[1][-1] for c in run.call_args_list]
    assert attempted == ["a.service", "broken.service", "z.service"]


def test_single_error_lists_every_failing_unit():
    a = SystemdAction({}, _ctx())
    changes = _enable("ok.service", "coolercontrold.service", "ollama.service")
    with patch("dasik.lib.actions.systemd_action.Command.execute",
               side_effect=_failing("coolercontrold.service", "ollama.service")):
        with pytest.raises(CommandExecutionError) as excinfo:
            a.apply(changes)
    message = str(excinfo.value)
    assert "coolercontrold.service" in message
    assert "ollama.service" in message
    assert "ok.service" not in message


def test_error_points_at_the_missing_package():
    """The message must explain the cause, not just the exit code."""
    a = SystemdAction({}, _ctx())
    with patch("dasik.lib.actions.systemd_action.Command.execute",
               side_effect=_failing("coolercontrold.service")):
        with pytest.raises(CommandExecutionError) as excinfo:
            a.apply(_enable("coolercontrold.service"))
    assert "package" in str(excinfo.value).lower()


def test_each_call_is_labelled_with_the_unit():
    """systemctl can exit non-zero with an empty stderr; the label is what makes
    the single-command error name the unit."""
    a = SystemdAction({}, _ctx())
    with patch("dasik.lib.actions.systemd_action.Command.execute") as run:
        a.apply(_enable("sshd.service") +
                [Change("systemd", Op.DISABLE, "bluetooth.service")])
    labels = [c.kwargs.get("label") for c in run.call_args_list]
    assert labels == ["systemctl enable sshd.service",
                      "systemctl disable bluetooth.service"]


def test_disable_failures_are_aggregated_too():
    a = SystemdAction({}, _ctx())
    changes = [Change("systemd", Op.DISABLE, "gone.service"),
               Change("systemd", Op.DISABLE, "also-gone.service")]
    with patch("dasik.lib.actions.systemd_action.Command.execute",
               side_effect=_failing("gone.service", "also-gone.service")):
        with pytest.raises(CommandExecutionError) as excinfo:
            a.apply(changes)
    assert "gone.service" in str(excinfo.value)
    assert "also-gone.service" in str(excinfo.value)


def test_nothing_raised_when_every_unit_succeeds():
    a = SystemdAction({}, _ctx())
    with patch("dasik.lib.actions.systemd_action.Command.execute"):
        a.apply(_enable("sshd.service", "cups.socket"))

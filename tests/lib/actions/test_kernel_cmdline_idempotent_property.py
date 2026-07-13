"""Property-based idempotency for KernelCmdlineAction (CLAUDE.md § Quality).

Kernel-cmdline params are a set domain reconciled with the same D/M/A set-math as
packages. Invariants: when the current cmdline already carries exactly the
declared tokens (and dasik owns them), planning is empty; a token present on the
cmdline but neither declared nor owned is drift and is never removed.
"""
from types import SimpleNamespace

from hypothesis import given
from hypothesis import strategies as st

from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction
from dasik.lib.state.change import Op

_token = st.text(alphabet="abcde=0123", min_size=1, max_size=5)
_tokens = st.lists(_token, max_size=5, unique=True)


def _action(explicit, current_tokens):
    a = KernelCmdlineAction(
        {"kernel_cmdline": list(explicit), "bootloader": "grub"},
        context=SimpleNamespace(target=object()),
    )
    a._derive_from_disks = lambda: []          # isolate: no disk-derived params
    a.actual = lambda: set(current_tokens)
    return a


@given(tokens=_tokens)
def test_converged_cmdline_plan_is_empty(tokens):
    """Declared tokens all present, managed == declared ⇒ no-op re-run."""
    a = _action(explicit=tokens, current_tokens=tokens)
    assert a.plan(managed=a._desired_tokens()) == []


@given(declared=_tokens, extra=_tokens)
def test_undeclared_cmdline_token_is_not_removed(declared, extra):
    """A token on the cmdline that is neither declared nor owned is drift —
    never emitted as a REMOVE."""
    strangers = set(extra) - set(declared)
    a = _action(explicit=declared, current_tokens=set(declared) | strangers)
    changes = a.plan(managed=a._desired_tokens())
    removed = {c.item for c in changes if c.op is Op.REMOVE}
    assert removed.isdisjoint(strangers)


@given(declared=_tokens, managed=_tokens, current=_tokens)
def test_plan_only_removes_owned_undeclared_tokens(declared, managed, current):
    a = _action(explicit=declared, current_tokens=current)
    changes = a.plan(managed=list(managed))
    removed = {c.item for c in changes if c.op is Op.REMOVE}
    assert removed <= (set(managed) - set(a._desired_tokens()))

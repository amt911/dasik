"""Property-based idempotency for the concrete composite domains locale + pacman.

Both reconcile a multi-field record through CompositeV3Action. The invariant:
when the on-disk record matches the declared one, planning is empty; a mismatch
on any field yields exactly one MODIFY naming the changed field(s). Proven here
over generated configs by making the mocked `_actual_state` reflect the desired
record (converged) or a mutated one (drift).
"""
from types import SimpleNamespace

from hypothesis import given
from hypothesis import strategies as st

from dasik.lib.actions.locale_action import LocaleAction
from dasik.lib.actions.pacman_action import PacmanAction
from dasik.lib.state.change import Op

_ctx = SimpleNamespace(target=object())
_locale = st.text(alphabet="abcdeUTF-8._ ", min_size=1, max_size=10)


@st.composite
def _locale_action(draw):
    cfg = {
        "selected_locales": draw(st.lists(_locale, max_size=3, unique=True)),
        "desired_locale": draw(_locale),
        "desired_tty_layout": draw(st.sampled_from(["us", "es", "de", "fr"])),
    }
    return LocaleAction(cfg, context=_ctx)


@given(a=_locale_action())
def test_locale_converged_is_a_noop(a):
    a._actual_state = lambda: a._desired_state()
    assert a.plan(managed=[]) == []
    assert a.is_needed() is False


@given(a=_locale_action())
def test_locale_drift_yields_one_modify(a):
    desired = a._desired_state()
    drifted = dict(desired)
    drifted["desired_tty_layout"] = drifted["desired_tty_layout"] + "X"  # guaranteed change
    a._actual_state = lambda: drifted
    changes = a.plan(managed=[])
    assert len(changes) == 1
    assert changes[0].op is Op.MODIFY
    assert changes[0].item == "desired_tty_layout"


@given(
    parallel=st.booleans(), color=st.booleans(),
    verbose=st.booleans(), multilib=st.booleans(),
)
def test_pacman_converged_is_a_noop(parallel, color, verbose, multilib):
    a = PacmanAction(
        {"options": {"Parallel": parallel, "Color": color, "VerbosePkgLists": verbose},
         "multilib": multilib},
        context=_ctx,
    )
    a._actual_state = lambda: a._desired_state()
    assert a.plan(managed=[]) == []
    assert a.is_needed() is False


@given(parallel=st.booleans(), color=st.booleans(), multilib=st.booleans())
def test_pacman_option_drift_yields_one_modify(parallel, color, multilib):
    a = PacmanAction(
        {"options": {"Parallel": parallel, "Color": color, "VerbosePkgLists": False},
         "multilib": multilib},
        context=_ctx,
    )
    desired = a._desired_state()
    drifted = dict(desired)
    drifted["Color"] = not desired["Color"]  # flip one field
    a._actual_state = lambda: drifted
    changes = a.plan(managed=[])
    assert len(changes) == 1
    assert changes[0].op is Op.MODIFY
    assert changes[0].item == "Color"

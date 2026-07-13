"""Property-based idempotency for UsersAction (CLAUDE.md § Quality).

UsersAction.plan reconciles declared users against /etc/passwd + /etc/shadow +
/etc/group via set-math plus per-user MODIFY drift checks. The NixOS invariant:
when the system already has exactly the declared users with matching
shell/groups/password, planning is empty; a mismatch on any attribute yields
exactly one MODIFY for that user. Proven here over generated user sets by making
the mocked "system readers" reflect the declared state.
"""
from types import SimpleNamespace

from hypothesis import given
from hypothesis import strategies as st

from dasik.lib.actions.users_action import UsersAction
from dasik.lib.state.change import Op

_username = st.text(alphabet="abcdefghijkmnop", min_size=1, max_size=6)
_shell = st.sampled_from(["/bin/bash", "/bin/zsh", "/usr/bin/fish"])
_groups = st.lists(st.sampled_from(["wheel", "audio", "video", "docker"]),
                   max_size=3, unique=True)
_hash = st.text(alphabet="0123456789abcdef$./", min_size=4, max_size=12)


@st.composite
def _user_sets(draw):
    names = draw(st.lists(_username, min_size=0, max_size=4, unique=True))
    return [
        {"username": n, "shell": draw(_shell), "groups": draw(_groups),
         "hashed_password": draw(_hash)}
        for n in names
    ]


def _converged(action, users):
    """Point the action's system readers at exactly the declared state."""
    by = {u["username"]: u for u in users}
    action.actual = lambda: set(by)
    action._shell = lambda n: by[n].get("shell", "/bin/bash")
    action._groups = lambda n: set(by[n].get("groups", []))
    action._hash = lambda n: by[n]["hashed_password"]


@given(users=_user_sets())
def test_converged_users_plan_is_empty(users):
    """System already matches the declared users ⇒ the v3 plan is empty (no-op
    re-run). (is_needed() is the separate legacy path with its own file reads;
    the reconciler drives plan(), which is the idempotency contract here.)"""
    action = UsersAction(users, context=SimpleNamespace(target=object()))
    _converged(action, users)
    managed = [u["username"] for u in users]  # dasik owns exactly the declared
    assert action.plan(managed=managed) == []


@given(users=_user_sets(), tweak=_hash)
def test_password_drift_yields_one_modify(users, tweak):
    """If one user's stored hash differs, exactly one MODIFY(password) for them
    and nothing else — the change is scoped, not a full re-apply."""
    if not users:
        return
    action = UsersAction(users, context=SimpleNamespace(target=object()))
    _converged(action, users)
    victim = users[0]["username"]
    if tweak == users[0]["hashed_password"]:
        return  # not actually a drift
    real_hash = action._hash
    action._hash = lambda n: (tweak if n == victim else real_hash(n))

    changes = action.plan(managed=[u["username"] for u in users])
    assert [(c.op, c.item) for c in changes] == [(Op.MODIFY, victim)]
    assert "password" in changes[0].reason


@given(users=_user_sets())
def test_undeclared_user_is_not_deleted_unless_managed(users):
    """An existing user that dasik does not own (not in managed) is never DELETEd
    — the drift-safety property, at the users layer."""
    action = UsersAction(users, context=SimpleNamespace(target=object()))
    by = {u["username"]: u for u in users}
    # System has an extra user 'stranger' that is neither declared nor managed.
    action.actual = lambda: set(by) | {"stranger"}
    action._shell = lambda n: by.get(n, {}).get("shell", "/bin/bash")
    action._groups = lambda n: set(by.get(n, {}).get("groups", []))
    action._hash = lambda n: by.get(n, {}).get("hashed_password", "")

    changes = action.plan(managed=[u["username"] for u in users])
    deleted = {c.item for c in changes if c.op is Op.DELETE}
    assert "stranger" not in deleted

"""A package declared `{"name": …, "reason": "dep"}` has to converge.

Found by driving the ThinkPad's real config through a VM: the FIRST apply set
21 packages to `installed as dependency`, pacman confirmed every one of them,
and the SECOND apply planned the exact same 21 changes and ran the exact same
`pacman -D --asdeps` again. Forever. `plan -> apply -> plan` never fell silent,
which is the one promise the whole tool rests on.

The cause is a widening that is right for ownership and wrong here.
`actual()` is `pacman -Qqe` PLUS every declared name a *provider* satisfies —
without that, a name no package literally carries (a virtual dependency, a
group) could never be owned, and dropping it from the config would remove
nothing. But `pacman -T <name>` answers "is this satisfied", and an installed
package satisfies its own name whether it is explicit or a dependency. So every
`reason: dep` package landed back in the "explicit" set the reason check
compares against, and the check saw drift that was not there.

The reason question has exactly one honest source: `pacman -Qqe`, raw.
"""
from unittest.mock import MagicMock, patch

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.packages_action import PackagesAction
from dasik.lib.state.change import Op
from dasik.lib.target.target import Target


def _action(declared, installed, explicit):
    """A PackagesAction over a machine whose pacman answers are fixed.

    `pacman -T` is answered the way the real one does: an installed package
    satisfies its own name, dependency or not — which is precisely the answer
    that used to poison the reason check.
    """
    action = PackagesAction(config=declared,
                            context=ActionContext(target=Target(root="/mnt")))

    def execute(cmd, args=None, **kw):
        args = args or []
        if cmd == "pacman" and args[:1] == ["-Qqe"]:
            return MagicMock(stdout="\n".join(explicit).encode(), returncode=0)
        if cmd == "pacman" and args[:1] == ["-Qq"]:
            return MagicMock(stdout="\n".join(installed).encode(), returncode=0)
        if cmd == "pacman" and args[:1] == ["-T"]:
            missing = [a for a in args[1:] if a not in installed]
            return MagicMock(stdout="\n".join(missing).encode(),
                             returncode=127 if missing else 0)
        if cmd == "pacman" and args[:1] == ["-Sg"]:
            return MagicMock(stdout=b"", returncode=1)
        return MagicMock(stdout=b"", returncode=0)

    return action, execute


def _plan(declared, installed, explicit, managed=()):
    action, execute = _action(declared, installed, explicit)
    with patch("dasik.lib.actions.packages_action.Command.execute",
               side_effect=execute):
        return action.plan(managed=list(managed))


def _reason_modifies(changes):
    return sorted(c.item for c in changes
                  if c.op is Op.MODIFY and c.reason == "install reason")


DECLARED = [{"name": "avahi", "reason": "dep"},
            {"name": "mesa", "reason": "dep"},
            "btop"]


def test_a_dep_package_already_marked_as_a_dep_plans_nothing():
    """The regression. `avahi` is installed and NOT in -Qqe: it IS a dep."""
    changes = _plan(DECLARED,
                    installed=["avahi", "mesa", "btop"],
                    explicit=["btop"])
    assert _reason_modifies(changes) == []


def test_a_dep_package_still_marked_explicit_is_planned():
    """The other direction has to keep working, or the fix is just a mute."""
    changes = _plan(DECLARED,
                    installed=["avahi", "mesa", "btop"],
                    explicit=["avahi", "mesa", "btop"])
    assert _reason_modifies(changes) == ["avahi", "mesa"]


def test_an_explicit_package_demoted_to_a_dep_is_planned():
    """`btop` is declared without a reason, so it must be EXPLICIT; pacman has
    it as a dependency (nothing is in -Qqe). The two dep-declared ones are
    already right, so only btop drifts."""
    changes = _plan(DECLARED,
                    installed=["avahi", "mesa", "btop"],
                    explicit=[])
    assert _reason_modifies(changes) == ["btop"]


def test_the_reason_check_ignores_packages_that_are_not_installed():
    changes = _plan(DECLARED, installed=["btop"], explicit=["btop"])
    assert _reason_modifies(changes) == []


def test_ownership_still_sees_a_dep_package_as_owned():
    """The widening exists for a reason: `actual()` must keep claiming these,
    or a sync would disown them and dropping one from the config would remove
    nothing at all (the bug fixed in the pacman-group case, #216)."""
    action, execute = _action(DECLARED,
                              installed=["avahi", "mesa", "btop"],
                              explicit=["btop"])
    with patch("dasik.lib.actions.packages_action.Command.execute",
               side_effect=execute):
        owned = action.actual()

    assert {"avahi", "mesa", "btop"} <= owned

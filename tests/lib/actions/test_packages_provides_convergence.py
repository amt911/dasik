"""A declared name that only a PROVIDER satisfies must converge, and be owned.

`iptables-nft` stopped being a package: `iptables` in core carries
`Provides: iptables-nft` and `Replaces: iptables-nft`. Resolving the name is
half the job — `pacman -Qq` answers `iptables` and never `iptables-nft`, so:

  * `plan` proposed the install again after every apply. Driven in a guest:
    apply, then `dasik apply` once more, and the second one still said
    `+ [packages] install iptables-nft` / `Applied: now at generation 2`. The
    exact shape CLAUDE.md warns about — planned, applied, planned forever, with
    every apply reporting success.
  * `actual` never reported it, so the reconciler could not own it, and dropping
    the name from the config afterwards would remove nothing at all.

`pacman -T` is the question both need: it prints the dependencies that are NOT
satisfied, honouring `Provides`, so a provided name disappears from its output.
"""
from unittest.mock import MagicMock, patch

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.packages_action import PackagesAction
from dasik.lib.state.change import Op
from dasik.lib.target.target import Target
from tests.support.pacman import pacman_double


def _pacman(installed=(), explicit=(), satisfied=()):
    """The shared strict double (tests/support/pacman.py)."""
    return pacman_double(installed=list(installed), explicit=list(explicit),
                         satisfied=list(satisfied))


def _action(names):
    return PackagesAction(config=list(names),
                          context=ActionContext(target=Target(root="/")))


def test_a_provided_name_is_not_planned_again_after_it_is_installed():
    a = _action(["iptables-nft"])
    with patch("dasik.lib.actions.packages_action.Command.execute",
               _pacman(installed=["iptables"], explicit=["iptables"],
                       satisfied=["iptables-nft"])):
        changes = a.plan(managed=["iptables-nft"])
    assert [c.item for c in changes if c.op is Op.INSTALL] == []


def test_a_name_nothing_provides_is_still_planned():
    a = _action(["iptables-nft"])
    with patch("dasik.lib.actions.packages_action.Command.execute",
               _pacman(installed=["firefox"], explicit=["firefox"], satisfied=[])):
        changes = a.plan(managed=[])
    assert [c.item for c in changes if c.op is Op.INSTALL] == ["iptables-nft"]


def test_a_provided_name_is_reported_as_actual_so_ownership_survives():
    """Without this, `_owned_after_sync` (actual ∩ (owned ∪ declared)) drops the
    name, and removing it from the config later removes nothing — the same way a
    pacman group used to be dispossessed."""
    a = _action(["iptables-nft"])
    with patch("dasik.lib.actions.packages_action.Command.execute",
               _pacman(installed=["iptables"], explicit=["iptables"],
                       satisfied=["iptables-nft"])):
        assert "iptables-nft" in a.actual()


def test_a_fresh_target_plans_every_package():
    """The probe runs `pacman -T` inside the target, and on a target that is
    still an empty directory arch-chroot fails: rc != 0, no output. Reading that
    as "nothing is unsatisfied" deleted the whole packages domain from the plan
    of a fresh install — a guest installed base and a bootloader and not one
    declared package, and said `rc=0`.

    Nothing is installed, so nothing can be providing anything: no probe is even
    meaningful here.
    """
    def broken_chroot(cmd, args=None, *a, **kw):
        args = list(args or [])
        flag = args[0] if args else None
        if flag in ("-Qq", "-Qqe"):
            return MagicMock(stdout=b"", stderr=b"", returncode=0)
        # what arch-chroot answers over an empty /mnt
        return MagicMock(stdout=b"", stderr=b"failed to setup chroot", returncode=1)

    a = _action(["git", "python", "sudo"])
    with patch("dasik.lib.actions.packages_action.Command.execute", broken_chroot):
        changes = a.plan(managed=[])
    assert sorted(c.item for c in changes if c.op is Op.INSTALL) == \
        ["git", "python", "sudo"]


def test_a_probe_that_fails_means_not_satisfied():
    """Same rule with packages already on the machine: an exit code pacman never
    uses for deptest (0 = all satisfied, 127 = these are missing) is an answer we
    cannot read, and an unreadable answer must never skip an install."""
    def failing_probe(cmd, args=None, *a, **kw):
        args = list(args or [])
        flag = args[0] if args else None
        if flag == "-Qq":
            return MagicMock(stdout=b"firefox\n", stderr=b"", returncode=0)
        if flag == "-Qqe":
            return MagicMock(stdout=b"firefox\n", stderr=b"", returncode=0)
        if flag == "-T":
            return MagicMock(stdout=b"", stderr=b"boom", returncode=1)
        return MagicMock(stdout=b"", stderr=b"", returncode=0)

    a = _action(["iptables-nft"])
    with patch("dasik.lib.actions.packages_action.Command.execute", failing_probe):
        changes = a.plan(managed=[])
    assert [c.item for c in changes if c.op is Op.INSTALL] == ["iptables-nft"]


def test_an_undeclared_name_is_never_invented_by_the_probe():
    """actual() answers for what is declared or installed, not for every name a
    provider happens to satisfy."""
    a = _action(["firefox"])
    with patch("dasik.lib.actions.packages_action.Command.execute",
               _pacman(installed=["firefox", "iptables"],
                       explicit=["firefox", "iptables"],
                       satisfied=["iptables-nft"])):
        assert "iptables-nft" not in a.actual()

"""A declared pacman group must converge, and survive a sync.

`plan()` compares the declared names against `pacman -Qq`, which lists
*packages* and never groups. So a config that declares `xorg` — the natural way
to say "the X server and its tools", and what the imperative installer this tool
replaces actually ran (`pacman -S xorg`) — used to behave like this:

    plan   →  + [packages] install xorg      … forever, on every run
    apply  →  pacman -S xorg                 … works, installs all 49 members
    sync   →  rewrites `xorg` into its 49 members

Three separate breakages of the project's own promise: the plan never
converges, `plan → apply → plan` is never silent, and the first `dasik save`
destroys the declaration it was meant to carry back.

The fix teaches `plan()` and `import_state()` what `apply()` already knew (see
`PackageResolver.repo_groups`): a group is satisfied when every member of it is
installed.
"""
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.packages_action import PackagesAction
from dasik.lib.target.target import Target
from tests.support.pacman import pacman_double


@pytest.fixture(autouse=True)
def _quiet_run_logger():
    """The blocked-removal warning writes to the run log, which a full-suite run
    may have closed already."""
    from dasik.lib.logging import run_logger
    with patch.object(run_logger, "get", return_value=MagicMock()):
        yield


XORG = ("xorg-server", "xorg-xrandr", "xorg-xwayland")
TEXLIVE = ("texlive-basic", "texlive-latex")

# `pacman -Sg a b` answers two columns, "<group> <member>", one member per line.
# A name that is not a group contributes no rows (it errors on stderr instead),
# which is exactly the "treat it as a package" answer.
_GROUPS = {"xorg": XORG, "texlive": TEXLIVE}


def _sg_output(names):
    return "\n".join(f"{g} {m}" for g in names if g in _GROUPS
                     for m in _GROUPS[g])


def _qi(entries):
    """A `pacman -Qi` answer: [(name, required_by), …]."""
    blocks = [f"Name            : {name}\n"
              f"Version         : 1-1\n"
              f"Required By     : {required}\n"
              for name, required in entries]
    return MagicMock(stdout="\n".join(blocks).encode(), returncode=0)


def _dispatch(required_by=(), sg_error=None):
    """Route `Command.execute` by the pacman operation being asked for.

    Built on the shared strict double, so a pacman query nobody modelled raises
    instead of coming back as an empty (and therefore meaningful) answer. The
    `-Sg` failure injection and this file's multi-block `-Qi` shape stay local.
    """
    strict = pacman_double(groups=_GROUPS)

    def run(cmd, args, **kwargs):
        args = list(args or [])
        if args and args[0] == "-Sg" and sg_error is not None:
            raise sg_error
        if args and args[0] == "-Qi":
            return _qi(required_by)
        return strict(cmd, args)
    return run


def _action(desired, installed):
    action = PackagesAction({"packages": list(desired)},
                            ActionContext(target=Target(root="/")))
    action._installed_all = MagicMock(return_value=set(installed))
    # The reason branch asks `_explicit_raw` (`pacman -Qqe`, raw); `actual()`
    # widens that with groups/providers for ownership. Same machine, both doors.
    action._explicit_raw = MagicMock(return_value=set(installed))
    action.actual = MagicMock(return_value=set(installed))
    return action


def _plan(desired, installed, managed=(), required_by=(), sg_error=None):
    action = _action(desired, installed)
    with patch("dasik.lib.actions.packages_action.Command.execute",
               side_effect=_dispatch(required_by, sg_error)):
        return [(c.op.name, c.item) for c in action.plan(managed=list(managed))]


# --------------------------------------------------------------------------- #
#  plan()
# --------------------------------------------------------------------------- #

def test_a_group_whose_members_are_all_installed_is_converged():
    """The whole point: a re-run of the same JSON must be a no-op."""
    assert _plan(desired=["xorg"], installed=XORG) == []


def test_a_group_missing_one_member_is_planned():
    assert _plan(desired=["xorg"], installed=XORG[:-1]) == [("INSTALL", "xorg")]


def test_a_group_is_planned_as_the_group_never_as_its_members():
    """`apply` resolves the group and runs one `pacman -S xorg`; announcing 49
    individual installs would describe a transaction that never happens."""
    planned = _plan(desired=["xorg"], installed=())

    assert planned == [("INSTALL", "xorg")]


def test_two_groups_are_judged_independently():
    planned = _plan(desired=["xorg", "texlive"],
                    installed=XORG + TEXLIVE[:1])

    assert planned == [("INSTALL", "texlive")]


def test_a_plain_package_is_unaffected():
    """No group anywhere: the behaviour that shipped must not move."""
    assert _plan(desired=["htop"], installed=()) == [("INSTALL", "htop")]
    assert _plan(desired=["htop"], installed=["htop"]) == []


def test_a_group_and_a_package_together():
    planned = _plan(desired=["xorg", "htop"], installed=XORG)

    assert planned == [("INSTALL", "htop")]


def test_a_group_query_that_cannot_answer_falls_back_to_package_behaviour():
    """No pacman to ask (a half-built target, an offline probe): plan the name
    as a package, exactly as before groups were understood. Failing safe means
    over-reporting a change, never silently claiming convergence."""
    planned = _plan(desired=["xorg"], installed=XORG,
                    sg_error=OSError("no pacman"))

    assert planned == [("INSTALL", "xorg")]


class _TruncatedOutput(str):
    """Output that dies part-way through being read.

    Non-empty on purpose: ``_group_members`` reads ``stdout or b""``, so an
    empty instance would be swapped for the default before the read even
    starts and the test would prove nothing.
    """

    def splitlines(self):    # type: ignore[override]
        yield "xorg xorg-server"
        raise OSError("truncated read")


def test_a_half_read_group_is_never_judged_converged():
    """The dangerous shape: one member arrives, the read dies, and a group of
    three now looks complete because its only known member is installed. A
    membership map is published only after the output has been read to the end,
    so a partial answer is no answer."""
    action = _action(["xorg"], installed=["xorg-server"])
    with patch("dasik.lib.actions.packages_action.Command.execute",
               return_value=MagicMock(
                   stdout=_TruncatedOutput("xorg xorg-server"), returncode=0)):
        planned = [(c.op.name, c.item) for c in action.plan(managed=[])]

    assert planned == [("INSTALL", "xorg")]


def test_the_group_query_runs_once_per_plan():
    """One `pacman -Sg` for every declared name, not one per name."""
    action = _action(["xorg", "texlive", "htop"], installed=XORG + TEXLIVE)
    with patch("dasik.lib.actions.packages_action.Command.execute",
               side_effect=_dispatch()) as execute:
        action.plan(managed=[])

    sg_calls = [c for c in execute.call_args_list
                if c.args[1] and c.args[1][0] == "-Sg"]
    assert len(sg_calls) == 1


# --------------------------------------------------------------------------- #
#  plan() — dropping a group
# --------------------------------------------------------------------------- #

def test_dropping_a_group_plans_its_installed_members():
    """`pacman -R xorg` expands to the members, so the plan must say so — and
    must go through the blocked-removal check, or one member another package
    still requires aborts the whole apply."""
    planned = _plan(desired=[], installed=XORG, managed=["xorg"],
                    required_by=[(m, "None") for m in XORG])

    assert sorted(planned) == sorted(("REMOVE", m) for m in XORG)


def test_dropping_a_group_keeps_the_members_the_system_still_needs():
    planned = _plan(
        desired=[], installed=XORG, managed=["xorg"],
        required_by=[("xorg-server", "None"), ("xorg-xrandr", "plasma-workspace"),
                     ("xorg-xwayland", "None")])

    assert sorted(planned) == [("REMOVE", "xorg-server"),
                               ("REMOVE", "xorg-xwayland")]


def test_switching_from_members_to_the_group_removes_nothing():
    """The migration every real config makes: a capture lists the 49 members,
    the admin replaces them with `xorg`, and the manifest still owns the member
    names. Without this, one apply installs the group and then deletes packages
    out of it — `apply` installs before it removes, so the machine ends up
    missing the very members the plan just put back."""
    planned = _plan(desired=["xorg"], installed=XORG, managed=list(XORG),
                    required_by=[(m, "None") for m in XORG])

    assert planned == []


def test_a_member_the_group_does_not_cover_is_still_removed():
    """`xorg-xeyes` is not in the `xorg` group. Owning it and then declaring
    only the group means it really is no longer declared."""
    planned = _plan(desired=["xorg"], installed=XORG + ("xorg-xeyes",),
                    managed=list(XORG) + ["xorg-xeyes"],
                    required_by=[("xorg-xeyes", "None")])

    assert planned == [("REMOVE", "xorg-xeyes")]


def test_dropping_a_group_ignores_members_that_are_not_installed():
    planned = _plan(desired=[], installed=XORG[:1], managed=["xorg"],
                    required_by=[("xorg-server", "None")])

    assert planned == [("REMOVE", "xorg-server")]


# --------------------------------------------------------------------------- #
#  actual() — ownership, the half a VM found missing
# --------------------------------------------------------------------------- #

def _actual(desired, explicit, installed=None):
    """`actual()` for real — nothing about it is stubbed here."""
    action = PackagesAction({"packages": list(desired)},
                            ActionContext(target=Target(root="/")))
    action._installed_all = MagicMock(
        return_value=set(installed if installed is not None else explicit))

    run = pacman_double(groups=_GROUPS, explicit=list(explicit),
                        installed=list(installed if installed is not None else explicit))

    with patch("dasik.lib.actions.packages_action.Command.execute",
               side_effect=run):
        return action.actual()


def test_actual_reports_a_complete_declared_group():
    """`sync` records `actual ∩ (owned ∪ declared)`. A group absent from
    `actual()` is dropped from the manifest on every sync — and an unowned
    domain is one nothing can ever remove."""
    assert "xorg" in _actual(desired=["xorg"], explicit=XORG)


def test_actual_omits_an_incomplete_group():
    """Reporting it would claim dasik owns something the machine does not have."""
    assert "xorg" not in _actual(desired=["xorg"], explicit=XORG[:1],
                                 installed=XORG[:1])


def test_actual_omits_a_group_nobody_declared():
    """`actual()` answers for the declared set; inventing every group on the
    machine is the "bare observation" the reconciler refuses to record."""
    assert _actual(desired=["htop"], explicit=("htop",)) == {"htop"}


def test_actual_still_reports_the_packages_it_always_did():
    assert _actual(desired=["xorg"], explicit=XORG + ("htop",)) == {
        *XORG, "htop", "xorg"}


def test_ownership_survives_a_sync():
    """The end-to-end shape of the VM failure: the group is declared, complete,
    and therefore claimable — so the manifest keeps it and a later removal has
    something to act on."""
    action = PackagesAction({"packages": ["xorg"]},
                            ActionContext(target=Target(root="/")))
    action._installed_all = MagicMock(return_value=set(XORG))

    run = pacman_double(groups=_GROUPS, explicit=list(XORG), installed=list(XORG))

    with patch("dasik.lib.actions.packages_action.Command.execute",
               side_effect=run):
        owned_after_sync = action.actual() & set(
            action.managed_keys()["packages"])

    assert owned_after_sync == {"xorg"}


# --------------------------------------------------------------------------- #
#  import_state() — sync
# --------------------------------------------------------------------------- #

def _captured(desired, installed):
    action = _action(desired, installed)
    action._unit_provider_packages = MagicMock(return_value=set())
    with patch("dasik.lib.actions.packages_action.Command.execute",
               side_effect=_dispatch()):
        return action.import_state()["packages"]


def test_sync_keeps_a_declared_group_instead_of_exploding_it():
    """`pacman -Qqe` reports the members; re-emitting them would replace the
    declaration with the thing it stands for, and the next save would have
    nothing left to keep."""
    captured = _captured(desired=["xorg"], installed=XORG)

    assert captured == ["xorg"]


def test_sync_keeps_an_incomplete_group_as_declared():
    """The config is intent. A group half-installed is a divergence for `plan`
    to report, not a reason for the capture to rewrite what was declared."""
    captured = _captured(desired=["xorg"], installed=XORG[:1])

    assert captured == ["xorg"]


def test_sync_still_captures_a_package_outside_the_group():
    captured = _captured(desired=["xorg"], installed=XORG + ("htop",))

    assert captured == ["xorg", "htop"]


def test_sync_leaves_a_config_without_groups_alone():
    captured = _captured(desired=["htop"], installed=("htop", "btop"))

    assert captured == ["htop", "btop"]


def test_a_declared_member_survives_beside_its_group():
    """`xorg-xeyes` is NOT in the `xorg` group but sits next to it in a real
    config; declaring a member the group does not cover must keep working."""
    captured = _captured(desired=["xorg", "xorg-xeyes"],
                         installed=XORG + ("xorg-xeyes",))

    assert captured == ["xorg", "xorg-xeyes"]


def test_sync_round_trips_a_group_to_a_silent_plan():
    """The real invariant: sync → plan must say nothing."""
    installed = XORG + ("htop",)
    captured = _captured(desired=["xorg"], installed=installed)

    assert _plan(desired=captured, installed=installed) == []

from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.packages_action import PackagesAction
from dasik.lib.actions.package_resolver import PackageResolution
from dasik.lib.exceptions.exceptions import CommandExecutionError
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target
from tests.support.pacman import pacman_double


@pytest.fixture(autouse=True)
def _aur_closure_satisfiable():
    """Stub the transitive AUR-closure gate: this file tests the v3 plumbing,
    not the closure (own suite in test_packages_apply_closure_gate.py) — and
    the real walk would try the RPC."""
    with patch("dasik.lib.validation.aur_closure.validate_aur_closure",
               return_value=[]):
        yield


def _ctx(root: str = "/") -> ActionContext:
    return ActionContext(target=Target(root=root))


def _ok():
    """A successful CompletedProcess-like result (needed now that mutating
    Command.execute calls pass check=True and inspect returncode)."""
    return MagicMock(returncode=0, stdout=b"", stderr=b"")


def _resolution(repo=(), aur=(), groups=(), unknown=(), unavailable=()):
    """A canned PackageResolution so apply-routing tests stay decoupled from the
    live PackageResolver (network + pacman DBs)."""
    return PackageResolution(
        repo=list(repo),
        aur=list(aur),
        groups=list(groups),
        unknown=list(unknown),
        unavailable=list(unavailable),
    )


def _fake_command_run(stdout: bytes = b"", returncode: int = 0):
    """The shared strict double, keeping this file's one-list signature.

    -Qqm (foreign/AUR) stays empty so these tests keep their "no AUR"
    assumption; the aur- prefixing is covered by the dedicated foreign tests.
    Anything the double does not model raises rather than answering "".
    """
    names = stdout.decode().split()
    strict = pacman_double(installed=names, explicit=names)

    def fake(cmd, args=None, *a, **kw):
        result = strict(cmd, args)
        if returncode:
            result.returncode = returncode
        return result
    return MagicMock(side_effect=fake)


def test_packages_action_is_v3_after_migration():
    assert PackagesAction.is_v3() is True


def test_actual_runs_pacman_Qqe_against_target_and_returns_set():
    fake = _fake_command_run(stdout=b"git\nhtop\nvim\n")
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=[], context=_ctx("/"))
        result = a.actual()
    assert result == {"git", "htop", "vim"}
    assert fake.called
    call_args = fake.call_args
    # Command.execute("pacman", ["-Qqe"], target=Target(root="/"))
    assert call_args.args[0] == "pacman"
    assert call_args.args[1] == ["-Qqe"]
    assert call_args.kwargs.get("target") is not None
    assert call_args.kwargs["target"].root == "/"


def test_actual_handles_empty_pacman_output():
    fake = _fake_command_run(stdout=b"")
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=[], context=_ctx("/"))
        assert a.actual() == set()


def test_actual_strips_blank_lines():
    fake = _fake_command_run(stdout=b"git\n\nhtop\n")
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=[], context=_ctx("/"))
        assert a.actual() == {"git", "htop"}


def test_actual_returns_empty_when_context_is_none():
    """Legacy call-sites instantiate without context — actual must not crash."""
    a = PackagesAction(config=[], context=None)
    assert a.actual() == set()


def test_plan_emits_install_for_missing_pacman_pkgs():
    fake = _fake_command_run(stdout=b"git\n")  # only git installed
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=["git", "htop"], context=_ctx("/"))
        changes = a.plan(managed=[])
    items = [(c.op, c.item) for c in changes]
    assert items == [(Op.INSTALL, "htop")]


def test_plan_emits_remove_for_managed_no_longer_declared():
    fake = _fake_command_run(stdout=b"vim\n")
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=[], context=_ctx("/"))
        changes = a.plan(managed=["vim"])
    assert len(changes) == 1
    assert changes[0].op == Op.REMOVE
    assert changes[0].item == "vim"
    assert changes[0].destructive is True


def test_plan_includes_aur_pkgs_as_install_changes():
    """Plan 4: AUR packages participate in plan()/apply() (stripped of aur- prefix)."""
    fake = _fake_command_run(stdout=b"")  # nothing installed
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=["git", "aur-yay"], context=_ctx("/"))
        changes = a.plan(managed=[])
    items = sorted((c.op, c.item) for c in changes)
    assert items == [(Op.INSTALL, "git"), (Op.INSTALL, "yay")]


def test_plan_empty_when_converged():
    fake = _fake_command_run(stdout=b"git\nhtop\n")
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=["git", "htop"], context=_ctx("/"))
        assert a.plan(managed=["git", "htop"]) == []


def test_managed_keys_returns_desired_pacman_set():
    a = PackagesAction(config=["git", "htop"], context=_ctx("/"))
    assert a.managed_keys() == {"packages": ["git", "htop"]}


def test_managed_keys_includes_aur_pkgs_stripped_of_prefix():
    """Plan 4: manifest tracks AUR packages too (under the 'packages' domain)."""
    a = PackagesAction(config=["git", "aur-yay"], context=_ctx("/"))
    assert a.managed_keys() == {"packages": ["git", "yay"]}


def test_import_state_returns_actual_as_config_fragment():
    fake = _fake_command_run(stdout=b"git\nhtop\n")
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=[], context=_ctx("/"))
        frag = a.import_state()
    assert frag == {"packages": ["git", "htop"]}


def test_legacy_is_needed_still_works_without_context():
    """Legacy entry point: ActionExecutor passes context=ActionContext()
    with target=None. is_needed/execute must keep working (hardcoded /mnt).
    """
    a = PackagesAction(config=["git"], context=ActionContext())
    # The legacy is_needed calls _missing → _is_installed, which uses
    # arch-chroot /mnt directly. We just confirm calling it does not raise.
    with patch("dasik.lib.actions.packages_action.subprocess.run") as run:
        run.return_value = MagicMock(returncode=1)  # not installed
        assert a.is_needed() is True


# ---------------------------------------------------------------------- #
#  Plan 4: apply() — destructive path (pacman + AUR)                     #
# ---------------------------------------------------------------------- #


def test_apply_no_changes_is_noop():
    a = PackagesAction(config=["git"], context=_ctx("/"))
    with patch("dasik.lib.actions.packages_action.Command.execute") as run:
        a.apply([])
    run.assert_not_called()


def _mutating(run):
    """The pacman calls that CHANGE something.

    apply() also probes `pacman -Qq`/`-Qqe` at the end to make the install
    reasons true against reality (issue #188), `-Sg` to tell a declared pacman
    group from a package, and `-T` to see whether a declared name is already
    satisfied by a PROVIDER, so counting every call would count those read-only
    queries too.
    """
    return [c for c in run.call_args_list
            if c.args[0] == "pacman"
            and c.args[1][0] not in ("-Qq", "-Qqe", "-D", "-Sg", "-T")]


def test_apply_install_routes_pacman_pkgs_through_pacman_S():
    a = PackagesAction(config=["git", "htop"], context=_ctx("/"))
    changes = [
        Change("packages", Op.INSTALL, "git"),
        Change("packages", Op.INSTALL, "htop"),
    ]
    with patch("dasik.lib.actions.packages_action.Command.execute") as run, \
         patch.object(a, "_resolve_sources", return_value=_resolution(repo=["git", "htop"])):
        run.return_value = _ok()
        a.apply(changes)
    # One pacman -S call with both names + --noconfirm --needed
    assert len(_mutating(run)) == 1
    args = _mutating(run)[0]
    assert args.args[0] == "pacman"
    pacman_args = args.args[1]
    assert "-S" in pacman_args
    assert "--noconfirm" in pacman_args
    assert "--needed" in pacman_args
    assert "git" in pacman_args
    assert "htop" in pacman_args
    assert args.kwargs["target"].root == "/"
    assert args.kwargs.get("stream") is True   # long install streams live


def test_apply_remove_routes_through_pacman_Rns():
    a = PackagesAction(config=[], context=_ctx("/"))
    changes = [Change("packages", Op.REMOVE, "vim")]
    with patch("dasik.lib.actions.packages_action.Command.execute") as run:
        run.return_value = _ok()
        a.apply(changes)
    assert len(_mutating(run)) == 1
    args = _mutating(run)[0]
    assert args.args[0] == "pacman"
    pacman_args = args.args[1]
    assert "-Rns" in pacman_args
    assert "--noconfirm" in pacman_args
    assert "vim" in pacman_args
    assert args.kwargs.get("stream") is True   # long removal streams live


def test_apply_mixes_install_and_remove_in_correct_order():
    """Install BEFORE remove (additive first reduces breakage if remove fails)."""
    a = PackagesAction(config=["git"], context=_ctx("/"))
    changes = [
        Change("packages", Op.REMOVE, "vim"),
        Change("packages", Op.INSTALL, "git"),
    ]
    with patch("dasik.lib.actions.packages_action.Command.execute") as run, \
         patch.object(a, "_resolve_sources", return_value=_resolution(repo=["git"])):
        run.return_value = _ok()
        a.apply(changes)
    # Two mutating calls: pacman -S first, then pacman -Rns
    assert len(_mutating(run)) == 2
    first_args = _mutating(run)[0].args
    second_args = _mutating(run)[1].args
    assert "-S" in first_args[1]
    assert "-Rns" in second_args[1]


def test_apply_aur_install_uses_makepkg_path():
    """AUR INSTALL: pkg in self.aur_pkgs goes through the makepkg dance."""
    a = PackagesAction(config=["aur-yay"], context=_ctx("/"))
    changes = [Change("packages", Op.INSTALL, "yay")]
    with patch.object(PackagesAction, "_apply_aur_install") as aur_install, \
         patch("dasik.lib.actions.packages_action.Command.execute") as run:
        a.apply(changes)
    aur_install.assert_called_once_with(["yay"], helper="yay")
    # No pacman -S call (no pacman pkgs to install). The read-only probes are
    # not that: `-Sg yay` only asks whether the name is a pacman group.
    for call in _mutating(run):
        assert "-S" not in call.args[1] or "base-devel" in call.args[1]
        # _apply_aur_install is mocked, so any Command.execute here would be
        # incidental setup we did not stub. Assert it is not a bulk pacman -S
        # of the AUR pkg list:
        assert "yay" not in call.args[1]


def test_apply_separates_pacman_install_from_aur_install():
    """Mixed config: pacman pkg → pacman -S; AUR pkg → AUR path."""
    a = PackagesAction(config=["git", "aur-yay"], context=_ctx("/"))
    changes = [
        Change("packages", Op.INSTALL, "git"),
        Change("packages", Op.INSTALL, "yay"),
    ]
    with patch.object(PackagesAction, "_apply_aur_install") as aur_install, \
         patch.object(a, "_resolve_sources",
                      return_value=_resolution(repo=["git"], aur=["yay"])), \
         patch("dasik.lib.actions.packages_action.Command.execute") as run:
        run.return_value = _ok()
        a.apply(changes)
    aur_install.assert_called_once_with(["yay"], helper="yay")
    # Exactly one pacman -S call for the pacman items
    pacman_S_calls = [
        c for c in run.call_args_list
        if c.args[0] == "pacman" and "-S" in c.args[1] and "git" in c.args[1]
    ]
    assert len(pacman_S_calls) == 1


def test_apply_skips_when_context_target_missing():
    """Defensive: no target → apply is a no-op (cannot run pacman)."""
    a = PackagesAction(config=["git"], context=None)
    with patch("dasik.lib.actions.packages_action.Command.execute") as run:
        a.apply([Change("packages", Op.INSTALL, "git")])
    run.assert_not_called()


def test_apply_aur_install_delegates_to_aur_installer():
    """_apply_aur_install builds the hybrid AurInstaller with the action's target
    and resolver, and hands it resolution.aur."""
    a = PackagesAction(config=["aur-yay"], context=_ctx("/"))
    with patch("dasik.lib.actions.aur_installer.AurInstaller") as Installer:
        a._apply_aur_install(["asunder"], helper="yay")
    # constructed with (target, resolver=self._resolver)
    Installer.assert_called_once()
    call = Installer.call_args
    assert call.args[0] is a.context.target
    assert call.kwargs.get("resolver") is a._resolver
    # and driven with the AUR package list + the chosen helper
    Installer.return_value.install.assert_called_once_with(
        ["asunder"], helper="yay", fragment_is_ours=False)


def test_apply_passes_declared_helper_when_only_rest_is_pending():
    """Partial retry: yay was installed by an earlier failed apply, so it is NOT
    in the delta — but it is still declared, so it must be the chosen helper."""
    action = PackagesAction(config=["yay", "asunder"], context=_ctx("/"))
    changes = [Change("packages", Op.INSTALL, "asunder")]
    with patch.object(
        action, "_resolve_sources", return_value=_resolution(aur=["asunder"])
    ), patch.object(action, "_apply_aur_install") as aur_install:
        action.apply(changes)
    aur_install.assert_called_once_with(["asunder"], helper="yay")


def test_apply_excludes_helper_skipped_as_unknown():
    """A helper dropped by warn-and-skip is not installed, so it is not eligible."""
    action = PackagesAction(config=["yay", "asunder"], context=_ctx("/"))
    changes = [
        Change("packages", Op.INSTALL, "yay"),
        Change("packages", Op.INSTALL, "asunder"),
    ]
    # warn-and-skip logs through the process-wide run_logger; stub it so the test
    # never writes to a capture stream another test already closed.
    with patch.object(
        action,
        "_resolve_sources",
        return_value=_resolution(aur=["asunder"], unknown=["yay"]),
    ), patch("dasik.lib.actions.packages_action.run_logger.get",
             return_value=MagicMock()), \
            patch.object(action, "_apply_aur_install") as aur_install:
        action.apply(changes)
    aur_install.assert_called_once_with(["asunder"], helper=None)


def test_apply_uses_next_eligible_helper_when_first_is_skipped():
    action = PackagesAction(config=["yay", "paru", "asunder"], context=_ctx("/"))
    changes = [
        Change("packages", Op.INSTALL, "yay"),
        Change("packages", Op.INSTALL, "paru"),
        Change("packages", Op.INSTALL, "asunder"),
    ]
    with patch.object(
        action,
        "_resolve_sources",
        return_value=_resolution(aur=["paru", "asunder"], unknown=["yay"]),
    ), patch("dasik.lib.actions.packages_action.run_logger.get",
             return_value=MagicMock()), \
            patch.object(action, "_apply_aur_install") as aur_install:
        action.apply(changes)
    aur_install.assert_called_once_with(["paru", "asunder"], helper="paru")


def test_apply_aur_install_requires_target():
    a = PackagesAction(config=["aur-yay"], context=None)
    with pytest.raises(CommandExecutionError):
        a._apply_aur_install(["yay"])


# ---------------------------------------------------------------------- #
#  Plan 5: import_state(managed) — sync reconciliation                   #
# ---------------------------------------------------------------------- #


def test_import_state_captures_drift_with_managed():
    """A \\ D \\ M is appended; declared+owned survive."""
    fake = _fake_command_run(stdout=b"git\nhtop\n")  # A = {git, htop}
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=["git"], context=_ctx("/"))
        frag = a.import_state(managed=["git"])  # M = {git}; htop is drift
    assert frag == {"packages": ["git", "htop"]}


def test_import_state_keeps_declared_intent_even_if_uninstalled():
    """A declared package not currently installed is kept as intent (sync never
    drops a declaration just because it is absent right now)."""
    fake = _fake_command_run(stdout=b"git\n")  # A = {git}; vim not installed
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=["git", "vim"], context=_ctx("/"))
        frag = a.import_state(managed=["git", "vim"])
    assert frag == {"packages": ["git", "vim"]}


def test_import_state_reemits_legacy_aur_survivor_as_plain_and_appends_drift():
    """A survivor declared with the deprecated aur- prefix is re-emitted plain;
    the source is resolved at apply, not encoded in the name."""
    fake = _fake_command_run(stdout=b"git\nyay\nhtop\n")  # A = {git, yay, htop}
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=["git", "aur-yay"], context=_ctx("/"))
        frag = a.import_state(managed=["git", "yay"])  # htop is drift
    assert frag == {"packages": ["git", "yay", "htop"]}


def test_import_state_keeps_declared_aur_entry_even_if_uninstalled():
    """A declared aur- package not currently installed is kept as intent — as the
    plain name (no aur- prefix on re-emit)."""
    fake = _fake_command_run(stdout=b"git\n")  # A = {git}; yay not installed
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=["git", "aur-yay"], context=_ctx("/"))
        frag = a.import_state(managed=["git", "yay"])
    assert frag == {"packages": ["git", "yay"]}


def test_import_state_keeps_declared_intent_not_owned_not_present():
    """D \\ A that is NOT owned (mere intent) is kept; sync never drops intent."""
    fake = _fake_command_run(stdout=b"git\n")  # A = {git}; 'future' not installed
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=["git", "future"], context=_ctx("/"))
        frag = a.import_state(managed=[])  # M = {} → nothing vanished
    assert frag == {"packages": ["git", "future"]}


def test_import_state_zero_arg_still_bootstraps_full_actual():
    """Back-compat: no managed arg ≡ M = {} → capture all of A (bootstrap)."""
    fake = _fake_command_run(stdout=b"git\nhtop\n")
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=[], context=_ctx("/"))
        assert a.import_state() == {"packages": ["git", "htop"]}


def test_import_state_captures_owned_present_undeclared():
    """Present + owned (M) but NOT declared (D) must still be captured (reality)."""
    fake = _fake_command_run(stdout=b"git\nhtop\nvim\n")  # A = git,htop,vim
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=["git"], context=_ctx("/"))
        frag = a.import_state(managed=["htop"])   # htop owned, not declared
    assert frag == {"packages": ["git", "htop", "vim"]}


# ---------------------------------------------------------------------- #
#  Install reason (explicit/dep) — pacman only                           #
# ---------------------------------------------------------------------- #


def _reason_fake(explicit=b"", installed=b""):
    """The shared strict double: -Qqe -> explicit set, -Qq -> all installed."""
    return pacman_double(explicit=explicit.decode().split(),
                         installed=installed.decode().split())


def test_parses_reason_for_pacman_objects():
    a = PackagesAction(config=["git", {"name": "foo", "reason": "dep"}], context=_ctx("/"))
    assert a.pacman_pkgs == ["git", "foo"]
    assert a._reason["git"] == "explicit"
    assert a._reason["foo"] == "dep"


def test_aur_object_ignores_reason():
    a = PackagesAction(config=[{"name": "aur-yay", "reason": "dep"}], context=_ctx("/"))
    assert a.aur_pkgs == ["yay"]
    assert "yay" not in a._reason     # AUR reason-exempt


def test_installed_all_and_reason_of():
    with patch("dasik.lib.actions.packages_action.Command.execute",
               _reason_fake(explicit=b"git\n", installed=b"git\ndep1\n")):
        a = PackagesAction(config=[], context=_ctx("/"))
        assert a._installed_all() == {"git", "dep1"}
        assert a._reason_of("git") == "explicit"
        assert a._reason_of("dep1") == "dep"   # installed but not in -Qqe


def test_plan_no_install_when_declared_dep_already_installed_as_dep():
    with patch("dasik.lib.actions.packages_action.Command.execute",
               _reason_fake(explicit=b"git\n", installed=b"git\nfoo\n")):
        a = PackagesAction(config=["git", {"name": "foo", "reason": "dep"}], context=_ctx("/"))
        changes = a.plan(managed=["git", "foo"])
    assert changes == []


def test_plan_modify_when_reason_drifts():
    with patch("dasik.lib.actions.packages_action.Command.execute",
               _reason_fake(explicit=b"git\nfoo\n", installed=b"git\nfoo\n")):
        a = PackagesAction(config=["git", {"name": "foo", "reason": "dep"}], context=_ctx("/"))
        changes = a.plan(managed=["git", "foo"])
    assert [(c.op, c.item) for c in changes] == [(Op.MODIFY, "foo")]


def test_plan_install_for_declared_dep_not_installed():
    with patch("dasik.lib.actions.packages_action.Command.execute",
               _reason_fake(explicit=b"git\n", installed=b"git\n")):
        a = PackagesAction(config=["git", {"name": "foo", "reason": "dep"}], context=_ctx("/"))
        changes = a.plan(managed=[])
    assert [(c.op, c.item) for c in changes] == [(Op.INSTALL, "foo")]


def test_plan_no_modify_for_aur():
    with patch("dasik.lib.actions.packages_action.Command.execute",
               _reason_fake(explicit=b"git\n", installed=b"git\nyay\n")):
        a = PackagesAction(config=["git", "aur-yay"], context=_ctx("/"))
        changes = a.plan(managed=["git", "yay"])
    assert changes == []   # yay installed (any reason), AUR never MODIFY


def test_apply_marks_installed_dep_as_asdeps():
    a = PackagesAction(config=[{"name": "foo", "reason": "dep"}], context=_ctx("/"))
    with patch("dasik.lib.actions.packages_action.Command.execute") as run, \
         patch.object(a, "_resolve_sources", return_value=_resolution(repo=["foo"])):
        run.return_value = _ok()
        a.apply([Change("packages", Op.INSTALL, "foo")])
    calls = [(c.args[0], c.args[1]) for c in run.call_args_list]
    assert any(c[0] == "pacman" and "-S" in c[1] and "foo" in c[1] for c in calls)
    assert any(c[0] == "pacman" and "-D" in c[1] and "--asdeps" in c[1] and "foo" in c[1]
               for c in calls)


def test_apply_modify_sets_reason_dep():
    a = PackagesAction(config=[{"name": "foo", "reason": "dep"}], context=_ctx("/"))
    with patch("dasik.lib.actions.packages_action.Command.execute") as run:
        run.return_value = _ok()
        a.apply([Change("packages", Op.MODIFY, "foo")])
    calls = [(c.args[0], c.args[1]) for c in run.call_args_list]
    assert ("pacman", ["-D", "--asdeps", "foo"]) in calls


def test_apply_modify_to_explicit():
    a = PackagesAction(config=["foo"], context=_ctx("/"))   # explicit
    with patch("dasik.lib.actions.packages_action.Command.execute") as run:
        run.return_value = _ok()
        a.apply([Change("packages", Op.MODIFY, "foo")])
    calls = [(c.args[0], c.args[1]) for c in run.call_args_list]
    assert ("pacman", ["-D", "--asexplicit", "foo"]) in calls


def test_apply_explicit_install_no_asdeps():
    a = PackagesAction(config=["git"], context=_ctx("/"))
    with patch("dasik.lib.actions.packages_action.Command.execute") as run, \
         patch.object(a, "_resolve_sources", return_value=_resolution(repo=["git"])):
        run.return_value = _ok()
        a.apply([Change("packages", Op.INSTALL, "git")])
    calls = [(c.args[0], c.args[1]) for c in run.call_args_list]
    assert not any("-D" in c[1] for c in calls)   # explicit needs no -D


def test_import_state_declared_dep_kept_as_object():
    with patch("dasik.lib.actions.packages_action.Command.execute",
               _reason_fake(explicit=b"git\n", installed=b"git\nfoo\n")):
        a = PackagesAction(config=["git", {"name": "foo", "reason": "dep"}], context=_ctx("/"))
        frag = a.import_state(managed=["git", "foo"])
    assert frag == {"packages": ["git", {"name": "foo", "reason": "dep"}]}


def test_import_state_explicit_drift_is_plain_string():
    with patch("dasik.lib.actions.packages_action.Command.execute",
               _reason_fake(explicit=b"git\nhtop\n", installed=b"git\nhtop\n")):
        a = PackagesAction(config=["git"], context=_ctx("/"))
        frag = a.import_state(managed=[])
    assert frag == {"packages": ["git", "htop"]}


def test_import_state_reemits_legacy_aur_as_plain_name():
    with patch("dasik.lib.actions.packages_action.Command.execute",
               _reason_fake(explicit=b"git\nyay\n", installed=b"git\nyay\n")):
        a = PackagesAction(config=["git", "aur-yay"], context=_ctx("/"))
        frag = a.import_state(managed=["git", "yay"])
    assert frag == {"packages": ["git", "yay"]}


# --- import_state: foreign (AUR) packages get the aur- prefix ------------- #

def _pacman_dispatch(qqe=b"", qq=b"", qqm=b""):
    """The shared strict double with this file's byte-string signature."""
    return pacman_double(explicit=qqe.decode().split(),
                         installed=qq.decode().split(),
                         foreign=qqm.decode().split())


def test_import_state_captures_foreign_as_plain_real_names():
    """Foreign (AUR) packages are captured under their real name — NO aur- prefix.
    apply() resolves the AUR source; the config stays source-agnostic."""
    fake = _pacman_dispatch(
        qqe=b"firefox\nyay\nclaude-code\n",
        qq=b"firefox\nyay\nclaude-code\n")
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=[], context=_ctx("/"))
        pkgs = a.import_state()["packages"]
    assert "yay" in pkgs and "claude-code" in pkgs and "firefox" in pkgs
    assert "aur-yay" not in pkgs and "aur-claude-code" not in pkgs


def test_import_state_keeps_declared_foreign_plain():
    fake = _pacman_dispatch(qqe=b"yay\n", qq=b"yay\n")
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=["yay"], context=_ctx("/"))   # declared PLAIN
        pkgs = a.import_state()["packages"]
    assert "yay" in pkgs and "aur-yay" not in pkgs


def test_import_state_legacy_aur_prefixed_becomes_plain_no_double_prefix():
    fake = _pacman_dispatch(qqe=b"yay\n", qq=b"yay\n")
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=["aur-yay"], context=_ctx("/"))
        pkgs = a.import_state()["packages"]
    assert pkgs.count("yay") == 1
    assert "aur-yay" not in pkgs and "aur-aur-yay" not in pkgs


def test_import_state_repo_dep_still_plain_dict():
    # a repo (non-foreign) dep-installed package stays {name, reason: dep}
    fake = _pacman_dispatch(qqe=b"", qq=b"linux-headers\n", qqm=b"")
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=["linux-headers"], context=_ctx("/"))
        pkgs = a.import_state()["packages"]
    assert {"name": "linux-headers", "reason": "dep"} in pkgs


def test_import_state_does_not_probe_qqm():
    """import_state no longer needs pacman -Qqm (foreign probe) — the source is
    resolved at apply, so sync captures plain real names with -Qqe/-Qq only."""
    seen_flags = []

    def fake(cmd, args=None, *a, **kw):
        flag = (args or [None])[0]
        seen_flags.append(flag)
        return MagicMock(stdout=b"yay\n", stderr=b"", returncode=0)

    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=[], context=_ctx("/"))
        pkgs = a.import_state()["packages"]
    assert "yay" in pkgs
    assert "-Qqm" not in seen_flags

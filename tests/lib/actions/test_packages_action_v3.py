from unittest.mock import MagicMock, patch

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.packages_action import PackagesAction
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target


def _ctx(root: str = "/") -> ActionContext:
    return ActionContext(target=Target(root=root))


def _fake_command_run(stdout: bytes = b"", returncode: int = 0):
    mock = MagicMock()
    mock.return_value = MagicMock(stdout=stdout, stderr=b"", returncode=returncode)
    return mock


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


def test_apply_install_routes_pacman_pkgs_through_pacman_S():
    a = PackagesAction(config=["git", "htop"], context=_ctx("/"))
    changes = [
        Change("packages", Op.INSTALL, "git"),
        Change("packages", Op.INSTALL, "htop"),
    ]
    with patch("dasik.lib.actions.packages_action.Command.execute") as run:
        a.apply(changes)
    # One pacman -S call with both names + --noconfirm --needed
    assert run.call_count == 1
    args = run.call_args
    assert args.args[0] == "pacman"
    pacman_args = args.args[1]
    assert "-S" in pacman_args
    assert "--noconfirm" in pacman_args
    assert "--needed" in pacman_args
    assert "git" in pacman_args
    assert "htop" in pacman_args
    assert args.kwargs["target"].root == "/"


def test_apply_remove_routes_through_pacman_Rns():
    a = PackagesAction(config=[], context=_ctx("/"))
    changes = [Change("packages", Op.REMOVE, "vim")]
    with patch("dasik.lib.actions.packages_action.Command.execute") as run:
        a.apply(changes)
    assert run.call_count == 1
    args = run.call_args
    assert args.args[0] == "pacman"
    pacman_args = args.args[1]
    assert "-Rns" in pacman_args
    assert "--noconfirm" in pacman_args
    assert "vim" in pacman_args


def test_apply_mixes_install_and_remove_in_correct_order():
    """Install BEFORE remove (additive first reduces breakage if remove fails)."""
    a = PackagesAction(config=["git"], context=_ctx("/"))
    changes = [
        Change("packages", Op.REMOVE, "vim"),
        Change("packages", Op.INSTALL, "git"),
    ]
    with patch("dasik.lib.actions.packages_action.Command.execute") as run:
        a.apply(changes)
    # Two calls: pacman -S first, then pacman -Rns
    assert run.call_count == 2
    first_args = run.call_args_list[0].args
    second_args = run.call_args_list[1].args
    assert "-S" in first_args[1]
    assert "-Rns" in second_args[1]


def test_apply_aur_install_uses_makepkg_path():
    """AUR INSTALL: pkg in self.aur_pkgs goes through the makepkg dance."""
    a = PackagesAction(config=["aur-yay"], context=_ctx("/"))
    changes = [Change("packages", Op.INSTALL, "yay")]
    with patch.object(PackagesAction, "_apply_aur_install") as aur_install, \
         patch("dasik.lib.actions.packages_action.Command.execute") as run:
        a.apply(changes)
    aur_install.assert_called_once_with(["yay"])
    # No pacman -S call (no pacman pkgs to install)
    for call in run.call_args_list:
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
         patch("dasik.lib.actions.packages_action.Command.execute") as run:
        a.apply(changes)
    aur_install.assert_called_once_with(["yay"])
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


def test_apply_aur_install_helper_runs_makepkg_dance():
    """The private _apply_aur_install helper: prerequisites + per-pkg makepkg."""
    from unittest.mock import mock_open

    a = PackagesAction(config=["aur-yay"], context=_ctx("/"))
    m_open = mock_open()
    with patch("dasik.lib.actions.packages_action.Command.execute") as run, \
         patch("dasik.lib.actions.packages_action.subprocess.run") as sp_run, \
         patch("dasik.lib.actions.packages_action.os.path.exists", return_value=False), \
         patch("builtins.open", m_open):
        sp_run.return_value = MagicMock(returncode=1, stdout=b"", stderr=b"")
        a._apply_aur_install(["yay"])

    # Prerequisites: pacman -S base-devel git via Command.execute
    pacman_calls = [c for c in run.call_args_list if c.args[0] == "pacman"]
    assert any(
        "base-devel" in c.args[1] and "git" in c.args[1]
        for c in pacman_calls
    )
    # Build user creation: useradd was called via Command.execute
    useradd_calls = [c for c in run.call_args_list if c.args[0] == "useradd"]
    assert len(useradd_calls) == 1
    # makepkg invoked for each pkg: assert a subprocess.run call's argv
    # contains a 'makepkg -sri' segment for yay.
    makepkg_calls = [
        c for c in sp_run.call_args_list
        if any("makepkg -sri" in str(a) for a in c.args[0])
    ]
    assert len(makepkg_calls) == 1
    assert any("yay" in str(a) for a in makepkg_calls[0].args[0])
    # git clone invoked for the pkg too
    git_clone_calls = [
        c for c in sp_run.call_args_list
        if any("git clone" in str(a) and "yay" in str(a) for a in c.args[0])
    ]
    assert len(git_clone_calls) == 1
    # sudoers fragment was written
    m_open.assert_called_once()
    sudoers_arg = m_open.call_args.args[0]
    assert "sudoers.d/_aurbuilder" in str(sudoers_arg)


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


def test_import_state_preserves_aur_prefix_on_survivors_and_appends_drift():
    fake = _fake_command_run(stdout=b"git\nyay\nhtop\n")  # A = {git, yay, htop}
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=["git", "aur-yay"], context=_ctx("/"))
        frag = a.import_state(managed=["git", "yay"])  # htop is drift
    assert frag == {"packages": ["git", "aur-yay", "htop"]}


def test_import_state_keeps_declared_aur_entry_even_if_uninstalled():
    """A declared aur- package not currently installed is kept as intent."""
    fake = _fake_command_run(stdout=b"git\n")  # A = {git}; yay not installed
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=["git", "aur-yay"], context=_ctx("/"))
        frag = a.import_state(managed=["git", "yay"])
    assert frag == {"packages": ["git", "aur-yay"]}


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
    """Command.execute fake: -Qqe -> explicit set, -Qq -> all installed."""
    def run(cmd, args, *a, **k):
        flag = args[0] if args else ""
        out = explicit if flag == "-Qqe" else installed if flag == "-Qq" else b""
        return MagicMock(stdout=out, stderr=b"", returncode=0)
    return run


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
    with patch("dasik.lib.actions.packages_action.Command.execute") as run:
        a.apply([Change("packages", Op.INSTALL, "foo")])
    calls = [(c.args[0], c.args[1]) for c in run.call_args_list]
    assert any(c[0] == "pacman" and "-S" in c[1] and "foo" in c[1] for c in calls)
    assert any(c[0] == "pacman" and "-D" in c[1] and "--asdeps" in c[1] and "foo" in c[1]
               for c in calls)


def test_apply_modify_sets_reason_dep():
    a = PackagesAction(config=[{"name": "foo", "reason": "dep"}], context=_ctx("/"))
    with patch("dasik.lib.actions.packages_action.Command.execute") as run:
        a.apply([Change("packages", Op.MODIFY, "foo")])
    calls = [(c.args[0], c.args[1]) for c in run.call_args_list]
    assert ("pacman", ["-D", "--asdeps", "foo"]) in calls


def test_apply_modify_to_explicit():
    a = PackagesAction(config=["foo"], context=_ctx("/"))   # explicit
    with patch("dasik.lib.actions.packages_action.Command.execute") as run:
        a.apply([Change("packages", Op.MODIFY, "foo")])
    calls = [(c.args[0], c.args[1]) for c in run.call_args_list]
    assert ("pacman", ["-D", "--asexplicit", "foo"]) in calls


def test_apply_explicit_install_no_asdeps():
    a = PackagesAction(config=["git"], context=_ctx("/"))
    with patch("dasik.lib.actions.packages_action.Command.execute") as run:
        a.apply([Change("packages", Op.INSTALL, "git")])
    calls = [(c.args[0], c.args[1]) for c in run.call_args_list]
    assert not any("-D" in c[1] for c in calls)   # explicit needs no -D

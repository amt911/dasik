from unittest.mock import MagicMock, patch

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.packages_action import PackagesAction
from dasik.lib.state.change import Op
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

from dasik.lib.state.change import Change


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
    a = PackagesAction(config=["aur-yay"], context=_ctx("/"))
    with patch("dasik.lib.actions.packages_action.Command.execute") as run, \
         patch("dasik.lib.actions.packages_action.subprocess.run") as sp_run, \
         patch("builtins.open", create=True):
        sp_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
        a._apply_aur_install(["yay"])
    # At minimum: pacman -S base-devel git was called via Command.execute
    pacman_calls = [c for c in run.call_args_list if c.args[0] == "pacman"]
    assert any(
        "base-devel" in c.args[1] and "git" in c.args[1]
        for c in pacman_calls
    )

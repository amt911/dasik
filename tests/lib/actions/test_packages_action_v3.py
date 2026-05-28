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


def test_plan_ignores_aur_prefixed_entries_in_config():
    """Plan-3 scope: AUR install/remove lands in Plan 4. aur- entries are skipped."""
    fake = _fake_command_run(stdout=b"")
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=["git", "aur-yay"], context=_ctx("/"))
        changes = a.plan(managed=[])
    items = [(c.op, c.item) for c in changes]
    assert items == [(Op.INSTALL, "git")]


def test_plan_empty_when_converged():
    fake = _fake_command_run(stdout=b"git\nhtop\n")
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=["git", "htop"], context=_ctx("/"))
        assert a.plan(managed=["git", "htop"]) == []


def test_managed_keys_returns_desired_pacman_set():
    a = PackagesAction(config=["git", "htop"], context=_ctx("/"))
    assert a.managed_keys() == {"packages": ["git", "htop"]}


def test_managed_keys_ignores_aur_prefix_entries():
    a = PackagesAction(config=["git", "aur-yay"], context=_ctx("/"))
    assert a.managed_keys() == {"packages": ["git"]}


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

"""`sync` must read the boot splash back as a `plymouth` block.

Without this the splash is a one-way street: the machine boots with it, the
captured config spells it as a bare `splash` in `kernel_cmdline` with no block
that explains it, and re-applying that config never installs plymouth again.
"""
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction
from dasik.lib.actions.plymouth_action import PlymouthAction, plymouth_installed
from dasik.lib.target.target import Target


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


def _install_plymouth(root, theme=None):
    (root / "usr/bin").mkdir(parents=True, exist_ok=True)
    (root / "usr/bin/plymouthd").write_text("")
    if theme is not None:
        (root / "etc/plymouth").mkdir(parents=True, exist_ok=True)
        (root / "etc/plymouth/plymouthd.conf").write_text(f"[Daemon]\nTheme={theme}\n")


def _entry(root, options):
    (root / "boot/loader/entries").mkdir(parents=True, exist_ok=True)
    (root / "boot/loader/entries/arch.conf").write_text(f"title Arch\noptions {options}\n")
    (root / "boot/loader/loader.conf").write_text("default arch\n")


def test_nothing_is_invented_on_a_machine_without_plymouth(tmp_path):
    assert PlymouthAction({}, _ctx(tmp_path)).import_state() == {}


def test_an_installed_plymouth_is_captured(tmp_path):
    _install_plymouth(tmp_path)
    assert PlymouthAction({}, _ctx(tmp_path)).import_state() == {"plymouth": {}}


def test_the_theme_is_captured_from_the_daemon_config(tmp_path):
    _install_plymouth(tmp_path, theme="bgrt")
    assert PlymouthAction({}, _ctx(tmp_path)).import_state() == {"plymouth": {"theme": "bgrt"}}


def test_a_daemon_config_without_a_theme_captures_no_theme(tmp_path):
    """Reporting reality: an absent Theme= means plymouth's own default, and
    inventing a name here would pin a theme the machine never declared."""
    _install_plymouth(tmp_path)
    (tmp_path / "etc/plymouth").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/plymouth/plymouthd.conf").write_text("[Daemon]\nShowDelay=0\n")
    assert PlymouthAction({}, _ctx(tmp_path)).import_state() == {"plymouth": {}}


def test_the_probe_is_the_daemon_binary(tmp_path):
    assert plymouth_installed(Target(root=str(tmp_path))) is False
    _install_plymouth(tmp_path)
    assert plymouth_installed(Target(root=str(tmp_path))) is True


def test_the_action_converges_nothing(tmp_path):
    """Capture-only, like CpuAction: plan() exists so Reconciler.sync visits it."""
    action = PlymouthAction({}, _ctx(tmp_path))
    assert action.plan(managed=[]) == []
    assert action.managed_keys() == {}
    assert action.is_needed() is False


def test_splash_is_subtracted_when_plymouth_owns_it(tmp_path):
    _install_plymouth(tmp_path)
    _entry(tmp_path, "root=LABEL=root rw quiet splash")
    captured = KernelCmdlineAction({"bootloader": "sd-boot"}, _ctx(tmp_path)).import_state()
    assert captured["kernel_cmdline"] == ["root=LABEL=root", "rw", "quiet"]


def test_splash_without_plymouth_stays_a_plain_parameter(tmp_path):
    """sync reports reality: nobody owns this splash, so it is not swallowed —
    dropping it would silently change the machine on the next apply."""
    _entry(tmp_path, "root=LABEL=root rw splash")
    captured = KernelCmdlineAction({"bootloader": "sd-boot"}, _ctx(tmp_path)).import_state()
    assert "splash" in captured["kernel_cmdline"]

"""`sync` must capture the package behind an enabled unit, however it got there.

`pacman -Qqe` lists only EXPLICIT packages, so a service whose provider was
pulled in as a dependency is invisible to the capture. On the machine that
found this, `sddm` is a dependency of an orphaned `sddm-kcm`: nothing in the
captured config pulled it, yet `sddm.service` was enabled — so the captured
config re-installed a machine with no graphical login, and `dasik check` on it
failed with `unit_without_provider`. Thirteen of forty enabled units were in
that state (sddm, openssh, avahi, lm_sensors, pcsclite, ...).

The unit is the evidence: if it is enabled and a package owns its unit file,
that package is part of the machine and belongs in the config — as
`reason: "dep"`, which is exactly how it is installed.
"""
from unittest.mock import MagicMock, patch

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.packages_action import PackagesAction
from dasik.lib.target.target import Target


def _ctx(root: str = "/") -> ActionContext:
    return ActionContext(target=Target(root=root))


def _probes(explicit=b"", installed=None, enabled=b"", fragments=b"", owners=b"",
            fail=()):
    """Fake `Command.execute` answering each probe import_state makes."""
    installed = explicit if installed is None else installed

    def fake(cmd, args=None, *a, **kw):
        args = list(args or [])
        head = args[0] if args else ""
        if cmd in fail or head in fail:
            raise OSError(f"no {cmd}")
        if cmd == "pacman":
            out = {"-Qqe": explicit, "-Qq": installed, "-Qqm": b"",
                   "-Qqo": owners}.get(head, b"")
        elif cmd == "systemctl":
            out = {"list-unit-files": enabled, "show": fragments}.get(head, b"")
        else:
            out = b""
        return MagicMock(stdout=out, stderr=b"", returncode=0)

    return MagicMock(side_effect=fake)


def _captured(declared=(), **probes):
    fake = _probes(**probes)
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        action = PackagesAction(config=list(declared), context=_ctx("/"))
        return action.import_state(managed=list(declared)), fake


_SDDM = dict(
    explicit=b"plasma-meta\n",
    installed=b"plasma-meta\nsddm\n",
    enabled=b"sddm.service enabled enabled\n",
    fragments=b"/usr/lib/systemd/system/sddm.service\n",
    owners=b"sddm\n",
)


def test_a_dependency_behind_an_enabled_unit_is_captured():
    fragment, _ = _captured(**_SDDM)

    assert {"name": "sddm", "reason": "dep"} in fragment["packages"]


def test_the_provider_is_looked_up_by_the_unit_file_pacman_owns():
    """No hard-coded unit→package table: ask pacman who owns the unit file."""
    _, fake = _captured(**_SDDM)

    calls = [(c.args[0], list(c.args[1])) for c in fake.call_args_list
             if len(c.args) > 1]
    assert ("systemctl", ["list-unit-files", "--state=enabled", "--no-legend"]) in calls
    assert any(cmd == "pacman" and args[0] == "-Qqo"
               and "/usr/lib/systemd/system/sddm.service" in args
               for cmd, args in calls)


def test_an_explicit_provider_is_not_captured_twice():
    fragment, _ = _captured(
        explicit=b"sddm\n", installed=b"sddm\n",
        enabled=b"sddm.service enabled enabled\n",
        fragments=b"/usr/lib/systemd/system/sddm.service\n",
        owners=b"sddm\n")

    assert fragment["packages"] == ["sddm"]


def test_a_declared_provider_keeps_its_declared_entry():
    fragment, _ = _captured(declared=["sddm"], **_SDDM)

    assert fragment["packages"].count("sddm") + sum(
        1 for p in fragment["packages"]
        if isinstance(p, dict) and p["name"] == "sddm") == 1


def test_a_local_unit_no_package_owns_invents_nothing():
    """/etc/systemd/system is the admin's; pacman owns nothing there."""
    fragment, _ = _captured(
        explicit=b"plasma-meta\n", installed=b"plasma-meta\n",
        enabled=b"homemade.service enabled enabled\n",
        fragments=b"/etc/systemd/system/homemade.service\n",
        owners=b"")

    assert fragment["packages"] == ["plasma-meta"]


def test_a_unit_with_no_fragment_path_is_skipped():
    """An alias or a masked unit reports an empty FragmentPath."""
    fragment, _ = _captured(
        explicit=b"plasma-meta\n", installed=b"plasma-meta\n",
        enabled=b"ghost.service enabled enabled\n",
        fragments=b"\n", owners=b"")

    assert fragment["packages"] == ["plasma-meta"]


def test_a_template_unit_does_not_break_the_lookup():
    """`systemctl show getty@.service` fails outright and would take the whole
    batch with it, so bare templates are filtered before the query."""
    fragment, _ = _captured(
        explicit=b"plasma-meta\n", installed=b"plasma-meta\nsddm\n",
        enabled=b"getty@.service enabled enabled\nsddm.service enabled enabled\n",
        fragments=b"/usr/lib/systemd/system/sddm.service\n",
        owners=b"sddm\n")

    assert {"name": "sddm", "reason": "dep"} in fragment["packages"]


def test_a_provider_that_is_not_installed_is_not_captured():
    """Belt and braces: only what the machine really has."""
    fragment, _ = _captured(
        explicit=b"plasma-meta\n", installed=b"plasma-meta\n",
        enabled=b"sddm.service enabled enabled\n",
        fragments=b"/usr/lib/systemd/system/sddm.service\n",
        owners=b"sddm\n")

    assert fragment["packages"] == ["plasma-meta"]


def test_a_failing_probe_leaves_the_capture_alone():
    """No systemctl on the target (a fake root, a half-built /mnt) must not
    lose the packages the capture already had."""
    fragment, _ = _captured(explicit=b"plasma-meta\n", fail=("systemctl",))

    assert fragment["packages"] == ["plasma-meta"]

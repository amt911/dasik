"""Security: package names come from the user's JSON and reach a shell (AUR builds
run `su -c "git clone .../<pkg>.git ..."`) and pacman's argv (a name starting with
`-` would be parsed as a flag). PackagesAction must reject names outside the Arch
package-naming charset at construction, before any name can reach a command line.
"""
import pytest

from dasik.lib.actions.packages_action import PackagesAction
from dasik.lib.exceptions.exceptions import ConfigValidationError


@pytest.mark.parametrize("bad", [
    "foo;reboot",              # command separator
    "foo && rm -rf /",         # shell AND + spaces
    "$(reboot)",               # command substitution
    "`reboot`",                # backtick substitution
    "foo|bar",                 # pipe
    "foo>out",                 # redirection
    "foo bar",                 # whitespace
    "../evil",                 # path traversal / slash
    "foo\nbar",                # newline
])
def test_rejects_injectable_pacman_name(bad):
    with pytest.raises(ConfigValidationError):
        PackagesAction(config=[bad], context=None)


@pytest.mark.parametrize("bad", [
    "aur-foo;reboot",
    "aur-$(reboot)",
    "aur-foo bar",
])
def test_rejects_injectable_aur_name(bad):
    with pytest.raises(ConfigValidationError):
        PackagesAction(config=[bad], context=None)


def test_rejects_name_starting_with_dash():
    # `pacman -S --config=/x` style arg-injection via a package name.
    with pytest.raises(ConfigValidationError):
        PackagesAction(config=["-Rns"], context=None)


@pytest.mark.parametrize("good", [
    "git", "htop", "linux-firmware", "python-pydantic", "lib32-glibc",
    "google-chrome", "visual-studio-code-bin", "gtk+", "c++.foo",
    "foo@1.0", "base-devel", "7zip",
])
def test_accepts_valid_pacman_names(good):
    a = PackagesAction(config=[good], context=None)
    assert good in a.pacman_pkgs


@pytest.mark.parametrize("good", ["aur-downgrade", "aur-yay", "aur-google-chrome"])
def test_accepts_valid_aur_names(good):
    a = PackagesAction(config=[good], context=None)
    assert good[len("aur-"):] in a.aur_pkgs


def test_accepts_dict_entry_form():
    a = PackagesAction(config=[{"name": "git", "reason": "explicit"}], context=None)
    assert "git" in a.pacman_pkgs


def test_aur_install_without_context_raises_clean_error():
    # A missing context/target must fail with a clear error, not an opaque
    # AttributeError from dereferencing None mid-way through a root-level build.
    from dasik.lib.exceptions.exceptions import CommandExecutionError

    a = PackagesAction(config=["aur-downgrade"], context=None)
    with pytest.raises(CommandExecutionError):
        a._apply_aur_install(["downgrade"])


# --- defense-in-depth: AUR build never interpolates values into a shell string --- #

def _su_scripts_and_args(calls):
    """From recorded argv calls, return (script, positional_args) for each `su -c`."""
    out = []
    for c in calls:
        if "su" in c and "-c" in c:
            i = c.index("-c")
            out.append((c[i + 1], c[i + 2:]))
    return out


def test_legacy_aur_build_passes_values_as_positional_args(monkeypatch):
    from unittest.mock import MagicMock
    calls = []
    monkeypatch.setattr("dasik.lib.actions.packages_action.subprocess.run",
                        lambda argv, **k: (calls.append(list(argv)), MagicMock(returncode=0))[1])
    a = PackagesAction(config=["aur-foo"], context=None)
    a._install_single_aur_pkg("foo")
    su = _su_scripts_and_args(calls)
    assert su, "no `su -c` invocation recorded"
    for script, args in su:
        # the shell script is a constant; the pkg / build dir / url are NEVER in it
        assert "foo" not in script and "aur.archlinux.org" not in script
        # …they arrive as positional args instead
        assert any("foo" in x for x in args)


# NOTE: the v3 AUR build path moved to aur_installer.AurInstaller (delegated from
# _apply_aur_install). Its safe-argv guarantee is covered by
# tests/lib/actions/test_aur_installer.py::test_helper_invocation_safe_argv and
# ::test_malicious_dep_name_rejected_before_argv; the delegation itself by
# test_packages_action_v3.py::test_apply_aur_install_delegates_to_aur_installer.

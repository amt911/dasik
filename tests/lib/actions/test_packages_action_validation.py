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

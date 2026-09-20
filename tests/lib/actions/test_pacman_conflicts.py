"""Parsing pacman's own conflict verdict.

dasik must not reimplement pacman's resolver. The real case (2026-09-20,
MSI GE63) is unreachable from the metadata of the package being installed:

    $ pacman -Si libjodycode
    Conflicts With  : None

`libjodycode` declares nothing. The conflict is declared by the INSTALLED
`libjodycode-git`, and in the general case through a shared `provides`
(`openresolv` and `systemd-resolvconf` both provide `resolvconf`), so neither
side names the other. Only pacman knows, and it says so on stderr:

    :: libjodycode-4.1.2-3 and libjodycode-git-4.1.2.r12.g1f56137-1 are in conflict. Remove libjodycode-git? [y/N]
    error: unresolvable package conflicts detected

These tests pin the parsing of that text and nothing else.
"""
import pytest

from dasik.lib.actions.pacman_conflicts import PacmanConflict, parse_conflicts


REAL_GE63 = (
    ":: libjodycode-4.1.2-3 and libjodycode-git-4.1.2.r12.g1f56137-1 are in "
    "conflict. Remove libjodycode-git? [y/N] error: unresolvable package "
    "conflicts detected\n"
    "error: failed to prepare transaction (conflicting dependencies)\n"
)


def test_the_real_ge63_failure_is_parsed():
    assert parse_conflicts(REAL_GE63) == [
        PacmanConflict(left="libjodycode", right="libjodycode-git",
                       removable="libjodycode-git")
    ]


def test_versions_are_stripped_including_an_epoch():
    text = ":: iptables-1:1.8.13-1 and foo-2.0-3 are in conflict. Remove foo? [y/N]"
    assert parse_conflicts(text) == [
        PacmanConflict(left="iptables", right="foo", removable="foo")
    ]


def test_a_conflict_named_through_a_provide_has_no_removable():
    """`X and Y are in conflict (Z)`: pacman states the pair but proposes
    nothing, because neither side is installed yet."""
    text = ":: openresolv-3.16.0-1 and systemd-resolvconf-261.3-1 are in conflict (resolvconf)\n"
    assert parse_conflicts(text) == [
        PacmanConflict(left="openresolv", right="systemd-resolvconf",
                       removable=None)
    ]


def test_several_conflicts_are_all_reported_in_order():
    text = (":: a-1-1 and b-1-1 are in conflict. Remove b? [y/N]\n"
            ":: c-1-1 and d-1-1 are in conflict. Remove d? [y/N]\n")
    assert [c.removable for c in parse_conflicts(text)] == ["b", "d"]


def test_the_same_conflict_twice_is_reported_once():
    """pacman repeats itself when dasik retries package by package; the caller
    must not warn twice about one pair."""
    assert len(parse_conflicts(REAL_GE63 + REAL_GE63)) == 1


def test_output_without_a_conflict_yields_nothing():
    assert parse_conflicts("resolving dependencies...\nhttps://mirror/x.pkg.tar.zst\n") == []
    assert parse_conflicts("") == []


def test_an_unrelated_error_is_not_read_as_a_conflict():
    text = ("error: target not found: nosuchpkg\n"
            "error: failed to prepare transaction (could not satisfy dependencies)\n")
    assert parse_conflicts(text) == []


def test_bytes_are_accepted_because_that_is_what_command_returns():
    assert parse_conflicts(REAL_GE63.encode())[0].removable == "libjodycode-git"


@pytest.mark.parametrize("bad", [None, 123])
def test_a_non_text_answer_is_no_conflict_rather_than_a_crash(bad):
    """A probe that cannot answer must plan exactly as before."""
    assert parse_conflicts(bad) == []


# --------------------------------------------------------------------------
# What plan has to work from.
#
# MEASURED in the guest (2026-09-20), because this was a guess before: pacman
# has NO read-only probe that reports a conflict. `pacman -Sp`, `-S --print`
# and `-S -w` all print the package and exit 0 with an empty stderr while the
# real `-S` fails. So `apply` reads the verdict off the failure it just got,
# and `plan` — which must not run a transaction — derives it from metadata.
# --------------------------------------------------------------------------

from dasik.lib.actions.pacman_conflicts import (  # noqa: E402
    PkgInfo, conflicts_from_metadata, parse_pkg_fields,
)

SI_RESOLVCONF = """\
Name            : systemd-resolvconf
Version         : 261.3-1
Provides        : openresolv  resolvconf
Conflicts With  : resolvconf
Replaces        : None
"""

QI_MACHINE = """\
Name            : openresolv
Provides        : resolvconf
Conflicts With  : resolvconf

Name            : htop
Provides        : None
Conflicts With  : None
"""


def test_fields_are_parsed_into_packages():
    assert parse_pkg_fields(SI_RESOLVCONF) == [
        PkgInfo(name="systemd-resolvconf",
                provides=frozenset({"openresolv", "resolvconf"}),
                conflicts=frozenset({"resolvconf"}))
    ]


def test_none_means_empty_not_a_package_called_none():
    htop = parse_pkg_fields(QI_MACHINE)[1]
    assert (htop.provides, htop.conflicts) == (frozenset(), frozenset())


def test_a_version_constraint_is_stripped_from_a_token():
    """Real machines carry `Conflicts With : at-spi2-atk<=2.38.0-2  atk<=2.38.0-2`."""
    info = parse_pkg_fields(
        "Name            : x\n"
        "Provides        : libfoo.so=1-64\n"
        "Conflicts With  : at-spi2-atk<=2.38.0-2  atk>=1.0\n")[0]
    assert info.conflicts == frozenset({"at-spi2-atk", "atk"})
    assert info.provides == frozenset({"libfoo.so"})


def test_the_resolvconf_pair_is_found_through_the_shared_provide():
    """Neither package names the other: openresolv conflicts with `resolvconf`,
    which systemd-resolvconf merely PROVIDES. The VM proved pacman refuses it."""
    candidates = parse_pkg_fields(SI_RESOLVCONF)
    installed = parse_pkg_fields(QI_MACHINE)
    assert conflicts_from_metadata(candidates, installed) == [
        PacmanConflict(left="systemd-resolvconf", right="openresolv",
                       removable="openresolv")
    ]


def test_the_ge63_pair_is_found_from_the_installed_side_alone():
    """`pacman -Si libjodycode` says `Conflicts With : None`; the whole verdict
    lives in the installed libjodycode-git."""
    candidates = [PkgInfo("libjodycode", frozenset(), frozenset())]
    installed = [PkgInfo("libjodycode-git", frozenset({"libjodycode"}),
                         frozenset({"libjodycode"}))]
    assert conflicts_from_metadata(candidates, installed)[0].removable == \
        "libjodycode-git"


def test_a_candidate_that_conflicts_with_an_installed_name_is_found():
    candidates = [PkgInfo("a", frozenset(), frozenset({"b"}))]
    installed = [PkgInfo("b", frozenset(), frozenset())]
    assert conflicts_from_metadata(candidates, installed)[0].removable == "b"


def test_sharing_a_provide_without_declaring_a_conflict_is_not_one():
    """Two packages may both provide a virtual name and coexist happily; only
    a declared conflict is a conflict."""
    candidates = [PkgInfo("a", frozenset({"virt"}), frozenset())]
    installed = [PkgInfo("b", frozenset({"virt"}), frozenset())]
    assert conflicts_from_metadata(candidates, installed) == []


def test_a_package_does_not_conflict_with_itself():
    """Reinstalling or upgrading `x` is not a conflict, whatever it declares."""
    candidates = [PkgInfo("x", frozenset({"v"}), frozenset({"v"}))]
    installed = [PkgInfo("x", frozenset({"v"}), frozenset({"v"}))]
    assert conflicts_from_metadata(candidates, installed) == []


def test_nothing_installed_and_nothing_declared_is_no_conflict():
    assert conflicts_from_metadata([], []) == []
    assert parse_pkg_fields("") == []

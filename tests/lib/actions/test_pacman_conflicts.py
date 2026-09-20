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

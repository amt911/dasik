"""The shared pacman double must refuse to answer what it does not model.

This exists because of a real defect. dasik grew a `pacman -T` probe; eight
test doubles answered it with `b""`, and in pacman's language an empty deptest
answer means "everything is satisfied". Nothing failed — the doubles simply
agreed with whatever the new code asked, and the suite stayed green while the
behaviour under test was wrong. A double that answers every question is not a
double, it is a yes-man.
"""
import pytest

from tests.support.pacman import UnmodelledPacmanQuery, pacman_double


def _text(result):
    out = result.stdout
    return out.decode() if isinstance(out, bytes) else out


def test_an_unmodelled_pacman_query_raises_instead_of_answering():
    run = pacman_double(installed=["git"])
    with pytest.raises(UnmodelledPacmanQuery, match=r"-Qkk"):
        run("pacman", ["-Qkk"])


def test_the_message_names_the_flag_and_says_what_to_do():
    run = pacman_double()
    with pytest.raises(UnmodelledPacmanQuery) as e:
        run("pacman", ["-Qc", "git"])
    said = str(e.value)
    assert "-Qc" in said and "pacman_double" in said


def test_an_explicitly_empty_explicit_set_stays_empty():
    """Omitting `explicit` means "same as installed"; passing `[]` means empty.
    Folding one into the other is the double inventing an answer — it turned a
    dep-installed package into an explicit one and lost its reason."""
    run = pacman_double(installed=["linux-headers"], explicit=[])
    assert _text(run("pacman", ["-Qqe"])).split() == []
    assert _text(run("pacman", ["-Qq"])).split() == ["linux-headers"]


def test_omitting_explicit_mirrors_installed():
    run = pacman_double(installed=["git"])
    assert _text(run("pacman", ["-Qqe"])).split() == ["git"]


def test_installed_and_explicit_are_separate_answers():
    run = pacman_double(installed=["git", "vim"], explicit=["git"])
    assert _text(run("pacman", ["-Qq"])).split() == ["git", "vim"]
    assert _text(run("pacman", ["-Qqe"])).split() == ["git"]


def test_deptest_prints_what_is_not_satisfied():
    """`pacman -T` prints the UNSATISFIED names — the inversion that fooled
    eight doubles into meaning the opposite of what they said."""
    run = pacman_double(satisfied=["iptables-nft"])
    result = run("pacman", ["-T", "iptables-nft", "firefox"])
    assert _text(result).split() == ["firefox"]
    assert result.returncode == 127


def test_deptest_with_everything_satisfied_is_silent_and_zero():
    run = pacman_double(satisfied=["iptables-nft"])
    result = run("pacman", ["-T", "iptables-nft"])
    assert _text(result).strip() == ""
    assert result.returncode == 0


def test_nothing_is_satisfied_unless_the_test_says_so():
    """The default has to be the safe one: a name nobody declared satisfied
    comes back as missing, so a plan still plans it."""
    run = pacman_double(installed=["firefox"])
    assert _text(run("pacman", ["-T", "firefox"])).split() == ["firefox"]


def test_a_provider_probe_answers_only_for_declared_providers():
    run = pacman_double(provided=["iptables-nft"])
    assert run("pacman", ["-Sp", "--noconfirm", "iptables-nft"]).returncode == 0
    assert run("pacman", ["-Sp", "--noconfirm", "fierfox"]).returncode != 0


def test_mutating_operations_succeed_and_are_recorded():
    """Tests assert on what was installed; the double must not refuse those."""
    run = pacman_double()
    assert run("pacman", ["--noconfirm", "--needed", "-S", "git"]).returncode == 0
    assert ["--noconfirm", "--needed", "-S", "git"] in run.calls_for("pacman")


def test_a_non_pacman_command_is_left_alone():
    run = pacman_double()
    assert run("lsblk", ["-no", "LABEL"]).returncode == 0


def test_a_caller_can_still_override_one_command():
    run = pacman_double(other=lambda cmd, args: "vda" if cmd == "lsblk" else None)
    assert _text(run("lsblk", ["-no", "LABEL"])) == "vda"

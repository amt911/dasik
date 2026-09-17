"""Tests for the pure gpg --with-colons / *-trusted parsers.

Interface note: this module implements the CONTROLLER RULING that overrides
task-4-brief.md's single-argument ``trusted_fingerprints(colons)``. FACT-PR-2
(docs/FACTS.md) measured that the plain validity field reaches ``f`` through
ordinary web-of-trust too, so "trusted" here means *locally* signed by the
keyring's own master key: ``trusted_fingerprints`` takes a second
``master_keyids`` argument (from ``secret_keyids``) and only counts a ``sig``
whose class (field 11) ends in ``l`` and whose issuer key id is one of them.
"""
from pathlib import Path

from dasik.lib.actions.pacman_repos_state import (
    _colon_fields,
    _field_at,
    packaged_trusted,
    primary_fingerprints,
    secret_keyids,
    trusted_fingerprints,
)

FIXTURES = Path("tests/fixtures/pacman_repos")

SHOW_KEYS_AMT911 = (FIXTURES / "show-keys-amt911.txt").read_text()
LIST_KEYS_ABSENT = (FIXTURES / "list-keys-absent.txt").read_text()
LIST_KEYS_ADDED = (FIXTURES / "list-keys-added.txt").read_text()
LIST_KEYS_LSIGNED = (FIXTURES / "list-keys-lsigned.txt").read_text()
LIST_KEYS_LSIGNED_CHROOT = (FIXTURES / "list-keys-lsigned-chroot.txt").read_text()
LIST_SIGS_LSIGNED = (FIXTURES / "list-sigs-lsigned.txt").read_text()
LIST_SECRET_KEYS = (FIXTURES / "list-secret-keys.txt").read_text()
ARCHLINUX_TRUSTED_HEAD = (FIXTURES / "archlinux-trusted.head").read_text()

AMT911_FPR = "6C6568CE34894645A23ABC44B5BD6F8F9023E53B"
AMT911_SUBKEY_FPR = "5018DF894BFD550663C32381A917D03BA23DF1C8"
MASTER_KEYID = "2535D4F912C7BF3C"
MASTER_FPR = "125C4E1E31F65FC81389D9FF2535D4F912C7BF3C"
AMT911_OWN_KEYID = "B5BD6F8F9023E53B"


# --- primary_fingerprints ---------------------------------------------------


def test_primary_fingerprints_picks_the_pub_fpr_not_the_subkey():
    result = primary_fingerprints(SHOW_KEYS_AMT911)
    assert result == {AMT911_FPR}
    assert AMT911_SUBKEY_FPR not in result


def test_primary_fingerprints_empty_on_empty_input():
    assert primary_fingerprints("") == set()


def test_primary_fingerprints_ignores_garbage_and_warning_lines():
    # show-keys-amt911.txt itself starts with two "gpg: ..." lines before
    # the first pub/fpr pair — proves those don't confuse the state machine.
    assert "gpg:" in SHOW_KEYS_AMT911.splitlines()[0]
    assert primary_fingerprints(SHOW_KEYS_AMT911) == {AMT911_FPR}


# --- secret_keyids -----------------------------------------------------------


def test_secret_keyids_reads_the_master_key_from_sec_record():
    assert secret_keyids(LIST_SECRET_KEYS) == {MASTER_KEYID}


def test_secret_keyids_empty_on_empty_input():
    assert secret_keyids("") == set()


# --- trusted_fingerprints (controller-ruling interface) ----------------------


def test_trusted_fingerprints_true_local_signature_by_master():
    assert trusted_fingerprints(LIST_SIGS_LSIGNED, {MASTER_KEYID}) == {AMT911_FPR}


def test_trusted_fingerprints_empty_master_set_yields_nothing():
    assert trusted_fingerprints(LIST_SIGS_LSIGNED, set()) == set()


def test_trusted_fingerprints_self_signature_issuer_is_not_a_master_id():
    # B5BD6F8F9023E53B is the key's OWN id (its self-sigs, 13x/18x) — even
    # if mistakenly treated as a "master", those sigs are never class *l*.
    assert trusted_fingerprints(LIST_SIGS_LSIGNED, {AMT911_OWN_KEYID}) == set()


def test_trusted_fingerprints_empty_on_empty_input():
    assert trusted_fingerprints("", {MASTER_KEYID}) == set()


def test_trusted_fingerprints_ignores_garbage_and_tru_lines():
    assert LIST_SIGS_LSIGNED.splitlines()[0].startswith("gpg: WARNING")
    assert LIST_SIGS_LSIGNED.splitlines()[1].startswith("tru:")
    # already proven not to raise / not to contribute by the true-positive
    # test above; assert explicitly that garbage-only input is inert too.
    garbage = "gpg: WARNING: unsafe permissions on homedir '/x'\ntru::1:1:1:3:1:5\nnotarecord:without:enough:fields\n"
    assert trusted_fingerprints(garbage, {MASTER_KEYID}) == set()


def test_trusted_fingerprints_hand_written_local_vs_web_of_trust():
    # pub A: a genuine LOCAL (10l) sig from the master -> trusted.
    # pub B: only a web-of-trust (10x) sig from the very same master id
    #        -> NOT trusted (class doesn't end in "l").
    text = (
        "pub:-:4096:1:AAAAAAAAAAAAAAAA:1700000000:::-:::scESC::::::23::0:\n"
        "fpr:::::::::AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA:\n"
        "uid:-::::1700000000::deadbeef::Pub A <a@example.com>::::::::::0:\n"
        f"sig:::1:{MASTER_KEYID}:1700000000::::Pacman Keyring Master Key <pacman@localhost>:10l::{MASTER_FPR}:::10:\n"
        "pub:-:4096:1:BBBBBBBBBBBBBBBB:1700000000:::-:::scESC::::::23::0:\n"
        "fpr:::::::::BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB:\n"
        "uid:-::::1700000000::cafebabe::Pub B <b@example.com>::::::::::0:\n"
        f"sig:::1:{MASTER_KEYID}:1700000000::::Pacman Keyring Master Key <pacman@localhost>:10x::{MASTER_FPR}:::10:\n"
    )
    assert trusted_fingerprints(text, {MASTER_KEYID}) == {
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    }


def test_trusted_fingerprints_hand_written_local_sig_by_other_key_id():
    # A local (10l) class sig, but issued by a key id that is NOT in
    # master_keyids -> not trusted (someone else's local sig, not ours).
    text = (
        "pub:-:4096:1:AAAAAAAAAAAAAAAA:1700000000:::-:::scESC::::::23::0:\n"
        "fpr:::::::::AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA:\n"
        "uid:-::::1700000000::deadbeef::Pub A <a@example.com>::::::::::0:\n"
        "sig:::1:FFFFFFFFFFFFFFFF:1700000000::::Someone Else <c@example.com>:10l::"
        "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF:::10:\n"
    )
    assert trusted_fingerprints(text, {MASTER_KEYID}) == set()


def test_trusted_fingerprints_added_but_not_lsigned_is_empty():
    # list-keys-added.txt has no --list-sigs sig: records at all (it's a
    # --list-keys capture), so there is nothing to qualify as local.
    assert trusted_fingerprints(LIST_KEYS_ADDED, {MASTER_KEYID}) == set()


# --- packaged_trusted --------------------------------------------------------


def test_packaged_trusted_reads_fingerprints_from_trusted_file():
    # All three real lines of the fixture must be read as separate entries —
    # not just the first one, which is what a whole-file-as-one-line bug
    # (splitting on the wrong separator) would still get right.
    result = packaged_trusted([ARCHLINUX_TRUSTED_HEAD])
    assert result == {
        "2AC0A42EFB0B5CBC7A0402ED4DC95B6D7BE9892E",
        "3572FA2A1B067F22C58AF155F8B821B42A6FDCD7",
        "69E6471E3AE065297529832E6BA0F5A2037F4F41",
    }


def test_packaged_trusted_ignores_blank_and_colonless_lines():
    text = "2AC0A42EFB0B5CBC7A0402ED4DC95B6D7BE9892E:4:\n\nnotrusted\n"
    assert packaged_trusted([text]) == {"2AC0A42EFB0B5CBC7A0402ED4DC95B6D7BE9892E"}


def test_packaged_trusted_empty_on_no_texts():
    assert packaged_trusted([]) == set()


def test_packaged_trusted_skips_a_malformed_line_and_keeps_reading():
    text = "not-a-trusted-line\n" + ("A" * 40) + ":4:\n"
    assert packaged_trusted([text]) == {"A" * 40}


def test_packaged_trusted_does_not_split_on_a_tab_within_one_line():
    # A tab glued between two fingerprint entries on the SAME physical line
    # is not a second entry -- only "\n" ends a line here.
    fpr_a, fpr_b = "A" * 40, "B" * 40
    text = f"{fpr_a}:4:\t{fpr_b}:4:\n"
    assert packaged_trusted([text]) == {fpr_a}


# --- mutation-testing round: killers for pacman_repos_state.py survivors ---
# (pyproject.toml [tool.mutmut].only_mutate; see docs/mutation-testing.md).


def test_colon_fields_only_strips_a_trailing_carriage_return():
    # rstrip("\r") must not become a generic whitespace strip: a trailing
    # space in the last field is data (however unlikely in real gpg output),
    # and must survive.
    assert _colon_fields("a:b ") == ["a", "b "]


def test_colon_fields_strips_from_the_right_not_the_left():
    assert _colon_fields("a:bX\r") == ["a", "bX"]


def test_colon_fields_does_not_strip_a_trailing_x():
    assert _colon_fields("a:bX") == ["a", "bX"]


def test_field_at_is_empty_past_the_end_including_the_exact_boundary():
    fields = ["a", "b"]
    assert _field_at(fields, 2) == ""  # len(fields) == index: still out of range
    assert _field_at(fields, 5) == ""


def test_primary_fingerprints_a_whitespace_only_line_is_not_silently_dropped():
    # A line that is a single space is non-empty (truthy), so it is a real
    # ``record_type=" "`` line, not the blank-line reset -- it overwrites
    # last_type away from "pub", and the fingerprint on the next line is
    # correctly NOT attributed. Splitting on "\n" only (never all whitespace)
    # is what keeps this line from vanishing instead of being read as one.
    fpr = "A" * 40
    text = f"pub:-:4096:1:AAAA:1700000000:::-:::scESC::::::23::0:\n \nfpr:::::::::{fpr}:\n"
    assert primary_fingerprints(text) == set()


def test_primary_fingerprints_two_keys_separated_by_a_blank_line():
    fpr_a, fpr_b = "A" * 40, "B" * 40
    text = (
        "pub:-:4096:1:AAAA:1700000000:::-:::scESC::::::23::0:\n"
        f"fpr:::::::::{fpr_a}:\n"
        "\n"
        "pub:-:4096:1:BBBB:1700000000:::-:::scESC::::::23::0:\n"
        f"fpr:::::::::{fpr_b}:\n"
    )
    assert primary_fingerprints(text) == {fpr_a, fpr_b}


def test_secret_keyids_does_not_match_an_indented_sec_line():
    # secret_keyids splits strictly on "\n", so a line's own indentation is
    # part of the line and _colon_fields never sees a bare "sec" — matching
    # the convention every other parser in this module follows (split by
    # newline, never by generic whitespace).
    assert secret_keyids("  sec:-:4096:1:AAAAAAAAAAAAAAAA:1700000000::::::::::::::::0:\n") == set()


def test_secret_keyids_two_keys_separated_by_a_blank_line():
    text = (
        "sec:-:4096:1:2535D4F912C7BF3C:1700000000::::::::::::::::0:\n"
        "\n"
        "sec:-:4096:1:AAAAAAAAAAAAAAAA:1700000000::::::::::::::::0:\n"
    )
    assert secret_keyids(text) == {"2535D4F912C7BF3C", "AAAAAAAAAAAAAAAA"}


def test_trusted_fingerprints_a_lone_sig_with_no_preceding_pub_trusts_nothing():
    # No "pub"/"fpr" ever seen -> current_fpr must still be None (not the
    # empty string), or a bare local sig with no key context "trusts" "".
    text = f"sig:::1:{MASTER_KEYID}:1700000000::::Name:10l::{MASTER_FPR}:::10:\n"
    assert trusted_fingerprints(text, {MASTER_KEYID}) == set()


def test_trusted_fingerprints_an_fpr_record_with_an_empty_fingerprint_trusts_nothing():
    # A "pub" followed by an "fpr" whose fingerprint field is EMPTY (truncated
    # capture) must leave current_fpr as None: storing "" instead would let the
    # local master sig right after it add "" to the trusted set.
    text = (
        "pub:-:4096:1:AAAA:1700000000:::-:::scESC::::::23::0:\n"
        "fpr::::::::::\n"
        f"sig:::1:{MASTER_KEYID}:1700000000::::Name:10l::{MASTER_FPR}:::10:\n"
    )
    assert trusted_fingerprints(text, {MASTER_KEYID}) == set()


def test_trusted_fingerprints_a_pub_with_no_fpr_line_guards_its_own_sig():
    # A "pub" with no "fpr" record at all (malformed capture) resets
    # current_fpr to None; the sig right after it must not be attributed to
    # a fingerprint that was never read (must not become "" either).
    text = (
        "pub:-:4096:1:AAAA:1700000000:::-:::scESC::::::23::0:\n"
        f"sig:::1:{MASTER_KEYID}:1700000000::::Name:10l::{MASTER_FPR}:::10:\n"
    )
    assert trusted_fingerprints(text, {MASTER_KEYID}) == set()


def test_trusted_fingerprints_two_real_keys_separated_by_a_blank_line():
    text = (
        "pub:-:4096:1:AAAA:1700000000:::-:::scESC::::::23::0:\n"
        f"fpr:::::::::{'A' * 40}:\n"
        "uid:-::::1700000000::deadbeef::A::::::::::0:\n"
        f"sig:::1:{MASTER_KEYID}:1700000000::::Name:10l::{MASTER_FPR}:::10:\n"
        "\n"
        "pub:-:4096:1:BBBB:1700000000:::-:::scESC::::::23::0:\n"
        f"fpr:::::::::{'B' * 40}:\n"
        "uid:-::::1700000000::cafebabe::B::::::::::0:\n"
        f"sig:::1:{MASTER_KEYID}:1700000000::::Name:10l::{MASTER_FPR}:::10:\n"
    )
    assert trusted_fingerprints(text, {MASTER_KEYID}) == {"A" * 40, "B" * 40}


def test_trusted_fingerprints_a_malformed_second_key_does_not_leak_the_first():
    # Key A has NO local sig of its own (untrusted). Key B is malformed: a
    # "pub" with no "fpr" record, followed straight by a local sig. That sig
    # must never be credited to A just because current_fpr was never reset
    # by B's own (absent) "pub" handling.
    text = (
        "pub:-:4096:1:AAAA:1700000000:::-:::scESC::::::23::0:\n"
        f"fpr:::::::::{'A' * 40}:\n"
        "uid:-::::1700000000::deadbeef::A::::::::::0:\n"
        "pub:-:4096:1:BBBB:1700000000:::-:::scESC::::::23::0:\n"
        f"sig:::1:{MASTER_KEYID}:1700000000::::Name:10l::{MASTER_FPR}:::10:\n"
    )
    assert trusted_fingerprints(text, {MASTER_KEYID}) == set()


def test_trusted_fingerprints_a_uid_record_is_never_mistaken_for_an_fpr():
    # A "uid" record right after "pub" (last_type == "pub") must not be
    # treated as the key's fingerprint just because a stray field happens to
    # look like one.
    text = (
        "pub:-:4096:1:AAAA:1700000000:::-:::scESC::::::23::0:\n"
        "uid:-::::1700000000::deadbeef::SNEAKYFPRLOOKALIKEVALUEHERE1234567890AB::::::::::0:\n"
        f"sig:::1:{MASTER_KEYID}:1700000000::::Name:10l::{MASTER_FPR}:::10:\n"
    )
    assert trusted_fingerprints(text, {MASTER_KEYID}) == set()


def test_trusted_fingerprints_a_uid_record_is_never_mistaken_for_a_sig():
    # A "uid" record, even with a master-matching field 4 and a field 10
    # ending in "l" by coincidence, must not count as a trust signature --
    # only an actual "sig" record type does.
    text = (
        "pub:-:4096:1:AAAA:1700000000:::-:::scESC::::::23::0:\n"
        f"fpr:::::::::{'A' * 40}:\n"
        f"uid:a:b:c:{MASTER_KEYID}:e:f:g:h:i:10l:extra\n"
    )
    assert trusted_fingerprints(text, {MASTER_KEYID}) == set()

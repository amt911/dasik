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
    result = packaged_trusted([ARCHLINUX_TRUSTED_HEAD])
    assert "2AC0A42EFB0B5CBC7A0402ED4DC95B6D7BE9892E" in result


def test_packaged_trusted_ignores_blank_and_colonless_lines():
    text = "2AC0A42EFB0B5CBC7A0402ED4DC95B6D7BE9892E:4:\n\nnotrusted\n"
    assert packaged_trusted([text]) == {"2AC0A42EFB0B5CBC7A0402ED4DC95B6D7BE9892E"}


def test_packaged_trusted_empty_on_no_texts():
    assert packaged_trusted([]) == set()

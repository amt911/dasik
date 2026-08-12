"""The CLI's own edges, found by driving every verb in a VM.

Two warts, both small and both confusing at exactly the wrong moment:

* `hash-password` refused `--no-log`, which every other verb accepts. A script
  that passes the flag uniformly (the VM harness does, because the repo is
  mounted read-only) fails on that one verb with "unrecognized arguments".
* A typo'd verb was reported as the *no-verb* form, so `dasik aply cfg.json`
  answered with "dasik plan aply" — advice about a file that does not exist.
"""
import subprocess
import sys

import pytest


def _run(*args, stdin=""):
    return subprocess.run([sys.executable, "-m", "dasik", *args],
                          capture_output=True, text=True, input=stdin)


def test_hash_password_accepts_the_common_flags():
    r = _run("hash-password", "--no-log", stdin="hunter2\nhunter2\n")

    assert "unrecognized arguments" not in r.stderr
    assert r.returncode == 0
    assert r.stdout.strip().startswith("$")


def test_hash_password_still_hashes_with_the_chosen_method():
    r = _run("hash-password", "--method", "sha512", "--no-log",
             stdin="hunter2\nhunter2\n")

    assert r.stdout.strip().startswith("$6$")


def test_an_unknown_verb_says_so():
    r = _run("aply", "config/vm-minimal.json")

    assert r.returncode == 2
    assert "aply" in r.stderr
    combined = r.stderr.lower()
    assert "unknown verb" in combined or "invalid choice" in combined
    # and NOT the advice for the no-verb form, which names the typo as a config
    assert "dasik plan  aply" not in r.stderr


def test_the_no_verb_form_still_gets_its_own_advice():
    r = _run("config/vm-minimal.json")

    assert r.returncode == 2
    assert "no verb" in r.stderr
    assert "dasik plan  config/vm-minimal.json" in r.stderr


@pytest.mark.parametrize("verb", ["plan", "apply", "sync", "check", "generations",
                                  "rollback", "hash-password"])
def test_every_verb_advertises_the_common_flags(verb):
    r = _run(verb, "--help")

    assert r.returncode == 0
    assert "--no-log" in r.stdout

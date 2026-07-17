"""Tests for the CLI ↔ RunLogger wiring in dasik.__main__.

The CLI derives a default ``./dasik-<verb>-<date>.log`` path, honors ``--log`` /
``--no-log``, and passes ``--verbose`` through so a run's command output is both
logged to a file and (verbosely) echoed / (on failure) reddened on the console.
"""
from __future__ import annotations

import re
from argparse import Namespace

import dasik.__main__ as m
import dasik.lib.logging.run_logger as rl


def test_default_log_path_is_verb_and_timestamp_in_cwd():
    path = m._default_log_path("apply")
    assert re.fullmatch(r"dasik-apply-\d{8}-\d{6}\.log", path.name)


def test_configure_logging_defaults_to_a_file_and_passes_verbose(tmp_path, monkeypatch):
    rl.reset()
    monkeypatch.chdir(tmp_path)
    try:
        m._configure_logging(Namespace(verbose=True, verb="apply"))
        logger = rl.get()
        assert logger.verbose is True
        assert logger.log_path is not None
        assert logger.log_path.name.startswith("dasik-apply-")
    finally:
        rl.reset()


def test_configure_logging_respects_explicit_log_path(tmp_path):
    rl.reset()
    target = tmp_path / "custom.log"
    try:
        m._configure_logging(Namespace(verbose=False, verb="apply", log=str(target)))
        assert rl.get().log_path == target
    finally:
        rl.reset()


def test_configure_logging_no_log_disables_the_file(tmp_path, monkeypatch):
    rl.reset()
    monkeypatch.chdir(tmp_path)
    try:
        m._configure_logging(Namespace(verbose=False, verb="apply", no_log=True))
        assert rl.get().log_path is None
    finally:
        rl.reset()

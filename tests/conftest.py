"""Shared pytest fixtures for the dasik test suite."""
import pytest

from dasik.lib.target.target import Target


@pytest.fixture(autouse=True)
def _run_log_in_tmp(tmp_path, monkeypatch):
    """Keep the CLI's default run log out of the working tree.

    ``dasik <verb>`` writes ``./dasik-<verb>-<date>.log`` in the CURRENT
    directory, so every test that drives ``main()`` dropped a real (empty) file
    into the repo root — 348 of them had piled up by 2026-08-08. Point the
    default at the test's tmp_path instead; tests that assert on the naming
    scheme call ``_default_log_path`` directly and are unaffected.
    """
    import dasik.__main__ as main_module

    original = main_module._default_log_path        # keeps the naming scheme
    monkeypatch.setattr(
        main_module, "_default_log_path",
        lambda verb: tmp_path / original(verb).name,
    )


@pytest.fixture
def tmp_target(tmp_path):
    """A Target rooted at a temporary directory.

    Because root != "/", Target.is_chroot is True, but the state/generation
    stores only do path mapping (no chroot commands run), so this gives an
    isolated on-disk root for filesystem tests.
    """
    return Target(root=str(tmp_path))

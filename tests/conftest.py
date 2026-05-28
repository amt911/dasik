"""Shared pytest fixtures for the dasik test suite."""
import pytest

from dasik.lib.target.target import Target


@pytest.fixture
def tmp_target(tmp_path):
    """A Target rooted at a temporary directory.

    Because root != "/", Target.is_chroot is True, but the state/generation
    stores only do path mapping (no chroot commands run), so this gives an
    isolated on-disk root for filesystem tests.
    """
    return Target(root=str(tmp_path))

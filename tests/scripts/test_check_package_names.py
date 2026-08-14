"""The package-drift checker's pure halves.

The resolution itself needs pacman with synced databases (the scheduled workflow
runs in an `archlinux` container), so what is asserted here is what can be:
that the name sets it checks are the ones that matter, and that an unreachable
AUR is never reported as "the package is gone".
"""
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "check_package_names", REPO_ROOT / "scripts/check-package-names.py")
assert _SPEC and _SPEC.loader
checker = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(checker)


def test_the_derived_set_covers_the_two_names_that_broke_installs():
    """`nvidia` and `libva-mesa-driver` (#187) were derived names. Whatever
    replaced them must be in the set this job resolves, or the job cannot ever
    catch the next one."""
    derived = checker.derived_names()

    assert "nvidia-open" in derived
    assert "mesa" in derived


def test_the_derived_set_spans_both_multilib_settings():
    assert "lib32-mesa" in checker.derived_names()


def test_config_saver_is_not_expected_in_a_repo():
    """It is built from a Git PKGBUILD by design — demanding a repo would make
    the job fail forever."""
    assert "config-saver" not in checker.derived_names()


def test_every_tracked_sample_config_is_read():
    samples = checker.sample_config_names()

    assert "install-megamix.json" in samples
    names, _sources = samples["install-megamix.json"]
    assert "base" in names


def test_a_git_sourced_package_is_not_reported_as_missing():
    """`package_sources` names resolve to a repository, not to pacman."""
    samples = checker.sample_config_names()
    names, sources = samples["vm-unknown-git.json"]

    assert "config-saver" in names
    assert "config-saver" in sources


def test_an_unreachable_aur_is_not_evidence_of_absence():
    with patch.object(checker.urllib.request, "urlopen",
                      side_effect=checker.urllib.error.URLError("no dns")):
        known, reachable = checker.aur_names(["whatever"])

    assert known == set()
    assert reachable is False


@pytest.mark.parametrize("path", sorted(
    p.name for p in (REPO_ROOT / "config").glob("*.json")))
def test_every_sample_config_still_parses(path):
    json.loads((REPO_ROOT / "config" / path).read_text())

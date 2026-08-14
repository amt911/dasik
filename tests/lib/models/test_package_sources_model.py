"""Tests for package_policy + package_sources config (PLAN v3 §4)."""
import pytest
from pydantic import ValidationError

from dasik.lib.models.package_model import PackagePolicyModel, GitPackageSourceModel
from dasik.lib.models.json_model import JsonModel


def _base(**extra):
    return JsonModel(
        locales={"selected_locales": [], "desired_locale": "en_US.UTF-8",
                 "desired_tty_layout": "us"},
        timezone={"region": "Europe", "city": "Madrid"},
        network={"type": "NetworkManager", "add_default_hosts": True},
        hostname="arch",
        **extra,
    )


_SHA = "a520605367e13ec25db4c3c7e1c4bf46175ba8cd"
_SHA2 = "51d259d4fbee428a2b4eebb43caeea65079707b3"
_SHA3 = "79076f84339a7afb485b8bd11a92f0a5681b6394"


# --- PackagePolicyModel ---------------------------------------------------

def test_policy_defaults_to_warn_and_skip():
    assert PackagePolicyModel().unknown == "warn-and-skip"


def test_policy_accepts_error():
    assert PackagePolicyModel(unknown="error").unknown == "error"


def test_policy_rejects_unknown_value():
    with pytest.raises(ValidationError):
        PackagePolicyModel(unknown="ignore")


# --- GitPackageSourceModel ------------------------------------------------

def _src(**over):
    data = {
        "type": "pkgbuild-git",
        "url": "https://github.com/amt911/config-saver-aur.git",
        "ref": _SHA,
    }
    data.update(over)
    return GitPackageSourceModel(**data)


def test_git_source_valid():
    s = _src()
    assert s.type == "pkgbuild-git"
    assert s.subdir == "."


def test_git_source_subdir_kept():
    assert _src(subdir="pkg/sub").subdir == "pkg/sub"


def test_git_source_rejects_http():
    with pytest.raises(ValidationError):
        _src(url="http://github.com/amt911/config-saver-aur.git")


@pytest.mark.parametrize("url", [
    "https://gitlab.com/amt911/config-saver-aur.git",
    "https://codeberg.org/amt911/config-saver-aur.git",
    "https://git.example.org/pkgbuilds/config-saver.git",
    "https://git.example.org:8443/pkgbuilds/config-saver.git",
])
def test_git_source_accepts_any_https_host(url):
    # A PKGBUILD that was never uploaded to the AUR can live on any forge; the
    # URL only ever reaches git as a positional argument, never a shell.
    assert _src(url=url).url == url


@pytest.mark.parametrize("url", [
    "https://user:token@github.com/amt911/private.git",   # secret in the config
    "https://token@github.com/amt911/private.git",
])
def test_git_source_rejects_credentials_in_url(url):
    with pytest.raises(ValidationError):
        _src(url=url)


@pytest.mark.parametrize("url", [
    "https:///amt911/config-saver-aur.git",     # no host at all
    "https://-bad-.com/x.git",                  # not a DNS name
    "https://exa mple.com/x.git",
    "https://github.com/../../x.git",
])
def test_git_source_rejects_unusable_host_or_path(url):
    with pytest.raises(ValidationError):
        _src(url=url)


def test_git_source_rejects_missing_git_suffix():
    with pytest.raises(ValidationError):
        _src(url="https://github.com/amt911/config-saver-aur")


def test_git_source_rejects_short_sha():
    with pytest.raises(ValidationError):
        _src(ref="a520605")


def test_git_source_rejects_non_hex_sha():
    with pytest.raises(ValidationError):
        _src(ref="z" * 40)


def test_git_source_rejects_subdir_traversal():
    with pytest.raises(ValidationError):
        _src(subdir="../etc")


def test_git_source_rejects_absolute_subdir():
    with pytest.raises(ValidationError):
        _src(subdir="/etc")


def test_git_source_rejects_bad_type():
    with pytest.raises(ValidationError):
        _src(type="tarball")


# --- JsonModel integration -----------------------------------------------

def test_json_model_defaults_policy_and_empty_sources():
    m = _base()
    assert m.package_policy.unknown == "warn-and-skip"
    assert m.package_sources == {}


def test_json_model_accepts_three_sources():
    m = _base(
        packages=[
            "firefox",
            "config-saver",
            "ttf-atkinson-hyperlegible-next-nerd-git",
            "ttf-atkinson-hyperlegible-next-nerd-mono-git",
        ],
        package_sources={
            "config-saver": {
                "type": "pkgbuild-git",
                "url": "https://github.com/amt911/config-saver-aur.git",
                "ref": _SHA,
            },
            "ttf-atkinson-hyperlegible-next-nerd-git": {
                "type": "pkgbuild-git",
                "url": "https://github.com/amt911/ttf-atkinson-hyperlegible-nerd.git",
                "ref": _SHA2,
            },
            "ttf-atkinson-hyperlegible-next-nerd-mono-git": {
                "type": "pkgbuild-git",
                "url": "https://github.com/amt911/ttf-atkinson-hyperlegible-mono-nerd.git",
                "ref": _SHA3,
            },
        },
    )
    assert set(m.package_sources) == {
        "config-saver",
        "ttf-atkinson-hyperlegible-next-nerd-git",
        "ttf-atkinson-hyperlegible-next-nerd-mono-git",
    }


def test_json_model_source_key_must_be_declared_in_packages():
    with pytest.raises(ValidationError):
        _base(
            packages=["firefox"],
            package_sources={
                "config-saver": {
                    "type": "pkgbuild-git",
                    "url": "https://github.com/amt911/config-saver-aur.git",
                    "ref": _SHA,
                }
            },
        )


def test_json_model_source_key_declared_as_object_form():
    # {name, reason} form of the same package satisfies the "declared" rule.
    m = _base(
        packages=[{"name": "config-saver", "reason": "explicit"}],
        package_sources={
            "config-saver": {
                "type": "pkgbuild-git",
                "url": "https://github.com/amt911/config-saver-aur.git",
                "ref": _SHA,
            }
        },
    )
    assert "config-saver" in m.package_sources


def test_json_model_rejects_bad_source_key_grammar():
    with pytest.raises(ValidationError):
        _base(
            packages=["firefox"],
            package_sources={
                "-bad;name": {
                    "type": "pkgbuild-git",
                    "url": "https://github.com/amt911/x.git",
                    "ref": _SHA,
                }
            },
        )

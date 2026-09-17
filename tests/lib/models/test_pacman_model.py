"""Tests for pacman.repositories / pacman.keys (2026-09-16 pacman-repositories spec)."""
import pytest
from pydantic import ValidationError

from dasik.lib.models.pacman_model import PacmanModel

FPR = "6C6568CE34894645A23ABC44B5BD6F8F9023E53B"
SRV = "https://amt911.github.io/arch-packages/$arch"


def test_defaults_are_empty_lists():
    m = PacmanModel()
    assert m.repositories == [] and m.keys == []


def test_amt911_block_validates():
    m = PacmanModel(repositories=[{"name": "amt911", "sig_level": "Required", "servers": [SRV]}],
                    keys=[{"fingerprint": FPR, "url": "https://amt911.github.io/arch-packages/amt911.gpg"}])
    assert m.repositories[0].servers == [SRV]


@pytest.mark.parametrize("name", ["core", "extra", "multilib", "options", "core-testing",
                                  "extra-testing", "multilib-testing", "gnome-unstable",
                                  "kde-unstable", "testing", "community"])
def test_official_names_refused(name):
    with pytest.raises(ValidationError):
        PacmanModel(repositories=[{"name": name, "servers": [SRV]}])


@pytest.mark.parametrize("name", ["-x", "a b", "a]b", "", "a/b"])
def test_bad_names_refused(name):
    with pytest.raises(ValidationError):
        PacmanModel(repositories=[{"name": name, "servers": [SRV]}])


def test_servers_xor_include():
    with pytest.raises(ValidationError):
        PacmanModel(repositories=[{"name": "x"}])
    with pytest.raises(ValidationError):
        PacmanModel(repositories=[{"name": "x", "servers": [SRV], "include": "/etc/pacman.d/x"}])
    assert PacmanModel(repositories=[{"name": "x", "include": "/etc/pacman.d/x"}]).repositories[0].include


@pytest.mark.parametrize("url", ["http://e/$arch", "https://u:p@e/$arch", "ftp://e", "e/$arch"])
def test_bad_servers_refused(url):
    with pytest.raises(ValidationError):
        PacmanModel(repositories=[{"name": "x", "servers": [url]}])


def test_file_server_allowed():
    PacmanModel(repositories=[{"name": "x", "servers": ["file:///srv/repo/$arch"]}])


def test_relative_include_refused():
    with pytest.raises(ValidationError):
        PacmanModel(repositories=[{"name": "x", "include": "pacman.d/x"}])


@pytest.mark.parametrize("sig", ["Required", "Optional TrustAll", "PackageRequired DatabaseOptional",
                                 "Never", "TrustedOnly"])
def test_sig_level_tokens_accepted(sig):
    PacmanModel(repositories=[{"name": "x", "servers": [SRV], "sig_level": sig}])


@pytest.mark.parametrize("sig", ["Requird", "Required; rm -rf /", ""])
def test_sig_level_garbage_refused(sig):
    with pytest.raises(ValidationError):
        PacmanModel(repositories=[{"name": "x", "servers": [SRV], "sig_level": sig}])


def test_duplicate_repo_names_refused():
    with pytest.raises(ValidationError):
        PacmanModel(repositories=[{"name": "x", "servers": [SRV]}, {"name": "x", "servers": [SRV]}])


def test_fingerprint_normalized_upper():
    assert PacmanModel(keys=[{"fingerprint": FPR.lower()}]).keys[0].fingerprint == FPR


@pytest.mark.parametrize("fpr", [FPR[:-1], FPR + "0", "Z" * 40, "B5BD6F8F9023E53B"])
def test_bad_fingerprints_refused(fpr):
    with pytest.raises(ValidationError):
        PacmanModel(keys=[{"fingerprint": fpr}])


def test_duplicate_fingerprints_refused_case_insensitively():
    with pytest.raises(ValidationError):
        PacmanModel(keys=[{"fingerprint": FPR}, {"fingerprint": FPR.lower()}])


@pytest.mark.parametrize("url", ["http://e/k.gpg", "https://u:p@e/k.gpg"])
def test_bad_key_url_refused(url):
    with pytest.raises(ValidationError):
        PacmanModel(keys=[{"fingerprint": FPR, "url": url}])


def test_unknown_field_refused():
    with pytest.raises(ValidationError):
        PacmanModel(repositories=[{"name": "x", "servers": [SRV], "usage": "All"}])


# --- fix round 1: control-character injection + hostless https URLs -------

_CONTROL_CHARS = ["\n", "\r", "\t"]


@pytest.mark.parametrize("ctrl", _CONTROL_CHARS)
def test_control_chars_in_server_refused(ctrl):
    with pytest.raises(ValidationError):
        PacmanModel(repositories=[{"name": "x", "servers": [
            f"https://good.example/$arch{ctrl}SigLevel = Never"
        ]}])


@pytest.mark.parametrize("ctrl", _CONTROL_CHARS)
def test_control_chars_in_include_refused(ctrl):
    with pytest.raises(ValidationError):
        PacmanModel(repositories=[{"name": "x", "include": f"/etc/pacman.d/m{ctrl}[evil]"}])


@pytest.mark.parametrize("ctrl", _CONTROL_CHARS)
def test_control_chars_in_key_url_refused(ctrl):
    with pytest.raises(ValidationError):
        PacmanModel(keys=[{"fingerprint": FPR, "url": f"https://e.example/k.gpg{ctrl}x"}])


def test_server_newline_injection_refused():
    # Reproduces the reported config-injection payload verbatim: a Server
    # entry that would append a whole extra section to pacman.conf.
    with pytest.raises(ValidationError):
        PacmanModel(repositories=[{"name": "x", "servers": [
            "https://good.example/$arch\nSigLevel = Never\n[evil]\n"
            "Server = https://evil.example/$arch"
        ]}])


def test_include_newline_injection_refused():
    with pytest.raises(ValidationError):
        PacmanModel(repositories=[{"name": "x", "include":
                                    "/etc/pacman.d/m\n[evil]\nServer = https://e/$arch"}])


def test_key_url_newline_injection_refused():
    with pytest.raises(ValidationError):
        PacmanModel(keys=[{"fingerprint": FPR, "url": "https://e.example/k.gpg\nx"}])


@pytest.mark.parametrize("url", ["https://", "https:///k.gpg"])
def test_hostless_key_url_refused(url):
    with pytest.raises(ValidationError):
        PacmanModel(keys=[{"fingerprint": FPR, "url": url}])


@pytest.mark.parametrize("sig", ["Required\nTrustedOnly", "Required\rNever", "Required\tOptional",
                                 "Required\x7f"])
def test_sig_level_with_a_control_character_is_refused(sig):
    # `str.split()` would tokenize these into valid keywords while the original
    # value — written verbatim as `SigLevel = …` — spans two pacman.conf lines.
    with pytest.raises(ValidationError):
        PacmanModel(repositories=[{"name": "x", "servers": [SRV], "sig_level": sig}])

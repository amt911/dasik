"""Tests for PackageResolver — classify declared package names into
repo / group / AUR / unknown / unavailable WITHOUT the user encoding the origin.

pacman sync DBs are read from the target (mocked ``Command.execute``); AUR is
queried via an injected ``http_get`` so no test ever touches the network.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from dasik.lib.actions.package_resolver import (
    AurUnavailableError,
    PackageResolver,
)
from dasik.lib.exceptions.exceptions import ConfigValidationError
from dasik.lib.target.target import Target


def _pacman_db(repo=b"", groups=b"", provides=()):
    """Fake Command.execute: -Slq -> repo names, -Sgq -> group names.

    ``-Sp <name>`` is pacman resolving a name the way an install would, which
    honours ``Provides``: it succeeds for *provides* and fails otherwise.
    """
    from unittest.mock import MagicMock

    calls = []

    def fake(cmd, args=None, *a, **kw):
        args = list(args or [])
        calls.append([cmd, *args])
        flag = args[0] if args else None
        if flag == "-Sp":
            wanted = args[-1]
            return MagicMock(stdout=b"", stderr=b"",
                             returncode=0 if wanted in provides else 1)
        out = repo if flag == "-Slq" else groups if flag == "-Sgq" else b""
        return MagicMock(stdout=out, stderr=b"", returncode=0)

    fake.calls = calls  # type: ignore[attr-defined]
    return fake


def _aur_http(found):
    """An http_get returning an RPC multiinfo body listing *found* names.

    Only names that are BOTH requested (present in the url) and in *found* are
    echoed back — mirrors aurweb returning a result only for existing packages.
    """
    calls = []

    def http_get(url: str) -> bytes:
        calls.append(url)
        import urllib.parse
        requested = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("arg[]", [])
        results = [{"Name": n} for n in found if n in requested]
        return json.dumps({"type": "multiinfo", "resultcount": len(results),
                           "results": results}).encode()

    http_get.calls = calls  # type: ignore[attr-defined]
    return http_get


def _resolve(names, repo=b"", groups=b"", aur_found=(), http_get=None,
             provides=(), pacman=None):
    r = PackageResolver(http_get=http_get or _aur_http(aur_found))
    pacman = pacman or _pacman_db(repo=repo, groups=groups, provides=provides)
    with patch("dasik.lib.actions.package_resolver.Command.execute", pacman):
        return r.resolve(names, target=Target(root="/"))


def test_repo_package_resolves_to_repo_and_skips_aur():
    http = _aur_http(found=[])
    res = _resolve(["firefox"], repo=b"firefox\nvim\n", http_get=http)
    assert res.repo == ["firefox"]
    assert res.aur == [] and res.unknown == []
    assert http.calls == []  # a repo hit never queries AUR


def test_name_absent_in_repo_but_in_aur_resolves_to_aur():
    res = _resolve(["yay"], repo=b"firefox\n", aur_found=["yay"])
    assert res.aur == ["yay"]
    assert res.repo == [] and res.unknown == []


def test_repo_wins_over_aur():
    http = _aur_http(found=["firefox"])
    res = _resolve(["firefox"], repo=b"firefox\n", http_get=http)
    assert res.repo == ["firefox"]
    assert res.aur == []
    assert http.calls == []


def test_valid_group_resolves_to_group_not_aur():
    http = _aur_http(found=[])
    res = _resolve(["gnome"], repo=b"firefox\n", groups=b"gnome\nkde-applications\n",
                   http_get=http)
    assert res.groups == ["gnome"]
    assert res.aur == [] and res.unknown == []
    assert http.calls == []


def test_typo_absent_everywhere_is_unknown_not_unavailable():
    res = _resolve(["antigravity"], repo=b"firefox\n", aur_found=[])
    assert res.unknown == ["antigravity"]
    assert res.unavailable == []
    assert res.ok is False


def test_a_name_that_only_exists_as_a_provides_resolves_to_repo():
    """`iptables-nft` stopped being a package: iptables in core carries
    `Provides: iptables-nft` and `Replaces: iptables-nft`. `pacman -Slq` lists
    NAMES, so dasik called it sourceless and skipped it —

        [WARNING] packages skipped because no source was found: …, iptables-nft

    while `pacman -S iptables-nft` installs the provider without blinking.
    """
    res = _resolve(["iptables-nft"], repo=b"iptables\n", provides=["iptables-nft"])
    assert res.repo == ["iptables-nft"]
    assert res.unknown == [] and res.aur == []


def test_a_name_nothing_provides_is_still_unknown():
    """The probe must not turn every typo into a package."""
    res = _resolve(["fierfox"], repo=b"firefox\n", provides=[])
    assert res.unknown == ["fierfox"]
    assert res.repo == []


def test_the_provides_probe_only_runs_for_names_nothing_else_claimed():
    """One pacman call per leftover name is fine; one per declared package is
    not — a 300-package config would pay it 300 times for nothing."""
    pacman = _pacman_db(repo=b"firefox\n", provides=["iptables-nft"])
    res = _resolve(["firefox", "yay", "iptables-nft"], aur_found=["yay"], pacman=pacman)
    probed = [c[-1] for c in pacman.calls if c[1:2] == ["-Sp"]]
    assert probed == ["iptables-nft"]
    assert res.repo == ["firefox", "iptables-nft"] and res.aur == ["yay"]


def test_aur_network_failure_marks_unavailable_not_unknown():
    def boom(url):
        raise AurUnavailableError("timeout")

    res = _resolve(["someaurpkg"], repo=b"firefox\n", http_get=boom)
    assert res.unavailable == ["someaurpkg"]
    assert res.unknown == []
    assert res.ok is False


def test_unsolicited_rpc_name_is_ignored():
    def http_get(url):
        # aurweb returns a name we never asked for — must not be trusted
        return json.dumps({"results": [{"Name": "evil"}, {"Name": "yay"}]}).encode()

    res = _resolve(["yay"], repo=b"", http_get=http_get)
    assert res.aur == ["yay"]
    assert "evil" not in res.aur + res.repo + res.groups


def test_more_names_than_fit_one_uri_are_batched_and_unioned():
    # long names force several requests under the URI char cap
    names = [f"aurpkg{i}-{'x' * 80}" for i in range(60)]
    http = _aur_http(found=names)
    res = _resolve(names, repo=b"", http_get=http)
    assert sorted(res.aur) == sorted(names)
    assert len(http.calls) >= 2  # split across multiple requests


def test_duplicate_names_resolve_once_in_declared_order():
    res = _resolve(["vim", "firefox", "vim"], repo=b"vim\nfirefox\n")
    assert res.repo == ["vim", "firefox"]


def test_invalid_name_raises_before_any_query():
    with pytest.raises(ConfigValidationError):
        _resolve(["-rf; rm"], repo=b"firefox\n")


def test_aur_info_raises_unavailable_on_bad_json():
    r = PackageResolver(http_get=lambda url: b"<html>502 Bad Gateway</html>")
    with pytest.raises(AurUnavailableError):
        r.aur_info(["yay"])

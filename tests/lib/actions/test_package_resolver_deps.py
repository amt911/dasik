"""Tests for PackageResolver.aur_depends / aur_providers — the RPC surface the
transitive-closure validator walks.

Everything goes through an injected ``http_get``; no test touches the network.
``aur_depends`` answers "does each name exist, and what does it depend on" in
one batched round; ``aur_providers`` answers "which AUR packages provide this
name" (sonames included) via the rpc/v5 search endpoint.
"""
from __future__ import annotations

import json
import urllib.parse

import pytest

from dasik.lib.actions.package_resolver import (
    AurUnavailableError,
    PackageResolver,
)
from dasik.lib.exceptions.exceptions import ConfigValidationError


def _deps_http(packages):
    """An http_get serving rpc/v5/info from *packages*: {name: {field: [...]}}.

    Only names both requested in the URL and present in *packages* are echoed,
    mirroring aurweb answering solely for existing packages.
    """
    calls = []

    def http_get(url: str) -> bytes:
        calls.append(url)
        requested = urllib.parse.parse_qs(
            urllib.parse.urlparse(url).query).get("arg[]", [])
        results = [{"Name": n, **packages[n]} for n in requested
                   if n in packages]
        return json.dumps({"type": "multiinfo", "resultcount": len(results),
                           "results": results}).encode()

    http_get.calls = calls  # type: ignore[attr-defined]
    return http_get


def _providers_http(providers):
    """An http_get serving rpc/v5/search?by=provides from *providers*:
    {searched-name: [provider-name, ...]}."""
    calls = []

    def http_get(url: str) -> bytes:
        calls.append(url)
        parsed = urllib.parse.urlparse(url)
        searched = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1])
        names = providers.get(searched, [])
        return json.dumps({"type": "search", "resultcount": len(names),
                           "results": [{"Name": n} for n in names]}).encode()

    http_get.calls = calls  # type: ignore[attr-defined]
    return http_get


# -- aur_depends -----------------------------------------------------------

def test_aur_depends_merges_depends_makedepends_and_checkdepends():
    http = _deps_http({"lib32-ffmpeg": {
        "Depends": ["lib32-glibc", "lib32-libdav1d"],
        "MakeDepends": ["nasm"],
        "CheckDepends": ["lib32-check"],
    }})
    r = PackageResolver(http_get=http)
    deps = r.aur_depends(["lib32-ffmpeg"])
    assert deps == {"lib32-ffmpeg": ["lib32-glibc", "lib32-libdav1d",
                                     "nasm", "lib32-check"]}


def test_aur_depends_tolerates_absent_dep_fields():
    http = _deps_http({"tiny": {}})
    r = PackageResolver(http_get=http)
    assert r.aur_depends(["tiny"]) == {"tiny": []}


def test_aur_depends_omits_names_the_aur_does_not_have():
    http = _deps_http({"real": {"Depends": ["glibc"]}})
    r = PackageResolver(http_get=http)
    deps = r.aur_depends(["real", "lib32-libdav1d"])
    assert deps == {"real": ["glibc"]}
    assert "lib32-libdav1d" not in deps


def test_aur_depends_ignores_unsolicited_names():
    def http_get(url):
        return json.dumps({"results": [
            {"Name": "evil", "Depends": ["rootkit"]},
            {"Name": "yay", "Depends": ["go"]},
        ]}).encode()

    r = PackageResolver(http_get=http_get)
    assert r.aur_depends(["yay"]) == {"yay": ["go"]}


def test_aur_depends_batches_under_the_uri_cap():
    names = [f"aurpkg{i}-{'x' * 80}" for i in range(60)]
    http = _deps_http({n: {"Depends": []} for n in names})
    r = PackageResolver(http_get=http)
    deps = r.aur_depends(names)
    assert sorted(deps) == sorted(names)
    assert len(http.calls) >= 2


def test_aur_depends_memoizes_across_calls():
    http = _deps_http({"a": {"Depends": ["glibc"]}, "b": {"Depends": []}})
    r = PackageResolver(http_get=http)
    assert r.aur_depends(["a"]) == {"a": ["glibc"]}
    first = len(http.calls)
    # 'a' (found) again and a second call for 'b': only 'b' may hit HTTP,
    # and its URL must not re-request 'a'.
    assert r.aur_depends(["a", "b"]) == {"a": ["glibc"], "b": []}
    assert len(http.calls) == first + 1
    assert "a" not in urllib.parse.parse_qs(
        urllib.parse.urlparse(http.calls[-1]).query).get("arg[]", [])
    # confirmed-absent is memoized too, not retried
    assert r.aur_depends(["missing"]) == {}
    absent_calls = len(http.calls)
    assert r.aur_depends(["missing"]) == {}
    assert len(http.calls) == absent_calls


def test_aur_depends_fully_cached_call_makes_no_http_request():
    http = _deps_http({"a": {"Depends": []}})
    r = PackageResolver(http_get=http)
    r.aur_depends(["a"])
    before = len(http.calls)
    r.aur_depends(["a"])
    assert len(http.calls) == before


def test_aur_depends_bad_json_raises_unavailable():
    r = PackageResolver(http_get=lambda url: b"<html>502</html>")
    with pytest.raises(AurUnavailableError):
        r.aur_depends(["yay"])


def test_aur_depends_rejects_metacharacter_names():
    r = PackageResolver(http_get=_deps_http({}))
    with pytest.raises(ConfigValidationError):
        r.aur_depends(["-rf; rm"])


# -- aur_providers ---------------------------------------------------------

def test_aur_providers_hits_the_search_by_provides_endpoint():
    http = _providers_http({"libdav1d.so": ["dav1d-git", "lib32-dav1d"]})
    r = PackageResolver(http_get=http)
    assert r.aur_providers("libdav1d.so") == ["dav1d-git", "lib32-dav1d"]
    assert len(http.calls) == 1
    url = http.calls[0]
    assert url.startswith("https://aur.archlinux.org/rpc/v5/search/")
    assert urllib.parse.urlparse(url).query == "by=provides"
    assert "libdav1d.so" in urllib.parse.unquote(url)


def test_aur_providers_empty_when_nothing_provides_the_name():
    http = _providers_http({})
    r = PackageResolver(http_get=http)
    assert r.aur_providers("lib32-libdav1d") == []


def test_aur_providers_short_names_return_empty_without_http():
    # aurweb rejects search args shorter than 2 chars; that is a client-side
    # limitation, not an outage, so it must not raise AurUnavailableError.
    http = _providers_http({"r": ["r-git"]})
    r = PackageResolver(http_get=http)
    assert r.aur_providers("r") == []
    assert http.calls == []


def test_aur_providers_memoizes_per_name():
    http = _providers_http({"libfoo.so": ["foo"]})
    r = PackageResolver(http_get=http)
    assert r.aur_providers("libfoo.so") == ["foo"]
    assert r.aur_providers("libfoo.so") == ["foo"]
    assert len(http.calls) == 1


def test_aur_providers_bad_json_raises_unavailable():
    r = PackageResolver(http_get=lambda url: b"not json")
    with pytest.raises(AurUnavailableError):
        r.aur_providers("libfoo.so")


def test_aur_providers_rejects_metacharacter_names():
    r = PackageResolver(http_get=_providers_http({}))
    with pytest.raises(ConfigValidationError):
        r.aur_providers("$(evil)")

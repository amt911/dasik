"""PackageResolver with explicit Git PKGBUILD sources (PLAN v3 §6).

Precedence: repo > group > package_sources (git) > AUR > unknown. A name with a
declared Git source is never sent to the AUR RPC.
"""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

from dasik.lib.actions.package_resolver import PackageResolver, ResolvedGitPackage
from dasik.lib.target.target import Target


_SRC = {
    "type": "pkgbuild-git",
    "url": "https://github.com/amt911/config-saver-aur.git",
    "ref": "a520605367e13ec25db4c3c7e1c4bf46175ba8cd",
    "subdir": ".",
}


def _pacman_db(repo=b"", groups=b"", provides=()):
    """``-Slq`` -> repo names, ``-Sgq`` -> groups, ``-Sp <name>`` -> does pacman
    resolve this name (honouring ``Provides``)? Nothing provides anything here
    unless a test says so: a double that answered 0 to every command would make
    the resolver's provides probe succeed for typos too."""
    def fake(cmd, args=None, *a, **kw):
        args = list(args or [])
        flag = args[0] if args else None
        if flag == "-Sp":
            return MagicMock(stdout=b"", stderr=b"",
                             returncode=0 if args[-1] in provides else 1)
        out = repo if flag == "-Slq" else groups if flag == "-Sgq" else b""
        return MagicMock(stdout=out, stderr=b"", returncode=0)
    return fake


def _aur_http(found):
    calls = []

    def http_get(url: str) -> bytes:
        calls.append(url)
        import urllib.parse
        requested = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("arg[]", [])
        results = [{"Name": n} for n in found if n in requested]
        return json.dumps({"results": results}).encode()

    http_get.calls = calls  # type: ignore[attr-defined]
    return http_get


def _resolve(names, sources, repo=b"", groups=b"", aur_found=(), http_get=None):
    r = PackageResolver(http_get=http_get or _aur_http(aur_found))
    with patch("dasik.lib.actions.package_resolver.Command.execute",
               _pacman_db(repo=repo, groups=groups)):
        return r.resolve(names, target=Target(root="/"), sources=sources)


def test_git_sourced_name_goes_to_git_bucket():
    res = _resolve(["config-saver"], sources={"config-saver": _SRC})
    assert [g.name for g in res.git] == ["config-saver"]
    assert res.aur == [] and res.unknown == []
    assert res.ok is True


def test_git_bucket_carries_source_metadata():
    res = _resolve(["config-saver"], sources={"config-saver": _SRC})
    assert isinstance(res.git[0], ResolvedGitPackage)
    assert res.git[0].source == _SRC


def test_git_sourced_name_never_queries_aur():
    http = _aur_http(found=["config-saver"])
    res = _resolve(["config-saver"], sources={"config-saver": _SRC}, http_get=http)
    assert [g.name for g in res.git] == ["config-saver"]
    assert http.calls == []          # not sent to the AUR RPC
    assert res.aur == []


def test_repo_wins_over_git_source():
    res = _resolve(["config-saver"], sources={"config-saver": _SRC},
                   repo=b"config-saver\n")
    assert res.repo == ["config-saver"]
    assert res.git == []


def test_group_wins_over_git_source():
    res = _resolve(["config-saver"], sources={"config-saver": _SRC},
                   groups=b"config-saver\n")
    assert res.groups == ["config-saver"]
    assert res.git == []


def test_git_source_wins_over_aur():
    # name exists in AUR too, but the explicit source is chosen (and AUR skipped)
    http = _aur_http(found=["config-saver"])
    res = _resolve(["config-saver"], sources={"config-saver": _SRC}, http_get=http)
    assert [g.name for g in res.git] == ["config-saver"]
    assert res.aur == []
    assert http.calls == []


def test_mixed_repo_git_and_aur():
    http = _aur_http(found=["yay"])
    res = _resolve(
        ["firefox", "config-saver", "yay", "nope"],
        sources={"config-saver": _SRC},
        repo=b"firefox\n",
        http_get=http,
    )
    assert res.repo == ["firefox"]
    assert [g.name for g in res.git] == ["config-saver"]
    assert res.aur == ["yay"]
    assert res.unknown == ["nope"]
    # only firefox(repo)/config-saver(git) are excluded from the AUR query
    import urllib.parse
    queried = urllib.parse.parse_qs(urllib.parse.urlparse(http.calls[0]).query)["arg[]"]
    assert set(queried) == {"yay", "nope"}


def test_no_sources_arg_behaves_like_before():
    res = _resolve(["firefox"], sources=None, repo=b"firefox\n")
    assert res.repo == ["firefox"]
    assert res.git == []

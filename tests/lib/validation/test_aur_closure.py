"""Tests for validate_aur_closure — walk the transitive dependency closure of
the declared AUR packages BEFORE anything mutates, and name every chain that
ends in a dependency nothing can satisfy.

This is the regression suite for the 2026-08-18 incident: lib32-gst-libav →
lib32-ffmpeg → lib32-libdav1d (a name that exists nowhere) killed a 27-package
yay transaction 25 minutes into an install.

pacman is the strict double; the AUR is an injected http_get serving both the
info and the search?by=provides endpoints. No network, no disk.
"""
from __future__ import annotations

import json
import urllib.parse
from unittest.mock import patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dasik.lib.actions.package_resolver import (
    AurUnavailableError,
    PackageResolver,
)
from dasik.lib.exceptions.exceptions import CommandExecutionError
from dasik.lib.target.target import Target
from dasik.lib.validation.aur_closure import (
    BrokenDep,
    strip_version_constraint,
    validate_aur_closure,
)
from tests.support.pacman import pacman_double


def _aur_http(packages=None, providers=None):
    """One http_get serving both RPC endpoints.

    *packages*: {name: [dep spec, …]} — what rpc/v5/info knows.
    *providers*: {searched-name: [provider, …]} — what search?by=provides knows.
    """
    packages = packages or {}
    providers = providers or {}
    calls = []

    def http_get(url: str) -> bytes:
        calls.append(url)
        parsed = urllib.parse.urlparse(url)
        if "/rpc/v5/info" in parsed.path:
            requested = urllib.parse.parse_qs(parsed.query).get("arg[]", [])
            results = [{"Name": n, "Depends": packages[n]} for n in requested
                       if n in packages]
        else:
            searched = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1])
            results = [{"Name": n} for n in providers.get(searched, [])]
        return json.dumps({"resultcount": len(results),
                           "results": results}).encode()

    http_get.calls = calls  # type: ignore[attr-defined]
    return http_get


def _validate(roots, *, packages=None, providers=None, repo=(), satisfied=(),
              provided=(), pacman=None, http_get=None, **kw):
    http = http_get or _aur_http(packages, providers)
    resolver = PackageResolver(http_get=http)
    pacman = pacman or pacman_double(repo=list(repo), satisfied=list(satisfied),
                                     provided=list(provided))
    with patch("dasik.lib.actions.package_resolver.Command.execute", pacman), \
         patch("dasik.lib.validation.aur_closure.Command.execute", pacman):
        broken = validate_aur_closure(roots, resolver, Target(root="/"), **kw)
    return broken, http, pacman


# -- strip_version_constraint ---------------------------------------------

def test_strip_version_constraint_handles_epoch_and_soname_forms():
    assert strip_version_constraint("ffmpeg>=2:8.1.2") == "ffmpeg"
    assert strip_version_constraint("lib32-x264>=3:0.161") == "lib32-x264"
    assert strip_version_constraint("libfoo.so=7-64") == "libfoo.so"
    assert strip_version_constraint("glibc<3") == "glibc"
    assert strip_version_constraint("plain-name") == "plain-name"


# -- the incident ----------------------------------------------------------

def test_the_incident_chain_is_reported_with_the_exact_path():
    broken, _, _ = _validate(
        ["lib32-gst-libav"],
        packages={
            "lib32-gst-libav": ["lib32-ffmpeg"],
            "lib32-ffmpeg": ["lib32-libdav1d", "lib32-glibc"],
        },
        repo=["lib32-glibc"],
    )
    assert len(broken) == 1
    b = broken[0]
    assert b.chain == ("lib32-gst-libav", "lib32-ffmpeg", "lib32-libdav1d")
    assert b.spec == "lib32-libdav1d"
    assert b.render() == (
        "lib32-gst-libav → lib32-ffmpeg → lib32-libdav1d: not in the "
        "configured repos, not in the AUR, and no repo or AUR package "
        "provides it"
    )


# -- clean closures --------------------------------------------------------

def test_repo_satisfied_deps_produce_no_findings_and_batch_the_probes():
    broken, _, pacman = _validate(
        ["yay"], packages={"yay": ["go", "git"]}, repo=["go", "git"],
    )
    assert broken == []
    assert pacman.calls_for("pacman").count(["-Slq"]) == 1
    t_calls = [c for c in pacman.calls_for("pacman") if c[:1] == ["-T"]]
    assert len(t_calls) == 1 and set(t_calls[0][1:]) == {"go", "git"}


def test_version_qualified_dep_is_stripped_for_lookup_but_probed_in_full():
    broken, _, pacman = _validate(
        ["stew"], packages={"stew": ["gtk2>=2:8.1.2"]}, repo=["gtk2"],
    )
    assert broken == []
    t_calls = [c for c in pacman.calls_for("pacman") if c[:1] == ["-T"]]
    assert t_calls and "gtk2>=2:8.1.2" in t_calls[0]


def test_soname_dep_satisfied_only_by_an_aur_provider_is_clean():
    broken, http, _ = _validate(
        ["mpv-git"],
        packages={"mpv-git": ["libdav1d.so"]},
        providers={"libdav1d.so": ["lib32-dav1d", "dav1d-git"]},
    )
    assert broken == []
    assert any("by=provides" in u for u in http.calls)


def test_versioned_soname_dep_uses_the_bare_soname_for_provider_lookup():
    broken, _, _ = _validate(
        ["mpv-git"],
        packages={"mpv-git": ["libplacebo.so=349-64"]},
        providers={"libplacebo.so": ["libplacebo-git"]},
    )
    assert broken == []


def test_repo_virtual_name_satisfied_via_provider_probe_is_clean():
    broken, _, pacman = _validate(
        ["shfmt-git"], packages={"shfmt-git": ["sh"]}, provided=["sh"],
    )
    assert broken == []
    assert ["-Sp", "--noconfirm", "sh"] in pacman.calls_for("pacman")


def test_target_satisfied_dep_short_circuits_before_any_aur_query():
    broken, http, _ = _validate(
        ["tool"], packages={"tool": ["already-there"]},
        satisfied=["already-there"],
    )
    assert broken == []
    # only the root's own info query; the satisfied dep is never looked up
    assert len(http.calls) == 1


def test_a_dep_that_is_itself_an_aur_package_recurses_into_it():
    broken, _, _ = _validate(
        ["outer"],
        packages={"outer": ["inner"], "inner": ["glibc"]},
        repo=["glibc"],
    )
    assert broken == []


# -- broken closures -------------------------------------------------------

def test_every_broken_chain_is_reported_not_just_the_first():
    broken, _, _ = _validate(
        ["a"],
        packages={"a": ["gone-one", "b"], "b": ["gone-two"]},
    )
    rendered = {b.render() for b in broken}
    assert len(broken) == 2
    assert any(b.chain == ("a", "gone-one") for b in broken)
    assert any(b.chain == ("a", "b", "gone-two") for b in broken)
    assert all("not in the AUR" in r for r in rendered)


def test_a_root_that_vanished_from_the_aur_is_reported_alone():
    broken, _, _ = _validate(["ghost"], packages={})
    assert len(broken) == 1
    assert broken[0].chain == ("ghost",)
    assert "no longer in the AUR" in broken[0].detail


def test_a_diamond_dep_is_queried_once_and_reported_once_with_shortest_chain():
    broken, http, _ = _validate(
        ["root"],
        packages={
            "root": ["mid", "shared"],
            "mid": ["shared"],
        },
    )
    assert len(broken) == 1
    assert broken[0].chain == ("root", "shared")
    shared_lookups = [
        u for u in http.calls
        if "shared" in urllib.parse.parse_qs(
            urllib.parse.urlparse(u).query).get("arg[]", [])
    ]
    assert len(shared_lookups) == 1


def test_a_dependency_cycle_terminates_with_each_node_expanded_once():
    broken, http, _ = _validate(
        ["a"],
        packages={"a": ["b"], "b": ["a", "glibc"]},
        repo=["glibc"],
    )
    assert broken == []
    assert len(http.calls) <= 3


def test_the_node_cap_aborts_loudly():
    packages = {"n0": ["n1"]}
    for i in range(1, 30):
        packages[f"n{i}"] = [f"n{i + 1}"]
    packages["n30"] = []
    with pytest.raises(CommandExecutionError, match="10"):
        _validate(["n0"], packages=packages, max_nodes=10)


def test_aur_unavailable_propagates_untouched():
    def boom(url):
        raise AurUnavailableError("timeout")

    with pytest.raises(AurUnavailableError):
        _validate(["yay"], http_get=boom)


def test_an_unanswerable_pacman_t_is_never_read_as_satisfied():
    """A pacman -T that errors out (not the 0/127 protocol) must fall through
    to the repo/AUR checks, not silently bless every dep on that level."""
    inner = pacman_double(repo=["go"])

    def pacman(cmd, args=None, *a, **kw):
        argv = list(cmd) if isinstance(cmd, (list, tuple)) else [cmd, *(args or [])]
        if "pacman" in argv and "-T" in argv:
            from unittest.mock import MagicMock
            return MagicMock(stdout=b"", stderr=b"db locked", returncode=1)
        return inner(cmd, args, *a, **kw)

    pacman.calls = inner.calls  # type: ignore[attr-defined]
    pacman.calls_for = inner.calls_for  # type: ignore[attr-defined]
    broken, _, _ = _validate(["yay"], packages={"yay": ["go", "vanished"]},
                             pacman=pacman)
    assert [b.chain for b in broken] == [("yay", "vanished")]


def test_no_roots_touch_nothing():
    broken, http, pacman = _validate([], packages={})
    assert broken == []
    assert http.calls == [] and pacman.calls == []


# -- property: satisfiable closures are quiet, poisoned ones are not -------

@st.composite
def _dep_graphs(draw):
    n = draw(st.integers(min_value=1, max_value=12))
    names = [f"aur{i}" for i in range(n)]
    packages = {}
    for i, name in enumerate(names):
        deps = []
        for j in range(n):
            if i != j and draw(st.booleans()):
                deps.append(names[j])  # forward AND back edges: cycles allowed
        if draw(st.booleans()):
            deps.append("repopkg")
        packages[name] = deps
    roots = draw(st.lists(st.sampled_from(names), min_size=1, max_size=3,
                          unique=True))
    return packages, roots


@settings(max_examples=40, deadline=None)
@given(_dep_graphs())
def test_property_satisfiable_graphs_are_quiet(graph):
    packages, roots = graph
    broken, _, _ = _validate(roots, packages=packages, repo=["repopkg"])
    assert broken == []


@settings(max_examples=40, deadline=None)
@given(_dep_graphs(), st.data())
def test_property_poisoning_one_leaf_reports_that_leaf(graph, data):
    packages, roots = graph
    victim = data.draw(st.sampled_from(sorted(packages)))
    packages[victim] = packages[victim] + ["poison-pkg"]
    broken, _, _ = _validate(roots, packages=packages, repo=["repopkg"])
    reachable = _reaches(packages, roots, victim)
    if reachable:
        assert any(b.chain[-1] == "poison-pkg" for b in broken)
        assert all(b.chain[-1] == "poison-pkg" for b in broken)
    else:
        assert broken == []


def _reaches(packages, roots, victim):
    seen, frontier = set(roots), list(roots)
    while frontier:
        node = frontier.pop()
        if node == victim:
            return True
        for dep in packages.get(node, ()):
            if dep in packages and dep not in seen:
                seen.add(dep)
                frontier.append(dep)
    return False


def test_broken_dep_is_frozen_and_hashable():
    b = BrokenDep(chain=("a", "b"), spec="b>=1", detail="x")
    assert {b}
    with pytest.raises(Exception):
        b.spec = "other"  # type: ignore[misc]

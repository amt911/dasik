"""Read pacman's conflict verdict instead of recomputing it.

Whether two packages conflict is not derivable from the metadata of the one
being installed. On 2026-09-20 an apply on the MSI GE63 died on::

    :: libjodycode-4.1.2-3 and libjodycode-git-…-1 are in conflict. Remove libjodycode-git? [y/N]
    error: unresolvable package conflicts detected

while ``pacman -Si libjodycode`` reports ``Conflicts With : None`` — the
conflict is declared by the installed side, and in the general case by neither
side directly but through a ``provides`` both claim. pacman owns that answer;
this module only parses it, so dasik and pacman can never disagree.
"""
from dataclasses import dataclass, field
import re

__all__ = ["PacmanConflict", "PkgInfo", "conflicts_from_metadata",
           "parse_conflicts", "parse_pkg_fields", "strip_version"]

# ":: <pkgver> and <pkgver> are in conflict[. Remove <name>?|( <provide>)]"
_LINE = re.compile(
    r"::\s+(?P<left>\S+)\s+and\s+(?P<right>\S+)\s+are in conflict"
    r"(?:\.\s*Remove\s+(?P<removable>\S+?)\?)?"
)
# "1:1.8.13-1" / "4.1.2.r12.g1f56137-1": pkgrel and pkgver, epoch rides pkgver.
_VERSION = re.compile(r"-[^-\s]+-[^-\s]+$")


@dataclass(frozen=True)
class PacmanConflict:
    """One ``X and Y are in conflict`` verdict, names stripped of versions.

    ``removable`` is the package pacman itself proposed removing (the installed
    side of the pair), or ``None`` when it proposed nothing — which it does when
    the conflict is between two packages that are both merely being installed.
    """
    left: str
    right: str
    removable: "str | None" = None


def strip_version(token: str) -> str:
    """``libjodycode-4.1.2-3`` -> ``libjodycode``; a bare name is unchanged."""
    return _VERSION.sub("", token)


def parse_conflicts(output) -> "list[PacmanConflict]":
    """Every conflict pacman reported in *output*, in order, without repeats.

    Anything that is not pacman's conflict line — a missing target, a
    dependency failure, an empty or non-text answer from a probe that could not
    run — yields no conflict, so the caller behaves exactly as it did before.
    """
    if isinstance(output, (bytes, bytearray)):
        output = output.decode("utf-8", errors="replace")
    if not isinstance(output, str):
        return []
    seen: list = []
    for m in _LINE.finditer(output):
        conflict = PacmanConflict(
            left=strip_version(m.group("left")),
            right=strip_version(m.group("right")),
            removable=(strip_version(m.group("removable"))
                       if m.group("removable") else None),
        )
        if conflict not in seen:
            seen.append(conflict)
    return seen


# ---------------------------------------------------------------------------
# What `plan` has to work from.
#
# MEASURED in a guest on 2026-09-20, because the first implementation guessed:
# pacman has NO read-only probe that reports a conflict. `pacman -Sp`,
# `-S --print` and `-S -w` each printed the package and exited 0 with an empty
# stderr while the real `-S` failed on the same machine, same transaction. So
# `apply` reads the verdict off the failure pacman just handed it (above), and
# `plan` — which must run no transaction at all — derives it from the metadata
# of both sides.
#
# This is not a second resolver: it answers one question pacman answers the
# same way, "does either side DECLARE a conflict the other satisfies?", and it
# only ever produces a warning. Version constraints are stripped rather than
# compared, which can only widen the answer (a warning about a pair that would
# in fact have installed), never hide one.
# ---------------------------------------------------------------------------

# "at-spi2-atk<=2.38.0-2", "libfoo.so=1-64" -> the bare name.
_CONSTRAINT = re.compile(r"[<>=].*$")


@dataclass(frozen=True)
class PkgInfo:
    """The three fields of a `pacman -Si`/`-Qi` block a conflict depends on."""
    name: str
    provides: frozenset = field(default_factory=frozenset)
    conflicts: frozenset = field(default_factory=frozenset)


def _tokens(value: str) -> frozenset:
    if value in ("", "None"):
        return frozenset()
    return frozenset(_CONSTRAINT.sub("", t) for t in value.split() if t)


def parse_pkg_fields(output) -> "list[PkgInfo]":
    """Every package block in a `pacman -Si`/`-Qi` dump, in order."""
    if isinstance(output, (bytes, bytearray)):
        output = output.decode("utf-8", errors="replace")
    if not isinstance(output, str):
        return []
    packages: list[PkgInfo] = []
    name = None
    provides: frozenset = frozenset()
    conflicts: frozenset = frozenset()

    def flush():
        if name:
            packages.append(PkgInfo(name, provides, conflicts))

    for line in output.splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if key == "Name":
            flush()
            name, provides, conflicts = value, frozenset(), frozenset()
        elif key == "Provides":
            provides = _tokens(value)
        elif key == "Conflicts With":
            conflicts = _tokens(value)
    flush()
    return packages


def conflicts_from_metadata(candidates: "list[PkgInfo]",
                            installed: "list[PkgInfo]") -> "list[PacmanConflict]":
    """Pairs where one side declares a conflict the other satisfies.

    A shared ``provides`` alone is not a conflict — two packages may provide
    the same virtual name and coexist. A package never conflicts with itself,
    so an upgrade or a reinstall is silent.
    """
    found: list[PacmanConflict] = []
    for candidate in candidates:
        wanted = {candidate.name} | set(candidate.provides)
        for other in installed:
            if other.name == candidate.name:
                continue
            satisfied = {other.name} | set(other.provides)
            if not (other.conflicts & wanted or candidate.conflicts & satisfied):
                continue
            conflict = PacmanConflict(left=candidate.name, right=other.name,
                                      removable=other.name)
            if conflict not in found:
                found.append(conflict)
    return found

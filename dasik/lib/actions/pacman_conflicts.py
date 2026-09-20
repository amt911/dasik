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
from dataclasses import dataclass
import re

__all__ = ["PacmanConflict", "parse_conflicts", "strip_version"]

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

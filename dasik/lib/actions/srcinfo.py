"""Pure .SRCINFO parsing helpers — no I/O, no subprocess.

Shared by :mod:`pkgbuild_git_installer` (pkgname identity gate) and
:mod:`aur_installer` (transitive dependency discovery). Kept dependency-free so
the parsing is trivially unit-testable and reused, not re-implemented per module.
"""
from __future__ import annotations

import re
from typing import Set

# The three dependency classes makepkg resolves before a build. ``optdepends`` is
# deliberately excluded — optional deps must NOT be pulled in as build deps, and
# their ``name: description`` form would misparse.
_DEP_KEYS = ("depends", "makedepends", "checkdepends")

# A version constraint is anything from the first comparison operator onward
# (``gtk2>=2.24`` -> ``gtk2``); Arch dep specs use < > =.
_CONSTRAINT_RE = re.compile(r"[<>=]")


def parse_pkgnames(text: str) -> Set[str]:
    """Extract ``pkgname`` values from .SRCINFO / ``makepkg --printsrcinfo`` text.

    Only exact ``pkgname = X`` keys count — ``pkgbase`` and ``depends`` are
    ignored — so a split-package PKGBUILD yields all its subpackage names."""
    names: Set[str] = set()
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "pkgname":
            v = value.strip()
            if v:
                names.add(v)
    return names


def parse_depends(text: str) -> Set[str]:
    """Return the union of ``depends`` / ``makedepends`` / ``checkdepends`` values,
    including architecture-suffixed variants (``depends_x86_64``). ``optdepends``
    is excluded. Version constraints are left intact here — the caller strips them
    with :func:`strip_version_constraint` when it needs the bare name."""
    deps: Set[str] = set()
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        k = key.strip()
        if any(k == d or k.startswith(d + "_") for d in _DEP_KEYS):
            v = value.strip()
            if v:
                deps.add(v)
    return deps


def strip_version_constraint(dep: str) -> str:
    """``gtk2>=2.24`` -> ``gtk2``: drop the version constraint, return the bare
    package name (whitespace-trimmed)."""
    return _CONSTRAINT_RE.split(dep, maxsplit=1)[0].strip()

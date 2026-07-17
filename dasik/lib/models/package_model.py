"""Models for packages, unknown-package policy, and explicit Git PKGBUILD sources.

``packages`` entries are **real, clean names** (``firefox``, ``config-saver``);
dasik resolves each name's origin (repo/group/AUR) automatically at apply time —
no ``aur-`` prefix. Two extra top-level maps tune that resolution:

- ``package_policy`` — what to do with a name confirmed to exist nowhere.
- ``package_sources`` — declares the Git PKGBUILD that builds a package that is
  neither in a pacman repo/group nor in the AUR (e.g. a personal GitHub repo).
"""
import re
from posixpath import normpath
from typing import Literal

from pydantic import BaseModel, field_validator

# Arch package-name grammar (pacman.conf(5)/PKGBUILD(5)): a leading '-' or any
# shell metacharacter is refused so a config value never reaches pacman argv or a
# shell unsafely. Same grammar PackageResolver/PackagesAction enforce.
_VALID_PKG_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9@._+-]*")
_SHA1_HEX = re.compile(r"[0-9a-fA-F]{40}")


class PackageSpec(BaseModel):
    """A pacman/AUR package marked with an install reason.

    Plain strings in the ``packages`` list mean ``reason="explicit"``; this object
    form marks a dependency (``reason="dep"``). Names are real names — the source
    (repo/group/AUR/``package_sources``) is resolved automatically; there is no
    ``aur-`` prefix.
    """
    name: str
    reason: Literal["explicit", "dep"] = "explicit"


class PackagePolicyModel(BaseModel):
    """Policy for a declared package that resolves to no known source.

    ``warn-and-skip`` (default): a name confirmed to exist in no repo/group/AUR is
    skipped with a visible warning, the rest install, and ``apply`` exits 0 — it
    is retried on the next apply. ``error`` restores the strict abort (useful for
    CI). A source that could not be *reached* (AUR unavailable) is always a
    blocking error regardless of this policy.
    """
    unknown: Literal["warn-and-skip", "error"] = "warn-and-skip"


class GitPackageSourceModel(BaseModel):
    """A PKGBUILD living in a Git repository outside the AUR.

    dasik clones ``url`` at the exact ``ref`` (a full 40-char commit SHA, so the
    build is reproducible), builds the PKGBUILD under ``subdir`` as an
    unprivileged user, and verifies its ``pkgname`` matches the declared package
    before installing. First version limits ``url`` to ``https://github.com/….git``.
    """
    type: Literal["pkgbuild-git"]
    url: str
    ref: str
    subdir: str = "."

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError(f"package source url must be https://, got {v!r}")
        if not v.startswith("https://github.com/"):
            raise ValueError(
                f"package source url host must be github.com (first version), got {v!r}"
            )
        if not v.endswith(".git"):
            raise ValueError(f"package source url must end with .git, got {v!r}")
        return v

    @field_validator("ref")
    @classmethod
    def _validate_ref(cls, v: str) -> str:
        if not _SHA1_HEX.fullmatch(v):
            raise ValueError(
                f"package source ref must be a full 40-char hex commit SHA, got {v!r}"
            )
        return v

    @field_validator("subdir")
    @classmethod
    def _validate_subdir(cls, v: str) -> str:
        if v.startswith("/"):
            raise ValueError(f"package source subdir must be relative, got {v!r}")
        normalized = normpath(v)
        if normalized == ".." or normalized.startswith("../") or "/../" in normalized:
            raise ValueError(f"package source subdir must not escape the clone root: {v!r}")
        return v

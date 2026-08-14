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
from urllib.parse import urlsplit

from pydantic import BaseModel, field_validator

# Arch package-name grammar (pacman.conf(5)/PKGBUILD(5)): a leading '-' or any
# shell metacharacter is refused so a config value never reaches pacman argv or a
# shell unsafely. Same grammar PackageResolver/PackagesAction enforce.
_VALID_PKG_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9@._+-]*")
_SHA1_HEX = re.compile(r"[0-9a-fA-F]{40}")
# A DNS name: labels of alphanumerics and inner hyphens. urlsplit already
# stripped any port, so this never has to think about ':'.
_VALID_HOSTNAME = re.compile(
    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*"
)


class PackageSpec(BaseModel):
    """A pacman/AUR package marked with an install reason.

    Plain strings in the ``packages`` list mean ``reason="explicit"``; this object
    form marks a dependency (``reason="dep"``). Names are real names — the source
    (repo/group/AUR/``package_sources``) is resolved automatically; there is no
    ``aur-`` prefix.
    """
    name: str
    reason: Literal["explicit", "dep"] = "explicit"
    optional: bool = False
    """A package whose install failure must not stop convergence.

    Set it on peripheral software (a large AUR application, a vendor printer
    driver) whose upstream source can break independently of dasik. A failed
    optional package is reported and left OUT of the manifest — it is never
    claimed as installed, so the divergence stays visible and the next apply
    retries it. Required packages (the default) still abort the apply."""


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
    before installing.

    ``url`` is any ``https://<host>/…​.git``: a PKGBUILD that was never uploaded
    to the AUR lives wherever its author put it (GitHub, GitLab, Codeberg, a
    self-hosted forge). The value only ever reaches ``git`` as a positional
    argument — never a shell — so the host allowlist was never what made this
    safe. Still refused: plain HTTP (no integrity), a URL that is not a Git
    repository, and **credentials in the URL**, which would put a secret in a
    config file that ``sync`` copies verbatim.
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
        if not v.endswith(".git"):
            raise ValueError(f"package source url must end with .git, got {v!r}")
        parts = urlsplit(v)
        if "@" in parts.netloc:
            raise ValueError(
                f"package source url must not carry credentials, got {v!r}; "
                "a synced config would copy the secret verbatim"
            )
        try:
            host = parts.hostname
            parts.port          # raises on a non-numeric / out-of-range port
        except ValueError as exc:
            raise ValueError(f"package source url has an unusable port: {v!r}") from exc
        if not host or not _VALID_HOSTNAME.fullmatch(host):
            raise ValueError(f"package source url host is not a hostname: {v!r}")
        path = parts.path
        if not path.strip("/") or ".." in path.split("/"):
            raise ValueError(f"package source url path is unusable: {v!r}")
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

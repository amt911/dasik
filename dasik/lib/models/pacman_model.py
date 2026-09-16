"""Models for pacman configuration, including third-party repositories and keys.

``pacman.repositories`` declares extra ``pacman.conf`` sections beyond the
official ones (e.g. a personal signed repo like ``[amt911]``), and
``pacman.keys`` declares the PGP keys those repositories need trusted before
their database can be synced. Both lists are optional and default empty —
declaring neither changes nothing.
"""
import re
from typing import List, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# dasik does not own these sections: the official repos are Arch's, `options`
# is pacman.conf's own global section, and `multilib` already has its own
# dedicated boolean field on PacmanModel. A declared repository naming one of
# these would either silently shadow it or duplicate an existing knob.
OFFICIAL_REPOS: frozenset[str] = frozenset({
    "core", "extra", "multilib", "options",
    "core-testing", "extra-testing", "multilib-testing",
    "gnome-unstable", "kde-unstable", "testing", "community",
})

# pacman.conf(5) repository name grammar: must start with an alphanumeric,
# then alphanumerics/'.'/'_'/'-'. No shell metacharacters, no leading '-'
# (which pacman/getopt would read as a flag), no '/' (path separator).
_VALID_REPO_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

# pacman.conf(5) SigLevel grammar: whitespace-separated tokens, each an
# optional Package/Database prefix plus one of the five level keywords.
_SIG_LEVEL_TOKEN = re.compile(r"(Package|Database)?(Never|Optional|Required|TrustedOnly|TrustAll)")

_SHA1_FINGERPRINT = re.compile(r"[0-9A-Fa-f]{40}")

# ASCII control characters (0x00-0x1F, plus DEL 0x7F). `urlsplit` strips
# \t\r\n from its *internal* parsing copy but the validators below return the
# original string unchanged — so a value like
# "https://good.example/$arch\nSigLevel = Never\n[evil]\nServer = ..." parsed
# fine and was written to pacman.conf verbatim, injecting an extra directive
# or a whole extra section. Every value that ends up in a config file dasik
# writes must be checked against the raw string, not just the parsed URL.
_CONTROL_CHAR = re.compile(r"[\x00-\x1f\x7f]")


def _reject_control_chars(value: str, what: str) -> None:
    if _CONTROL_CHAR.search(value):
        raise ValueError(
            f"{what} must not contain control characters, got {value!r}; "
            "dasik writes this value verbatim into a config file"
        )


def _validate_https_url(value: str, what: str) -> None:
    """Shared ``https://`` URL check: scheme, non-empty host, no credentials,
    no control characters.

    Used by both ``PacmanRepositoryModel.servers`` (its https branch) and
    ``PacmanKeyModel.url`` so the two can never drift apart again — they
    previously duplicated this logic and ``PacmanKeyModel.url`` silently
    accepted a hostless URL (``"https://"``, ``"https:///k.gpg"``) that the
    servers check would have refused.
    """
    _reject_control_chars(value, what)
    parts = urlsplit(value)
    if parts.scheme != "https":
        raise ValueError(f"{what} must be https://, got {value!r}")
    if "@" in parts.netloc:
        raise ValueError(
            f"{what} must not carry credentials, got {value!r}; "
            "a synced config would copy the secret verbatim"
        )
    if not parts.netloc:
        raise ValueError(f"{what} has no host: {value!r}")


def _validate_server_url(value: str) -> None:
    """Refuse anything that is not a plain ``https://`` or ``file://`` URL.

    The https branch delegates to ``_validate_https_url`` (shared with
    ``PacmanKeyModel.url``); ``file://`` keeps its own path-only check.
    """
    _reject_control_chars(value, "pacman repository server")
    parts = urlsplit(value)
    if parts.scheme not in ("https", "file"):
        raise ValueError(
            f"pacman repository server must be https:// or file://, got {value!r}"
        )
    if parts.scheme == "https":
        _validate_https_url(value, "pacman repository server")
        return
    if "@" in parts.netloc:
        raise ValueError(
            f"pacman repository server must not carry credentials, got {value!r}; "
            "a synced config would copy the secret verbatim"
        )
    if not parts.path:
        raise ValueError(f"pacman repository server has no path: {value!r}")


class PacmanOptionsModel(BaseModel):
    """Individual pacman.conf options."""
    Parallel: bool = Field(default=True, description="Enable parallel downloads")
    Color: bool = Field(default=True, description="Enable coloured output")
    VerbosePkgLists: bool = Field(default=False, description="Enable verbose package lists")


class PacmanRepositoryModel(BaseModel):
    """A third-party ``pacman.conf`` repository section, e.g. ``[amt911]``.

    Exactly one of ``servers`` (non-empty) or ``include`` is required, mirroring
    the two ways pacman.conf itself points a repo at its mirrors: a literal
    ``Server =`` list, or an ``Include =`` file (the form ``sync`` uses to
    capture something like chaotic-aur without inventing servers).
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    servers: List[str] = Field(default_factory=list)
    include: Optional[str] = None
    sig_level: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not _VALID_REPO_NAME.fullmatch(value):
            raise ValueError(f"pacman repository name is invalid: {value!r}")
        if value in OFFICIAL_REPOS:
            raise ValueError(
                f"pacman repository name {value!r} is reserved (an official "
                "repo, or the [options] section) — dasik does not own it"
            )
        return value

    @field_validator("servers")
    @classmethod
    def _validate_servers(cls, value: List[str]) -> List[str]:
        for url in value:
            _validate_server_url(url)
        return value

    @field_validator("include")
    @classmethod
    def _validate_include(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        _reject_control_chars(value, "pacman repository include")
        if not value.startswith("/"):
            raise ValueError(
                f"pacman repository include must be an absolute path, got {value!r}"
            )
        return value

    @field_validator("sig_level")
    @classmethod
    def _validate_sig_level(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        tokens = value.split()
        if not tokens:
            raise ValueError("pacman repository sig_level must not be empty")
        for token in tokens:
            if not _SIG_LEVEL_TOKEN.fullmatch(token):
                raise ValueError(
                    f"pacman repository sig_level has an invalid token: {token!r}"
                )
        return value

    @model_validator(mode="after")
    def _servers_xor_include(self) -> "PacmanRepositoryModel":
        has_servers = bool(self.servers)
        has_include = self.include is not None
        if has_servers and has_include:
            raise ValueError(
                f"pacman repository '{self.name}': `servers` and `include` are "
                "mutually exclusive"
            )
        if not has_servers and not has_include:
            raise ValueError(
                f"pacman repository '{self.name}': exactly one of `servers` "
                "(non-empty) or `include` is required"
            )
        return self


class PacmanKeyModel(BaseModel):
    """A PGP key a third-party repository needs trusted before it can sync.

    ``url`` is optional: it is how dasik fetches the key (``pacman-key --add``
    on the downloaded file); without it, dasik falls back to
    ``pacman-key --recv-keys``. Either way the key is only ever trusted if its
    fetched primary fingerprint matches ``fingerprint`` exactly.
    """

    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    url: Optional[str] = None

    @field_validator("fingerprint")
    @classmethod
    def _validate_fingerprint(cls, value: str) -> str:
        if not _SHA1_FINGERPRINT.fullmatch(value):
            raise ValueError(
                f"pacman key fingerprint must be 40 hex characters, got {value!r}"
            )
        return value.upper()

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        _validate_https_url(value, "pacman key url")
        return value


class PacmanModel(BaseModel):
    """Pacman configuration."""
    options: PacmanOptionsModel = Field(default_factory=PacmanOptionsModel)
    multilib: bool = Field(default=False, description="Enable multilib repository")
    repositories: List[PacmanRepositoryModel] = Field(default_factory=list)
    keys: List[PacmanKeyModel] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_duplicates(self) -> "PacmanModel":
        seen_names = set()
        for repo in self.repositories:
            if repo.name in seen_names:
                raise ValueError(f"pacman repository '{repo.name}' is declared twice")
            seen_names.add(repo.name)
        seen_fingerprints = set()
        for key in self.keys:
            if key.fingerprint in seen_fingerprints:
                raise ValueError(
                    f"pacman key fingerprint '{key.fingerprint}' is declared twice"
                )
            seen_fingerprints.add(key.fingerprint)
        return self

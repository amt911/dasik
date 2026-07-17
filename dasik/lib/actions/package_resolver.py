"""PackageResolver — classify declared package names by origin at plan/apply time.

The user declares only real names (``firefox``, ``yay``, ``claude-desktop-bin``);
dasik decides whether each is an official-repo package, a pacman group, an AUR
package, unknown, or from a source that could not be reached. This removes the
``aur-`` prefix requirement: the same name keeps working if a package moves from
AUR into a repo (repo wins on the next apply).

Precedence: **configured repo > pacman group > AUR > unknown**.

- Repo/group membership is read from the *target*'s pacman sync DBs in a single
  batch each (``pacman -Slq`` / ``pacman -Sgq``) — never one process per name.
- AUR membership is an **exact-name** lookup via the aurweb RPC v5 ``info``
  endpoint (batched under the URI length limit), injected as ``http_get`` so no
  test touches the network.
- A network/DNS/5xx/parse failure is ``AurUnavailableError`` → the names land in
  ``unavailable`` (retryable), NEVER ``unknown`` (a package that truly does not
  exist). Callers MUST NOT install when anything is unknown or unavailable.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from ..command_worker.command_worker import Command
from ..exceptions.exceptions import ConfigValidationError

# Same grammar PackagesAction enforces (pacman.conf(5)/PKGBUILD(5)); rejecting a
# leading '-' or any shell metacharacter keeps an untrusted name out of both
# pacman's argv and the AUR query URL.
_VALID_PKG_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9@._+-]*")

_AUR_RPC_INFO_URL = "https://aur.archlinux.org/rpc/v5/info"
# aurweb documents a ~4443-byte request-URI cap; stay well under it per batch.
_MAX_QUERY_CHARS = 3500
_HTTP_TIMEOUT = 15
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class AurUnavailableError(Exception):
    """The AUR RPC could not be queried (timeout / DNS / HTTP 5xx / bad body).

    Distinct from "package not found": the source is unreachable, so we do not
    know whether the package exists. Callers treat this as retryable, never as
    proof of a typo."""


@dataclass(frozen=True)
class ResolvedGitPackage:
    """A name resolved to an explicit ``package_sources`` Git PKGBUILD.

    ``source`` is the validated source mapping (``type``/``url``/``ref``/``subdir``)
    carried through so the installer is self-contained."""

    name: str
    source: Mapping[str, Any]


@dataclass
class PackageResolution:
    """Where each declared name resolved. ``ok`` gates installation."""

    repo: List[str] = field(default_factory=list)
    groups: List[str] = field(default_factory=list)
    git: List[ResolvedGitPackage] = field(default_factory=list)
    aur: List[str] = field(default_factory=list)
    unknown: List[str] = field(default_factory=list)
    unavailable: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True only when every name resolved to an installable source."""
        return not self.unknown and not self.unavailable


def _validate(name: str) -> str:
    if not isinstance(name, str) or not _VALID_PKG_NAME.fullmatch(name):
        raise ConfigValidationError(
            f"Invalid package name {name!r}: names must match "
            f"[A-Za-z0-9][A-Za-z0-9@._+-]* (no shell metacharacters, no leading '-')."
        )
    return name


def _lines(result) -> set:
    stdout = getattr(result, "stdout", b"") or b""
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", errors="replace")
    return {line.strip() for line in stdout.splitlines() if line.strip()}


class PackageResolver:
    def __init__(self, http_get: Optional[Callable[[str], bytes]] = None):
        self._http_get = http_get or _default_http_get

    # -- pacman sync DBs (read from the target) ---------------------------

    def repo_names(self, target) -> set:
        """All package names in the target's configured sync repos."""
        return _lines(Command.execute("pacman", ["-Slq"], target=target))

    def repo_groups(self, target) -> set:
        """All pacman group names in the target's sync repos."""
        return _lines(Command.execute("pacman", ["-Sgq"], target=target))

    # -- AUR ---------------------------------------------------------------

    def aur_info(self, names: Sequence[str]) -> set:
        """Return the subset of *names* that exist in the AUR (exact match).

        Batches requests under the URI cap; raises ``AurUnavailableError`` if any
        batch could not be fetched or parsed. Only ``Name`` values that were
        actually requested are trusted (aurweb echoing an unsolicited name is
        ignored)."""
        requested = list(dict.fromkeys(names))
        for n in requested:
            _validate(n)
        found: set = set()
        for batch in self._batches(requested):
            found |= self._aur_info_batch(batch)
        return found

    def _batches(self, names: List[str]) -> List[List[str]]:
        batches: List[List[str]] = []
        current: List[str] = []
        length = 0
        for name in names:
            piece = len("&arg[]=") + len(urllib.parse.quote(name))
            if current and length + piece > _MAX_QUERY_CHARS:
                batches.append(current)
                current, length = [], 0
            current.append(name)
            length += piece
        if current:
            batches.append(current)
        return batches

    def _aur_info_batch(self, names: List[str]) -> set:
        query = urllib.parse.urlencode([("arg[]", n) for n in names])
        url = f"{_AUR_RPC_INFO_URL}?{query}"
        try:
            body = self._http_get(url)
            data = json.loads(body)
        except AurUnavailableError:
            raise
        except Exception as e:  # noqa: BLE001 - any fetch/parse failure = unavailable
            raise AurUnavailableError(f"AUR RPC query failed: {e}") from e
        requested = set(names)
        results = data.get("results", []) if isinstance(data, dict) else []
        return {
            r["Name"] for r in results
            if isinstance(r, dict) and r.get("Name") in requested
        }

    # -- resolution --------------------------------------------------------

    def resolve(
        self,
        names: Sequence[str],
        target,
        sources: Optional[Mapping[str, Mapping[str, Any]]] = None,
    ) -> PackageResolution:
        """Classify *names* into repo/group/git/AUR/unknown/unavailable.

        Precedence: **configured repo > pacman group > ``package_sources`` (git)
        > AUR > unknown**. A name with an explicit Git source is never sent to the
        AUR RPC — the user's declared source wins over a same-named AUR package.

        Deduplicates while preserving declared order so the resulting plan is
        stable. Only names that miss repo, group and any Git source are looked up
        in the AUR, in a single batched round. *sources* is the already-validated
        ``package_sources`` map (name -> source); ``None`` ≡ no Git sources."""
        git_sources: Dict[str, Mapping[str, Any]] = dict(sources or {})
        ordered = [_validate(n) for n in dict.fromkeys(names)]
        repo = self.repo_names(target)
        groups = self.repo_groups(target)

        res = PackageResolution()
        aur_candidates: List[str] = []
        for name in ordered:
            if name in repo:
                res.repo.append(name)
            elif name in groups:
                res.groups.append(name)
            elif name in git_sources:
                res.git.append(ResolvedGitPackage(name=name, source=git_sources[name]))
            else:
                aur_candidates.append(name)

        if not aur_candidates:
            return res

        try:
            aur_found = self.aur_info(aur_candidates)
        except AurUnavailableError:
            res.unavailable.extend(aur_candidates)
            return res

        for name in aur_candidates:
            (res.aur if name in aur_found else res.unknown).append(name)
        return res


def _default_http_get(url: str) -> bytes:
    """GET *url* with a timeout and response-size cap. Raises on any failure so
    the caller maps it to ``AurUnavailableError``."""
    # Defense-in-depth: the only caller builds this from the hardcoded
    # _AUR_RPC_INFO_URL (https), but refuse anything else so a file://custom
    # scheme can never reach urlopen.
    if not url.startswith("https://"):
        raise AurUnavailableError(f"refusing non-https AUR URL: {url!r}")
    req = urllib.request.Request(url, headers={"User-Agent": "dasik"})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:  # nosec B310 - https-only, guarded above
            return resp.read(_MAX_RESPONSE_BYTES + 1)[:_MAX_RESPONSE_BYTES]
    except urllib.error.URLError as e:
        raise AurUnavailableError(str(e)) from e

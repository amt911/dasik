"""Action: third-party pacman repositories and the keys that sign them.

v3 domain ``pacman_repositories``, ``config_key='pacman'`` — this action reads
the same ``pacman`` config block as ``PacmanAction`` (options/multilib), but
owns a disjoint slice of it: ``repositories`` and ``keys``. It is registered
as a separate action (not folded into ``PacmanAction``) so a repo/key can be
planned, applied and synced independently of the four scalar pacman.conf
flags — see the design doc
(``docs/superpowers/specs/2026-09-16-pacman-repositories-design.md``).

Item grammar::

    repo:<name>       — a ``[name]`` section exists in pacman.conf (third-party
                         only; ``OFFICIAL_REPOS``/``[options]`` are excluded by
                         ``pacman_repos_state.parse_sections``/``third_party``).
    key:<FINGERPRINT>  — that fingerprint is in the target's pacman keyring AND
                         is trusted: locally signed by the keyring's own master
                         key (``pacman-key --lsign-key``), not merely valid
                         through the web of trust (FACT-PR-2, docs/FACTS.md) —
                         and not a fingerprint a keyring PACKAGE (e.g.
                         archlinux-keyring) already ships as trusted
                         (``/usr/share/pacman/keyrings/*-trusted``, FACT-PR-5):
                         that key was installed by a package, not declared by
                         this config, and dasik does not own it.

All of the actual text/keyring parsing is pure and lives in
``pacman_repos_state.py`` (parse/render pacman.conf sections, parse
``gpg --with-colons``) so this action and ``PacmanAction.import_state`` (sync)
read the same machine the same way and can never disagree about it.

``plan()`` mirrors ``McpServersAction.plan``: ``compute_changes`` gives the
CREATE/DELETE block (D vs A, scoped to M for removal), and on top of that a
MODIFY is emitted for every declared ``repo:`` item that is present but whose
on-disk section differs from what is declared — never for ``key:`` items,
which are boolean (trusted or not) and have no "drifted" shape to report.
Reasons are checked in a fixed order and the first that applies wins, because
they are not independent: a section can be both content-correct and syncless,
and the two matter differently to the operator (a rewrite of pacman.conf vs.
a `pacman -Sy` for one repo).
"""
from __future__ import annotations

import glob
import os
from typing import Any, Dict, List, Optional, Set

from .abstract_action import AbstractAction
from .pacman_repos_state import (
    RepoSection,
    below_core,
    packaged_trusted,
    parse_sections,
    secret_keyids,
    section_of,
    third_party,
    trusted_fingerprints,
)
from ..command_worker.command_worker import Command
from ..state.change import Change, Op
from ..state.set_math import compute_changes

_DOMAIN = "pacman_repositories"

_CONF_PATH = "/etc/pacman.conf"
_GNUPG_HOMEDIR = "/etc/pacman.d/gnupg"
_KEYRINGS_GLOB = "/usr/share/pacman/keyrings/*-trusted"
_SYNC_DB_DIR = "/var/lib/pacman/sync"

_KEY_PREFIX = "key:"
_REPO_PREFIX = "repo:"


def _field(entry: Any, key: str, default: Any = None) -> Any:
    """Read *key* from a dict or from a pydantic model, whichever arrived."""
    if isinstance(entry, dict):
        return entry.get(key, default)
    return getattr(entry, key, default)


def _decode(data: Any) -> str:
    """``Command.execute`` returns ``bytes`` stdout for a real subprocess run,
    but tests mock it with a plain ``str`` (see ``test_mcp_servers_apply.py``'s
    idiom) — accept either."""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data or ""


class PacmanRepositoriesAction(AbstractAction):
    """Converge third-party pacman repositories and their trusted keys."""

    _DOMAIN = _DOMAIN

    def __init__(self, config: Any, context: Any = None):
        super().__init__(config, context)
        repos_raw = _field(config, "repositories", []) or []
        keys_raw = _field(config, "keys", []) or []
        self._repos: List[RepoSection] = [section_of(r) for r in repos_raw]
        self._keys: Dict[str, Optional[str]] = {
            str(_field(k, "fingerprint")).upper(): _field(k, "url")
            for k in keys_raw
        }

    @classmethod
    def empty_config(cls) -> Any:
        return {}

    @property
    def name(self) -> str:
        return "Pacman Repositories"

    @property
    def is_optional(self) -> bool:
        return True

    # -- target / paths ------------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _path(self, absolute: str) -> str:
        target = self._target()
        return target.path(absolute) if target is not None else "/mnt" + absolute

    # -- system reality --------------------------------------------------- #

    def _conf_text(self) -> Optional[str]:
        """The target's ``/etc/pacman.conf``, or ``None`` if it can't be read."""
        try:
            with open(self._path(_CONF_PATH), "r", encoding="utf-8") as handle:
                return handle.read()
        except OSError:
            return None

    def _db_synced(self, name: str) -> bool:
        """Whether ``<name>.db`` exists under the target's pacman sync dir.

        Without it the resolver classifies the repo's packages as AUR — the
        same silent failure ``multilib_synced`` had to tap (2026-08-18,
        docs/MEMORY.md) — so a section can be textually correct and still need
        a ``pacman -Sy`` for this one repo.
        """
        return os.path.exists(self._path(f"{_SYNC_DB_DIR}/{name}.db"))

    def _run_gpg(self, args: List[str]) -> Optional[str]:
        """One ``gpg`` call against the target's pacman keyring.

        Returns ``None`` (never raises) on a missing binary/keyring or a
        non-zero exit — the controller ruling's "no trusted keys, never an
        exception" contract.
        """
        try:
            result = Command.execute("gpg", args, target=self._target(),
                                      check=False)
        except Exception:
            return None
        if getattr(result, "returncode", 1) != 0:
            return None
        return _decode(getattr(result, "stdout", ""))

    def _keyring_trusted_texts(self) -> List[str]:
        """Every ``*-trusted`` file a keyring package shipped under the target."""
        pattern = self._path(_KEYRINGS_GLOB)
        texts: List[str] = []
        for path in sorted(glob.glob(pattern)):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    texts.append(handle.read())
            except OSError:
                continue
        return texts

    def _trusted_keys(self) -> Set[str]:
        """Fingerprints locally signed by the keyring's own master key, minus
        anything a keyring package already ships as trusted (controller
        ruling; FACT-PR-1/FACT-PR-2/FACT-PR-5, docs/FACTS.md)."""
        if self._target() is None:
            return set()
        secret_out = self._run_gpg(
            ["--homedir", _GNUPG_HOMEDIR, "--with-colons", "--list-secret-keys"])
        if secret_out is None:
            return set()
        master_keyids = secret_keyids(secret_out)
        if not master_keyids:
            return set()
        sigs_out = self._run_gpg(
            ["--homedir", _GNUPG_HOMEDIR, "--with-colons", "--list-sigs"])
        if sigs_out is None:
            return set()
        trusted = trusted_fingerprints(sigs_out, master_keyids)
        if not trusted:
            return set()
        return trusted - packaged_trusted(self._keyring_trusted_texts())

    def actual(self) -> set:
        if self._target() is None:
            return set()
        text = self._conf_text()
        if text is None:
            return set()
        repo_items = {f"{_REPO_PREFIX}{s.name}" for s in third_party(parse_sections(text))}
        key_items = {f"{_KEY_PREFIX}{fpr}" for fpr in self._trusted_keys()}
        return repo_items | key_items

    # -- desired state ------------------------------------------------------ #

    def _desired(self) -> Set[str]:
        desired = {f"{_REPO_PREFIX}{section.name}" for section in self._repos}
        desired |= {f"{_KEY_PREFIX}{fpr}" for fpr in self._keys}
        return desired

    def _declared_section(self, name: str) -> Optional[RepoSection]:
        for section in self._repos:
            if section.name == name:
                return section
        return None

    # -- v3 contract -------------------------------------------------------- #

    def _modify_reason(self, name: str, text: str,
                       actual_sections: Dict[str, RepoSection]) -> Optional[str]:
        """First applicable MODIFY reason for declared repo *name*, or ``None``.

        Checked in a fixed order — section content, then position, then the
        database — because they are not mutually exclusive and only the first
        one an operator would want to fix is reported.
        """
        declared = self._declared_section(name)
        current = actual_sections.get(name)
        if declared is None or current is None:
            return None
        if (declared.sig_level != current.sig_level
                or declared.servers != current.servers
                or declared.include != current.include):
            return "section drift"
        if below_core(text, name):
            return "below [core]"
        if not self._db_synced(name):
            return "database not synced"
        return None

    def plan(self, managed) -> List[Change]:
        if self._target() is None:
            return []
        desired = self._desired()
        actual = self.actual()
        changes, _drift = compute_changes(
            _DOMAIN,
            desired=desired,
            managed=managed,
            actual=actual,
            op_install=Op.CREATE,
            op_remove=Op.DELETE,
        )
        creates = sorted((c for c in changes if c.op is Op.CREATE),
                         key=lambda c: c.item)
        deletes = sorted((c for c in changes if c.op is Op.DELETE),
                         key=lambda c: c.item)

        modifies: List[Change] = []
        text = self._conf_text()
        if text is not None:
            actual_sections = {s.name: s for s in third_party(parse_sections(text))}
            for item in sorted(desired & actual):
                if not item.startswith(_REPO_PREFIX):
                    continue
                name = item[len(_REPO_PREFIX):]
                reason = self._modify_reason(name, text, actual_sections)
                if reason is not None:
                    modifies.append(Change(_DOMAIN, Op.MODIFY, item, reason=reason))

        return creates + modifies + deletes

    def managed_keys(self) -> dict:
        return {_DOMAIN: sorted(self._desired())}

    def verify(self) -> bool:
        return not self.plan(managed=[])

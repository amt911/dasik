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
    options_block,
    packaged_trusted,
    parse_sections,
    primary_fingerprints,
    render,
    secret_keyids,
    section_of,
    third_party,
    trusted_fingerprints,
)
from ..command_worker.command_worker import Command
from ..exceptions.exceptions import PacmanKeyMismatchError
from ..state.change import Change, Op
from ..state.set_math import compute_changes

_DOMAIN = "pacman_repositories"

_CONF_PATH = "/etc/pacman.conf"
_GNUPG_HOMEDIR = "/etc/pacman.d/gnupg"
_KEYRINGS_GLOB = "/usr/share/pacman/keyrings/*-trusted"
_SYNC_DB_DIR = "/var/lib/pacman/sync"

_KEY_PREFIX = "key:"
_REPO_PREFIX = "repo:"


def _key_download_path(fingerprint: str) -> str:
    """In-target path a CREATE key with a ``url`` is downloaded to.

    Lives under ``/var/tmp`` (never ``/tmp``): ``arch-chroot`` mounts a
    private ``/tmp``, so a file the host wrote there is invisible once inside
    the chroot (FACT-PR-3, docs/FACTS.md); ``/var/tmp`` is shared.
    """
    return f"/var/tmp/dasik-key-{fingerprint}.gpg"


def _repo_sync_conf_path(name: str) -> str:
    """In-target path of the single-repo ``pacman.conf`` used to ``pacman -Sy``
    just *name*'s database (FACT-PR-4: leaves every other repo's DB alone)."""
    return f"/var/tmp/dasik-pacman-{name}.conf"


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

    # -- apply ---------------------------------------------------------- #

    def _remove_if_exists(self, host_path: str) -> None:
        """Best-effort cleanup of a temp file — never lets a missing file
        (e.g. a mocked/failed download that never wrote one) raise out of a
        ``finally`` block and mask the real error."""
        try:
            os.remove(host_path)
        except OSError:
            pass

    def _apply_key_create(self, fingerprint: str, url: Optional[str]) -> None:
        """One CREATE key: with a ``url``, download + verify + trust it; without
        one, fetch it from the configured keyservers directly. Order and
        commands per the design doc's ``apply`` §1."""
        target = self._target()
        if url is None:
            Command.execute("pacman-key", ["--recv-keys", fingerprint],
                            target=target, check=True)
            Command.execute("pacman-key", ["--lsign-key", fingerprint],
                            target=target, check=True)
            return

        key_target_path = _key_download_path(fingerprint)
        key_host_path = self._path(key_target_path)
        try:
            Command.execute("curl", ["-fsSL", url, "-o", key_target_path],
                            target=target, check=True)
            result = Command.execute(
                "gpg", ["--show-keys", "--with-colons", key_target_path],
                target=target, check=True)
            found = primary_fingerprints(_decode(getattr(result, "stdout", "")))
            declared = {fingerprint}
            if found != declared:
                raise PacmanKeyMismatchError(
                    f"declared fingerprint {declared!r} does not match the "
                    f"downloaded key file {url!r}, which contains {found!r}"
                )
            Command.execute("pacman-key", ["--add", key_target_path],
                            target=target, check=True)
            Command.execute("pacman-key", ["--lsign-key", fingerprint],
                            target=target, check=True)
        finally:
            self._remove_if_exists(key_host_path)

    def _apply_repo_sync(self, name: str, new_conf_text: str) -> None:
        """One CREATE/MODIFY ``repo:``: build the single-repo ``pacman.conf`` (the real
        ``[options]`` verbatim + only this section) and ``pacman -Sy`` against
        it, so only *name*'s database is refreshed (FACT-PR-4, docs/FACTS.md)."""
        section = self._declared_section(name)
        if section is None:
            return  # a CREATE/MODIFY repo: item is always declared; defensive only
        single_conf = render(options_block(new_conf_text), [section], remove=[])
        conf_target_path = _repo_sync_conf_path(name)
        conf_host_path = self._path(conf_target_path)
        try:
            with open(conf_host_path, "w", encoding="utf-8") as handle:
                handle.write(single_conf)
            Command.execute("pacman", ["-Sy", "--config", conf_target_path],
                            target=self._target(), check=True, stream=True)
        finally:
            self._remove_if_exists(conf_host_path)

    def apply(self, changes: List[Change]) -> None:
        if self._target() is None or not changes:
            return

        key_creates = sorted(
            (c for c in changes if c.op is Op.CREATE and c.item.startswith(_KEY_PREFIX)),
            key=lambda c: c.item)
        repo_changes = [c for c in changes if c.item.startswith(_REPO_PREFIX)]
        repo_deletes = sorted((c for c in repo_changes if c.op is Op.DELETE),
                              key=lambda c: c.item)
        repo_upserts = sorted(
            (c for c in repo_changes if c.op in (Op.CREATE, Op.MODIFY)),
            key=lambda c: c.item)
        key_deletes = sorted(
            (c for c in changes if c.op is Op.DELETE and c.item.startswith(_KEY_PREFIX)),
            key=lambda c: c.item)

        # 1. keys, created first — a repo's SigLevel=Required db refresh
        # would otherwise need a key that never made it into the keyring.
        for change in key_creates:
            fingerprint = change.item[len(_KEY_PREFIX):]
            self._apply_key_create(fingerprint, self._keys.get(fingerprint))

        # 2. one read + one write of pacman.conf, whenever any repo: item
        # (CREATE, MODIFY or DELETE) is in this batch.
        new_conf_text = ""
        if repo_changes:
            conf_host_path = self._path(_CONF_PATH)
            current_text = self._conf_text()
            if current_text is None:
                raise FileNotFoundError(
                    f"cannot read {conf_host_path} to apply pacman_repositories")
            remove_names = [c.item[len(_REPO_PREFIX):] for c in repo_deletes]
            new_conf_text = render(current_text, declared=self._repos,
                                   remove=remove_names)
            with open(conf_host_path, "w", encoding="utf-8") as handle:
                handle.write(new_conf_text)

        # 3. sync each created/modified repo's own database.
        for change in repo_upserts:
            name = change.item[len(_REPO_PREFIX):]
            self._apply_repo_sync(name, new_conf_text)

        # 4. keys no longer declared, last — never strand a repo that still
        # needs one (a DELETE key alongside a surviving repo would be a
        # config the model already refuses, but ordering costs nothing).
        for change in key_deletes:
            fingerprint = change.item[len(_KEY_PREFIX):]
            Command.execute("pacman-key", ["--delete", fingerprint],
                            target=self._target(), check=True)

"""Action: the registries a SHORT image name is searched in.

Arch ships ``containers-common`` with an EMPTY ``/etc/containers`` — the sample
``registries.conf`` lives under ``/usr/share/containers`` and no package
installs it (``pacman -Ql containers-common`` lists the *directories* and
nothing in them). So a freshly installed machine resolves no unqualified name,
and the first ``docker compose up`` dies with

    Error: short-name "postgres:17.5" did not resolve to an alias and no
    containers-registries.conf(5) was found

The ArchWiki (Podman#Registries) answers with exactly one file::

    /etc/containers/registries.conf.d/10-unqualified-search-registries.conf
    unqualified-search-registries = ["docker.io"]

and that drop-in is what this domain owns. Measured, not assumed: with no
``/etc/containers/registries.conf`` at all, ``podman info`` on a machine
carrying only the drop-in reports ``registries: search: [docker.io]``.

Both machines this repo installs had the file written by hand years ago, so it
survived no reinstall and no ``sync`` ever saw it — the same shape of hole as
libvirt's autostart symlink ([[libvirt_network_action]]).

**Why a domain of its own** rather than a flag on ``ContainersAction``: an
action owns exactly one manifest domain (``Reconciler._domain_for`` refuses a
second), and that one is already ``subid``. The *capture* still belongs to
``ContainersAction``, which builds the whole ``containers`` block —
``ConfigWriter.merge`` splices fragments by TOP-LEVEL key, so a second action
returning ``{"containers": ...}`` would replace the block rather than add to it.

**Removing the last entry deletes the file.** An empty list is not the absence
of a list: ``unqualified-search-registries = []`` tells podman to search
nothing, which is the broken state this domain exists to fix.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List

from .abstract_action import AbstractAction
from ..state.change import Change, Op
from ..state.set_math import compute_changes

DROP_IN = ("/etc/containers/registries.conf.d/"
           "10-unqualified-search-registries.conf")
_KEY = "unqualified-search-registries"

# The key as the file carries it: `key = ["a", "b"]`, possibly spread over
# several lines. A commented-out line is not a declaration, so the match is
# anchored at the start of a line and refuses a leading `#`.
_ASSIGNMENT = re.compile(
    r'^[ \t]*' + re.escape(_KEY) + r'[ \t]*=[ \t]*\[(?P<list>[^]]*)\]',
    re.MULTILINE)
_ENTRY = re.compile(r'"([^"]+)"')

_HEADER = (
    "# Written by dasik: containers.search_registries.\n"
    "# Arch configures no registries, so an unqualified image name\n"
    "# (`postgres:17.5`) does not resolve without this file.\n"
    "# ArchWiki: Podman#Registries\n"
)


class ContainerRegistriesAction(AbstractAction):
    """Own the unqualified-search-registries drop-in."""

    _DOMAIN = "container_registries"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self._containers: Dict[str, Any] = cfg.get("containers") or {}

    @classmethod
    def empty_config(cls) -> Any:
        """Root-level action: bootstrap from an empty mapping, not a list."""
        return {}

    @property
    def name(self) -> str:
        return "Container Registries"

    @property
    def is_optional(self) -> bool:
        return True

    # --- paths -------------------------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _path(self) -> str:
        target = self._target()
        return target.path(DROP_IN) if target is not None else "/mnt" + DROP_IN

    # --- desired / actual ---------------------------------------------------- #

    def _desired(self) -> List[str]:
        """The declared search order, or nothing.

        Only podman reads this file. Under docker the model already refuses the
        field, so an empty list here means the domain simply has no business on
        a docker machine rather than "search nothing".
        """
        if self._containers.get("runtime") != "podman":
            return []
        declared = self._containers.get("search_registries")
        if not isinstance(declared, list):
            return []
        return [str(r) for r in declared]

    def actual(self) -> List[str]:
        """The registries the file lists, IN ORDER — the list is a search order.

        A list, not a set: ``compute_changes`` takes any iterable and the order
        is what apply has to reproduce.
        """
        try:
            with open(self._path(), "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            return []
        match = _ASSIGNMENT.search(text)
        if match is None:
            return []
        return _ENTRY.findall(match.group("list"))

    # --- v3 contract ---------------------------------------------------------- #

    def plan(self, managed: Any) -> List[Change]:
        if self._target() is None:
            return []
        changes, _drift = compute_changes(
            self._DOMAIN,
            desired=self._desired(),
            managed=managed or [],
            actual=self.actual(),
            op_install=Op.CREATE,
        )
        return changes

    def managed_keys(self) -> dict:
        return {self._DOMAIN: sorted(self._desired())}

    def apply(self, changes) -> None:
        if self._target() is None:
            return
        creates = {c.item for c in changes if c.op is Op.CREATE}
        removes = {c.item for c in changes if c.op is Op.REMOVE}
        if not creates and not removes:
            return
        self._write(self._merge(removes))

    def _merge(self, removes: set) -> List[str]:
        """Declared order first, then whatever else the file already listed.

        A registry someone added by hand is drift, so it is kept — but the
        declared order is policy and wins for the entries dasik owns.
        """
        desired = self._desired()
        kept = [r for r in self.actual()
                if r not in removes and r not in desired]
        return desired + kept

    def _write(self, registries: List[str]) -> None:
        path = self._path()
        if not registries:
            # See the module docstring: no file, not an empty list.
            try:
                os.unlink(path)
            except OSError:
                pass
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        listed = ", ".join(f'"{r}"' for r in registries)
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"{_HEADER}{_KEY} = [{listed}]\n")

    def import_state(self, managed: Any = None) -> dict:
        """Captured by ``ContainersAction``, which owns the whole block.

        Returning a ``containers`` fragment here would REPLACE the block that
        action builds, since ``ConfigWriter.merge`` splices by top-level key.
        The domain is still recorded as owned: the reconciler asks
        ``managed_keys()``/``actual()`` regardless of what the fragment says.
        """
        return {}

    def verify(self) -> bool:
        return not self.plan(managed=[])

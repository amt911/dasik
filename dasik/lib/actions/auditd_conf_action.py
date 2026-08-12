"""Action: the one line in /etc/audit/auditd.conf that makes the log readable.

The tmpfiles override the `apparmor.audit` toggle drops is necessary but not
sufficient: auditd enforces the mode of ``/var/log/audit`` itself at start, and
with no ``log_group`` it sets ``0700 root:root`` — VM-proven, the directory came
back root-only on every boot however the tmpfiles rule was written.

``auditd.conf`` is a pacman **backup** file with no drop-in directory, so dasik
owns the single key it needs and leaves every other line alone. Idempotent by
reading the effective value back, and reversible: dropping the audit flag
restores the file's own default by removing the line dasik added.
"""
from __future__ import annotations
import os
import re
from typing import Any, Dict, List, Optional

from .abstract_action import AbstractAction
from ..state.change import Change, Op

_AUDITD_CONF = "/etc/audit/auditd.conf"
_LOG_GROUP = "adm"
_ITEM = "log_group"
_LINE_RE = re.compile(r"^\s*log_group\s*=\s*(\S+)\s*$", re.I)
_MARK = "# Managed by dasik: auditd owns the mode of /var/log/audit at start.\n"


class AuditdConfAction(AbstractAction):
    """Set ``log_group`` in auditd.conf when the audit framework is declared."""

    _DOMAIN = "auditd_conf"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        apparmor = cfg.get("apparmor") or {}
        self._wanted: bool = bool(
            cfg.get("apparmor") is not None
            and apparmor.get("enable", True)
            and apparmor.get("audit"))

    @property
    def name(self) -> str:
        return "Auditd Log Group"

    @property
    def is_optional(self) -> bool:
        return True

    @classmethod
    def empty_config(cls):
        """Root-level action: bootstrap from an empty mapping, not a list."""
        return {}

    # --- paths ------------------------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _p(self) -> str:
        target = self._target()
        return target.path(_AUDITD_CONF) if target is not None else "/mnt" + _AUDITD_CONF

    def _lines(self) -> List[str]:
        try:
            with open(self._p(), "r", encoding="utf-8") as f:
                return f.read().splitlines()
        except OSError:
            return []

    def _current(self) -> Optional[str]:
        for line in self._lines():
            match = _LINE_RE.match(line)
            if match:
                return match.group(1)
        return None

    # --- v3 contract -------------------------------------------------------- #

    def actual(self) -> set:
        if self._target() is None:
            return set()
        return {_ITEM} if self._current() == _LOG_GROUP else set()

    def plan(self, managed: Any) -> List[Change]:
        changes: List[Change] = []
        if self._wanted and _ITEM not in self.actual():
            # Planned on the DECLARATION, not on the file. The whole plan is
            # computed before anything is applied, and on a fresh install the
            # `audit` package — which ships auditd.conf — is not there yet, so a
            # file check here made the change land one apply late (VM-observed).
            # PackagesAction runs first, so by apply time the file exists.
            changes.append(Change(self._DOMAIN, Op.MODIFY, _ITEM,
                                  reason=f"log_group = {_LOG_GROUP}"))
        if not self._wanted and _ITEM in (managed or []) and self._current() == _LOG_GROUP:
            changes.append(Change(self._DOMAIN, Op.REMOVE, _ITEM,
                                  reason="audit no longer declared"))
        return changes

    def apply(self, changes) -> None:
        if not changes or self._target() is None:
            return
        for change in changes:
            self._rewrite(remove=change.op is Op.REMOVE)

    def _rewrite(self, remove: bool) -> None:
        # Never CREATE the file: it belongs to the `audit` package, and a file
        # pacman does not own yet makes installing that package fail with
        # "exists in filesystem". If it is absent the package never arrived,
        # which is a louder failure than this one.
        if not os.path.exists(self._p()):
            return
        kept = [line for line in self._lines()
                if not _LINE_RE.match(line) and line != _MARK.rstrip("\n")]
        if not remove:
            kept += [_MARK.rstrip("\n"), f"log_group = {_LOG_GROUP}"]
        with open(self._p(), "w", encoding="utf-8") as f:
            f.write("\n".join(kept) + "\n")

    def managed_keys(self) -> dict:
        return {self._DOMAIN: [_ITEM] if self._wanted else []}

    def import_state(self, managed=None) -> dict:
        """Nothing: the `apparmor` block owns the declaration, and ApparmorAction
        captures it. This action only carries out what that flag implies."""
        return {}

    # --- legacy executor bridge --------------------------------------------- #

    def is_needed(self) -> bool:
        return bool(self.plan(managed=[]))

    def execute(self) -> None:
        self.apply(self.plan(managed=[]))

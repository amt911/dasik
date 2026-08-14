"""Action: the sudoers fragment dasik owns (v3 scalar domain "sudo").

Declaring a user in `wheel` is not enough on Arch: /etc/sudoers ships `%wheel`
commented out, so the declared administrator has no sudo at all. This action
writes /etc/sudoers.d/10-dasik with the wheel rule (and any extra rules), and
never installs a fragment `visudo` refuses — a broken fragment breaks sudo for
every user on the machine.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .scalar_action import ScalarV3Action
from ..command_worker.command_worker import Command
from ..exceptions.exceptions import ConfigValidationError
from ..state.change import Change, Op

_CANON = "/etc/sudoers.d/10-dasik"
# sudo's `#includedir` skips any filename containing a '.', so even a temporary
# left behind by a crash is never parsed as a rule file.
_TMP = _CANON + ".tmp"
_SUDOERS = "/etc/sudoers"
_HEADER = "# Managed by dasik — `dasik apply` overwrites this file.\n"


def _render(cfg: Dict[str, Any]) -> str:
    """The fragment's content for a `sudo` block. Empty when it grants nothing.

    ``wheel`` defaults to False HERE — an empty mapping means "no sudo block",
    which must render nothing. The model's ``wheel: True`` default is applied by
    ``SudoAction._effective`` when a block IS declared.
    """
    lines: List[str] = []
    if cfg.get("wheel", False):
        lines.append("%wheel ALL=(ALL) NOPASSWD: ALL" if cfg.get("nopasswd")
                     else "%wheel ALL=(ALL:ALL) ALL")
    lines.extend(str(rule).strip() for rule in cfg.get("rules") or [])
    if not lines:
        return ""
    return _HEADER + "\n".join(lines) + "\n"


def _canonical(text: str) -> str:
    """Comparable form: effective lines only, so a comment or blank-line edit is
    not mistaken for drift and re-applied forever."""
    keep = [line.strip() for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")]
    return "\n".join(keep) + "\n" if keep else ""


class SudoAction(ScalarV3Action):
    """Manage /etc/sudoers.d/10-dasik declaratively."""

    _DOMAIN = "sudo"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        self._cfg: Dict[str, Any] = config if isinstance(config, dict) else {}

    @property
    def name(self) -> str:
        return "Sudo Access"

    @property
    def is_optional(self) -> bool:
        return True

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _path(self, canonical: str = _CANON) -> str:
        t = self._target()
        return t.path(canonical) if t is not None else "/mnt" + canonical

    def _effective(self) -> Dict[str, Any]:
        """The `sudo` block, or the implicit default it stands in for.

        With no block declared, a user in `wheel` still expects to be an
        administrator — that is the whole point of the group — so the default is
        the password-protected wheel rule. An explicit ``{"wheel": false}`` opts
        out; only omission triggers the default.
        """
        declared = self._cfg.get("sudo")
        if declared is not None:
            # A raw dict (a hand-written config that never crossed SudoModel)
            # may omit keys; fill in the model's own defaults so both paths
            # render the same fragment.
            return {"wheel": True, "nopasswd": False, "rules": [], **dict(declared)}
        for user in self._cfg.get("users") or []:
            if isinstance(user, dict) and "wheel" in (user.get("groups") or []):
                return {"wheel": True, "nopasswd": False, "rules": []}
        return {}

    # --- ScalarV3Action hooks ----------------------------------------- #

    def _desired_value(self) -> Optional[str]:
        return _canonical(_render(self._effective())) or None

    def _actual_value(self) -> Optional[str]:
        try:
            with open(self._path(), "r") as f:
                return _canonical(f.read()) or None
        except OSError:
            return None

    def plan(self, managed: Any):
        """Also plan the removal the scalar base never plans.

        ScalarV3Action is set-or-replace by design ("a scalar is set or
        replaced, never removed"), which is right for a timezone — a machine
        always has one. It is wrong here: this fragment is what makes `%wheel`
        work at all, so leaving it behind after the last thing that declared
        sudo is gone keeps granting root to a group the config no longer
        mentions. Only ever removes a fragment the manifest owns; one someone
        else wrote is left alone.
        """
        if self._desired_value() is None:
            actual = self._actual_value()
            if actual is not None and managed:
                return [Change(self._DOMAIN, Op.REMOVE, actual,
                               reason="no longer declared: nothing in the config grants sudo")]
            return []
        return super().plan(managed)

    def apply(self, changes) -> None:
        if self._target() is None:
            return
        if any(c.op is Op.REMOVE for c in changes):
            try:
                os.remove(self._path())
            except FileNotFoundError:
                pass
            return
        super().apply(changes)

    def _set_value(self) -> None:
        content = _render(self._effective())
        tmp = self._path(_TMP)
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        with open(tmp, "w") as f:
            f.write(content)
        os.chmod(tmp, 0o440)

        # visudo runs INSIDE the target, so it gets the canonical path.
        result = Command.execute("visudo", ["-cf", _TMP], target=self._target())
        if getattr(result, "returncode", 1) != 0:
            os.remove(tmp)
            raise ConfigValidationError(
                f"visudo rejected the generated sudoers fragment; {_CANON} was left "
                "untouched. Check the `sudo.rules` entries in the config.")

        os.replace(tmp, self._path())
        os.chmod(self._path(), 0o440)

    def _import_fragment(self, value: str) -> dict:
        wheel = False
        nopasswd = False
        rules: List[str] = []
        for line in value.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("%wheel"):
                wheel = True
                nopasswd = "NOPASSWD" in line
                continue
            rules.append(line)
        if not wheel and not rules:
            return {}
        return {"sudo": {"wheel": wheel, "nopasswd": nopasswd, "rules": rules}}

    def import_state(self, managed=None) -> dict:
        """Capture the fragment — or, when dasik does not own one, the fact that
        stock /etc/sudoers already grants wheel. A captured config must reproduce
        a machine where sudo works, whichever of the two enabled it."""
        value = self._actual_value()
        if value:
            return self._import_fragment(value)
        if self._stock_sudoers_grants_wheel():
            return {"sudo": {"wheel": True, "nopasswd": False, "rules": []}}
        desired = self._desired_value()
        return self._import_fragment(desired) if desired else {}

    def _stock_sudoers_grants_wheel(self) -> bool:
        try:
            with open(self._path(_SUDOERS), "r") as f:
                lines = f.read().splitlines()
        except OSError:
            return False
        return any(line.strip().startswith("%wheel") for line in lines)

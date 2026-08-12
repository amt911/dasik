"""Action: PAM hardening — account lockout, resource limits, password policy.

Three independent items in one domain (``pam``):

* **faillock** — ``/etc/security/faillock.conf``, owned whole. ``pam_faillock``
  is ALREADY in Arch's ``/etc/pam.d/system-auth``, so the policy is a config
  file and nothing under /etc/pam.d has to be touched. That file has no
  ``.d`` drop-in directory, which is why it is owned rather than extended; it
  is a pacman *backup* file, so an upgrade leaves a ``.pacnew`` instead of
  clobbering it.
* **limits** — ``/etc/security/limits.d/10-dasik.conf``, a real drop-in.
* **pwquality** — ``/etc/security/pwquality.conf.d/10-dasik.conf`` (also a real
  drop-in) PLUS ``/etc/pam.d/passwd``, because ``pam_pwquality`` is *not* in
  Arch's stack and a policy nobody loads is not a policy. That is the only PAM
  stack file dasik writes, and the worst case is a broken ``passwd`` command —
  never a machine that cannot log in.

Reads are EFFECTIVE, not literal: pwquality merges the package file with every
drop-in in lexicographic order, the way libpwquality itself does. Reading only
our own file would make a value set in the package file invisible.

Removal is gated on ownership: only an item a previous generation recorded is
dasik's to undo, and undoing means restoring what the machine had before —
a header-only faillock.conf (the compiled-in defaults return), no drop-in, and
the stock four-line ``/etc/pam.d/passwd``.
"""
from __future__ import annotations
import glob
import os
from typing import Any, Dict, List, Optional

from .abstract_action import AbstractAction
from ..state.change import Change, Op

_FAILLOCK_CONF = "/etc/security/faillock.conf"
_LIMITS_DROPIN = "/etc/security/limits.d/10-dasik.conf"
_PWQUALITY_MAIN = "/etc/security/pwquality.conf"
_PWQUALITY_DROPIN = "/etc/security/pwquality.conf.d/10-dasik.conf"
_PASSWD_STACK = "/etc/pam.d/passwd"

_HEADER = "# Managed by dasik\n"

# What `shadow` ships as /etc/pam.d/passwd. Restored verbatim when the pwquality
# item is dropped: dasik only ever replaces this file when the block asks for it,
# so putting the original back is the honest undo.
_STOCK_PASSWD = (
    "#%PAM-1.0\n"
    "auth\t\tinclude\t\tsystem-auth\n"
    "account\t\tinclude\t\tsystem-auth\n"
    "password\tinclude\t\tsystem-auth\n"
)

_FAILLOCK = "faillock"
_LIMITS = "limits"
_PWQUALITY = "pwquality"


def _render_kv(settings: Dict[str, Any]) -> str:
    """Canonical `key = value` rendering: sorted, one per line.

    Canonical so key order or spacing on disk never produces a phantom change —
    the same reason SystemdConfAction renders its ini sections sorted.
    """
    lines = [_HEADER.rstrip("\n")]
    lines += [f"{key} = {settings[key]}" for key in sorted(settings)]
    return "\n".join(lines) + "\n"


def _parse_kv(text: str) -> Dict[str, str]:
    """`key = value` pairs, ignoring comments and bare flags."""
    out: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        out[key.strip()] = value.strip()
    return out


class PamAction(AbstractAction):
    """Own the three PAM policy files, item by item."""

    _DOMAIN = "pam"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        pam: Dict[str, Any] = cfg.get("pam") or {}
        self._faillock: Optional[Dict[str, Any]] = pam.get("faillock")
        self._limits: Optional[Dict[str, Any]] = pam.get("limits")
        pwquality = pam.get("pwquality")
        # `enable: false` is a declaration too — it means "do not enforce",
        # which is the same desired state as not declaring the item.
        self._pwquality: Optional[Dict[str, Any]] = (
            pwquality if pwquality is not None and pwquality.get("enable", True) else None)

    @property
    def name(self) -> str:
        return "PAM Hardening"

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

    def _p(self, canonical: str) -> str:
        target = self._target()
        return target.path(canonical) if target is not None else "/mnt" + canonical

    def _read(self, canonical: str) -> str:
        try:
            with open(self._p(canonical), "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def _write(self, canonical: str, content: str) -> None:
        path = self._p(canonical)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    # --- desired content ---------------------------------------------------- #

    @staticmethod
    def _faillock_settings(cfg: Dict[str, Any]) -> Dict[str, Any]:
        settings: Dict[str, Any] = {
            "deny": cfg.get("deny", 5),
            "fail_interval": cfg.get("fail_interval", 900),
            "unlock_time": cfg.get("unlock_time", 600),
        }
        if cfg.get("persistent", True):
            # The default /run/faillock is cleared by a reboot, which an
            # attacker holding the power button can arrange.
            settings["dir"] = "/var/lib/faillock"
        return settings

    @staticmethod
    def _limits_content(cfg: Dict[str, Any]) -> str:
        soft = cfg.get("nproc_soft", 100)
        hard = cfg.get("nproc_hard", 200)
        return (f"{_HEADER}"
                f"# Caps the processes one user can run — the ceiling a fork bomb hits.\n"
                f"* soft nproc {soft}\n"
                f"* hard nproc {hard}\n")

    @staticmethod
    def _pwquality_settings(cfg: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "minlen": cfg.get("minlen", 10),
            "difok": cfg.get("difok", 6),
            "retry": cfg.get("retry", 2),
            "dcredit": cfg.get("dcredit", -1),
            "ucredit": cfg.get("ucredit", -1),
            "lcredit": cfg.get("lcredit", -1),
            "ocredit": cfg.get("ocredit", -1),
        }

    @staticmethod
    def _passwd_stack(cfg: Dict[str, Any]) -> str:
        """`/etc/pam.d/passwd` with pwquality in front of pam_unix.

        `use_authtok` is what makes pam_unix accept the password pwquality just
        validated instead of prompting for a new one — without it the policy is
        enforced and then bypassed in the same transaction.
        """
        options = f"retry={cfg.get('retry', 2)}"
        if cfg.get("enforce_for_root"):
            options += " enforce_for_root"
        return (
            "#%PAM-1.0\n"
            "# Managed by dasik: the policy itself lives in\n"
            "# /etc/security/pwquality.conf.d/10-dasik.conf.\n"
            "auth\t\tinclude\t\tsystem-auth\n"
            "account\t\tinclude\t\tsystem-auth\n"
            f"password\trequired\tpam_pwquality.so {options}\n"
            "password\trequired\tpam_unix.so use_authtok yescrypt shadow\n"
        )

    # --- effective state ---------------------------------------------------- #

    def _effective_faillock(self) -> Dict[str, str]:
        return _parse_kv(self._read(_FAILLOCK_CONF))

    def _effective_pwquality(self) -> Dict[str, str]:
        """The package file plus every drop-in, later winning — the order
        libpwquality applies. Reading only dasik's own drop-in would make a
        value set in the package file invisible."""
        merged: Dict[str, str] = _parse_kv(self._read(_PWQUALITY_MAIN))
        for path in sorted(glob.glob(self._p("/etc/security/pwquality.conf.d/*.conf"))):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    merged.update(_parse_kv(f.read()))
            except OSError:
                continue
        return merged

    def _converged(self, item: str) -> bool:
        if item == _FAILLOCK:
            desired = {k: str(v) for k, v in
                       self._faillock_settings(self._faillock or {}).items()}
            actual = self._effective_faillock()
            return all(actual.get(k) == v for k, v in desired.items())
        if item == _LIMITS:
            return self._read(_LIMITS_DROPIN) == self._limits_content(self._limits or {})
        desired_conf = {k: str(v) for k, v in
                        self._pwquality_settings(self._pwquality or {}).items()}
        actual_conf = self._effective_pwquality()
        if not all(actual_conf.get(k) == v for k, v in desired_conf.items()):
            return False
        # The drop-in alone changes nothing: without the module in the stack,
        # nobody ever reads it.
        return self._read(_PASSWD_STACK) == self._passwd_stack(self._pwquality or {})

    def _declared(self) -> List[str]:
        return [item for item, cfg in ((_FAILLOCK, self._faillock),
                                       (_LIMITS, self._limits),
                                       (_PWQUALITY, self._pwquality))
                if cfg is not None]

    def _present(self, item: str) -> bool:
        """Whether the target still carries dasik's version of this item."""
        if item == _FAILLOCK:
            return _HEADER.strip() in self._read(_FAILLOCK_CONF)
        if item == _LIMITS:
            return os.path.exists(self._p(_LIMITS_DROPIN))
        return (os.path.exists(self._p(_PWQUALITY_DROPIN))
                or "pam_pwquality.so" in self._read(_PASSWD_STACK))

    # --- v3 contract --------------------------------------------------------- #

    def actual(self) -> set:
        if self._target() is None:
            return set()
        return {item for item in self._declared() if self._converged(item)}

    def plan(self, managed: Any) -> List[Change]:
        declared = self._declared()
        converged = self.actual()
        changes: List[Change] = []
        for item in declared:
            if item not in converged:
                changes.append(Change(self._DOMAIN, Op.INSTALL, item,
                                      reason="PAM policy"))
        for item in managed or []:
            if item not in declared and self._present(item):
                changes.append(Change(self._DOMAIN, Op.REMOVE, item,
                                      reason="no longer declared"))
        return changes

    def apply(self, changes) -> None:
        if not changes or self._target() is None:
            return
        for change in changes:
            if change.op is Op.REMOVE:
                self._undo(change.item)
            else:
                self._write_item(change.item)

    def _write_item(self, item: str) -> None:
        if item == _FAILLOCK:
            self._write(_FAILLOCK_CONF,
                        _render_kv(self._faillock_settings(self._faillock or {})))
        elif item == _LIMITS:
            self._write(_LIMITS_DROPIN, self._limits_content(self._limits or {}))
        else:
            cfg = self._pwquality or {}
            self._write(_PWQUALITY_DROPIN, _render_kv(self._pwquality_settings(cfg)))
            self._write(_PASSWD_STACK, self._passwd_stack(cfg))

    def _undo(self, item: str) -> None:
        if item == _FAILLOCK:
            # A header-only file: pam_faillock falls back to its compiled-in
            # defaults, which is what the stock (all-commented) file means too.
            self._write(_FAILLOCK_CONF, _HEADER)
        elif item == _LIMITS:
            try:
                os.remove(self._p(_LIMITS_DROPIN))
            except FileNotFoundError:
                pass
        else:
            try:
                os.remove(self._p(_PWQUALITY_DROPIN))
            except FileNotFoundError:
                pass
            self._write(_PASSWD_STACK, _STOCK_PASSWD)

    def managed_keys(self) -> dict:
        return {self._DOMAIN: self._declared()}

    # --- legacy executor bridge ---------------------------------------------- #

    def is_needed(self) -> bool:
        return bool(self.plan(managed=[]))

    def execute(self) -> None:
        self.apply(self.plan(managed=[]))

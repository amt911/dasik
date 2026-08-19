"""Action: declarative tailscale preferences (the tailscaled conffile).

v3 scalar domain "tailscale": the desired state is the content of
``/etc/tailscale/tailscaled.conf``, the file ``tailscaled --config`` reads.

Why a file and not ``tailscale set``: prefs otherwise live in
``/var/lib/tailscale/tailscaled.state``, which no ``plan`` against an unmounted
``/mnt`` can read, so the domain would need a unit converging on every boot. The
precedent is :mod:`firewall_action`, which reaches for ``firewall-offline-cmd``
for the same reason. The conffile is a plain file: readable, comparable and
capturable with the target cold.

The trade the user accepts by declaring the block: while the conffile is in use
tailscaled answers ``can't reconfigure tailscaled when using a config file;
config file is locked``, so ``tailscale set`` no longer moves the declared keys.
That is the ownership model dasik wants, stated by the daemon itself.

**The schema is `alpha0` and ships no documentation.** Every key below was
verified against the binary by ``scripts/vmtest/guest-tsspike.sh``, which uses
tailscaled's own behaviour as the oracle: an unknown key is a HARD ERROR
(``json: unknown field``), so a candidate that starts the daemon is a real key
and one that does not is not. Three plausible names turned out to be wrong, each
of which would have produced a daemon that refuses to start:

===========================  ==============================
guessed                      actual
===========================  ==============================
``ExitNodeAllowLANAccess``   ``AllowLANWhileUsingExitNode``
``SSH``                      ``RunSSHServer``
``NoSNAT``                   ``DisableSNAT``
===========================  ==============================

So the map is explicit and never mechanical case conversion: ``accept_dns`` does
not titlecase to ``AcceptDNS``, and a near miss is not a near miss here.

Idempotent: both sides are rendered to canonical JSON (sorted keys) before
comparing, so re-applying an unchanged config is a no-op whatever the on-disk
key order. The daemon is pointed at this file by ``/etc/default/tailscaled``,
which :func:`dasik.lib.expand.toggles.expand_tailscale` contributes as an
ordinary ``files`` entry. NOT by a drop-in: a ``tailscaled.service.d`` fragment
setting ``FLAGS`` does not reach the daemon — measured in a guest, the
EnvironmentFile wins with or without a ``daemon-reload``. pacman lists
``/etc/default/tailscaled`` under ``Backup Files``, so owning it is the
vendor-sanctioned route and an upgrade writes a ``.pacnew`` beside it rather
than clobbering it.
"""
from __future__ import annotations
import json
import os
from typing import Any, Dict, Optional

from .scalar_action import ScalarV3Action
from ..logging import run_logger

_CONF = "/etc/tailscale/tailscaled.conf"

# The only accepted value: tailscaled refuses "v1alpha1" with `unsupported
# "version" value ... want "alpha0" for now`, and an absent field with
# `no "version" field defined`.
_SCHEMA_VERSION = "alpha0"

# dasik field -> conffile key. Verified accepted by the binary; see the module
# docstring for the three that are not what they look like.
_CONFFILE_KEYS: Dict[str, str] = {
    "accept_routes": "AcceptRoutes",
    "accept_dns": "AcceptDNS",
    "ssh": "RunSSHServer",
    "web_client": "RunWebClient",
    "shields_up": "ShieldsUp",
    "exit_node": "ExitNode",
    "exit_node_allow_lan_access": "AllowLANWhileUsingExitNode",
    "advertise_routes": "AdvertiseRoutes",
    "advertise_exit_node": "AdvertiseExitNode",
    "hostname": "Hostname",
    "operator": "OperatorUser",
    "netfilter_mode": "NetfilterMode",
    "posture_checking": "PostureChecking",
    "server_url": "ServerURL",
    # Rendered as a `file:` reference and parsed back only in that form — a
    # literal key someone hand-provisioned is never copied into a Git config.
    "auth_key_file": "AuthKey",
}

_FIELD_FOR_KEY = {v: k for k, v in _CONFFILE_KEYS.items()}

# Fields of the block that are deliberately NOT conffile keys. `port` is the
# daemon's listening port: it belongs in /etc/default/tailscaled beside the
# --config flag (expand_tailscale writes it), and putting it in the conffile
# would make tailscaled refuse to start. Listed rather than silently skipped, so
# a genuine typo still raises.
_NON_CONFFILE_FIELDS = frozenset({"port"})


def _render(block: Dict[str, Any]) -> Optional[str]:
    """Canonical conffile JSON for *block*, or None when nothing is declared.

    Only keys the config actually sets are emitted. That distinction is not
    cosmetic: an absent key leaves the preference to tailscale, while a key set
    to its default takes it away from the CLI just as firmly as any other.
    """
    body: Dict[str, Any] = {}
    for field, value in block.items():
        if field in _NON_CONFFILE_FIELDS:
            continue
        if field not in _CONFFILE_KEYS:
            # Never drop it in silence. TailscaleModel forbids extras, but this
            # action is also handed raw dicts — a sync seed, expand output — that
            # never crossed the model, and a skipped key is a preference the user
            # declared, watched converge, and never got.
            raise ValueError(
                f"unknown tailscale preference {field!r}; known: "
                f"{', '.join(sorted(_CONFFILE_KEYS))}")
        key = _CONFFILE_KEYS[field]
        if value is None:
            continue
        if isinstance(value, (list, tuple)) and not value:
            continue
        if field == "auth_key_file":
            body[key] = f"file:{value}"
            continue
        body[key] = list(value) if isinstance(value, (list, tuple)) else value
    if not body:
        return None
    body["version"] = _SCHEMA_VERSION
    return json.dumps(body, indent=2, sort_keys=True) + "\n"


def _parse(text: str) -> Dict[str, Any]:
    """The dasik block a conffile describes; keys dasik does not model are
    dropped rather than surfaced as bogus config fields."""
    try:
        raw = json.loads(text)
    except (ValueError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        if k not in _FIELD_FOR_KEY:
            continue
        if _FIELD_FOR_KEY[k] == "auth_key_file":
            # Capture the PATH form only. A literal key someone provisioned by
            # hand is a secret; copying it into a config `dasik save` commits
            # to Git is the exact leak the field exists to avoid.
            if isinstance(v, str) and v.startswith("file:"):
                out["auth_key_file"] = v[len("file:"):]
            continue
        out[_FIELD_FOR_KEY[k]] = v
    return out


class TailscaleAction(ScalarV3Action):
    """Manage /etc/tailscale/tailscaled.conf declaratively."""

    _DOMAIN = "tailscale"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self._block: Dict[str, Any] = cfg.get("tailscale") or {}
        self._warned_missing_key = False

    @property
    def name(self) -> str:
        return "Tailscale Configuration"

    @property
    def is_optional(self) -> bool:
        return True

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _path(self) -> str:
        t = self._target()
        return t.path(_CONF) if t is not None else "/mnt" + _CONF

    def _desired_value(self) -> Optional[str]:
        return _render(self._effective_block())

    def _effective_block(self) -> Dict[str, Any]:
        """The declared block, minus an AuthKey whose file is not there yet.

        Measured in the guest oracle (guest-authkey-spike.sh): tailscaled
        refuses to START on a dangling ``file:`` reference. A key file the user
        has not provisioned yet must not take the daemon down, so the entry is
        omitted with a loud warning and the domain converges without it; once
        the file appears, the next plan shows the MODIFY that adds the AuthKey.
        """
        path = self._block.get("auth_key_file")
        if not path:
            return self._block
        t = self._target()
        real = t.path(path) if t is not None else path
        if os.path.exists(real):
            return self._block
        if not self._warned_missing_key:
            self._warned_missing_key = True
            run_logger.get().warning(
                f"tailscale.auth_key_file declares {path!r}, which does not "
                "exist on the target — writing the conffile WITHOUT AuthKey "
                "(a dangling file: reference stops tailscaled from starting)",
                detail="Create the file (a tailnet auth key, mode 0600) and "
                       "re-run apply; the node logs in on the next daemon "
                       "start. Until then it stays logged out.",
            )
        return {k: v for k, v in self._block.items() if k != "auth_key_file"}

    def _actual_value(self) -> Optional[str]:
        try:
            with open(self._path(), "r") as f:
                return _render(_parse(f.read()))
        except (FileNotFoundError, NotADirectoryError, OSError):
            return None

    def _set_value(self) -> None:
        path = self._path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rendered = self._desired_value() or ""
        with open(path, "w") as f:
            f.write(rendered)

    def plan(self, managed):
        """The scalar comparison, plus the removal it cannot express.

        ``ScalarV3Action.plan`` only ever proposes a MODIFY towards a non-empty
        desired value, which would leave the conffile in place when the block is
        dropped — and a conffile is not inert while it sits there: the daemon
        keeps reading it, and it keeps refusing ``tailscale set``. Blanking the
        file is not the fix either, since an empty conffile locks the CLI out
        just the same. It has to go, and only when the manifest says it is ours.
        """
        from ..state.change import Change, Op

        if self._block:
            return super().plan(managed)
        if managed and self._actual_value() is not None:
            return [Change(self._DOMAIN, Op.REMOVE, _CONF,
                           reason="no longer declared — the daemon stops "
                                  "reading it and `tailscale set` works again")]
        return []

    def apply(self, changes) -> None:
        from ..state.change import Op

        if any(c.op is Op.REMOVE for c in changes):
            try:
                os.remove(self._path())
            except OSError:
                pass
            return
        super().apply(changes)

    def _import_fragment(self, value: str) -> dict:
        return {"tailscale": _parse(value)} if value else {}

    def import_state(self, managed=None) -> dict:
        """Report the machine, never the config.

        ``ScalarV3Action`` falls back to the DESIRED value when the target reads
        as nothing, which suits a domain where "nothing read" is a failure — a
        machine always has a timezone. Here it is a state: no conffile means no
        declared preferences. A declared block the machine lacks is CLEARED
        rather than omitted, because ``ConfigWriter.merge`` only overwrites a
        key and never deletes one, so silence would leave the stale declaration
        standing.
        """
        value = self._actual_value()
        if value:
            return self._import_fragment(value)
        return {"tailscale": {}} if self._block else {}

"""Action: firewalld default-zone rules, written declaratively (idempotent).

Installing firewalld + enabling the service is the `firewall` expand toggle's job
(packages + systemd). This action owns the RULES the toggle can't express:
allowed services, rich rules, and services removed from the default zone.

It writes the complete ``/etc/firewalld/zones/public.xml`` — dasik owns the file
— instead of driving ``firewall-offline-cmd``. That avoids firewalld's
default-service quirk (``--remove-service`` does not strip a built-in default,
and ``--list-services`` reports defaults, so a remove_service re-fired on every
apply). The desired zone is: (default services − remove_services) + allowed
services + rich rules. Idempotent by construction: a change is planned only when
the on-disk file differs from the desired content.
"""
import os
import re
from typing import Any, List, Optional

from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command
from ..exceptions.exceptions import ConfigValidationError
from ..state.change import Change, Op

_ZONE_PATH = "/etc/firewalld/zones/public.xml"
_UFW_BIN = "/usr/bin/ufw"
# `ufw status` prints one rule per line as "<target> <ACTION IN> <source>".
# Parsed rather than /etc/ufw/user.rules: that file is generated state only ufw
# should write, and its `### tuple ###` form is ufw's internal grammar, not the
# one a config declares.
_UFW_ACTIONS = {"ALLOW": "allow", "DENY": "deny", "REJECT": "reject",
                "LIMIT": "limit"}
# firewalld's upstream `public` zone default services.
_DEFAULT_SERVICES = ["dhcpv6-client", "ssh"]


# Clause grammar, consumed left to right. Each entry is (key, anchored regex).
# A rule is only accepted when EVERY token it contains matches one of these —
# an unrepresentable clause must fail closed, never be silently dropped: the
# rate limit of `accept limit value="2/m"` is what keeps the rule narrow, so
# ignoring it would widen access (see firewalld richlanguage(5)).
_CLAUSES: List[tuple] = [
    ("family", r'family[=\s]+"?([^"\s]+)"?'),
    ("source", r'source\s+address[=\s]+"?([^"\s]+)"?'),
    ("destination", r'destination\s+address[=\s]+"?([^"\s]+)"?'),
    ("service", r'service\s+name[=\s]+"?([^"\s]+)"?'),
    ("port", r'port\s+port[=\s]+"?([^"\s]+)"?\s+protocol[=\s]+"?([^"\s]+)"?'),
    ("protocol", r'protocol\s+value[=\s]+"?([^"\s]+)"?'),
    ("action", r'(accept|drop)\b'),
    ("reject", r'reject\b(?:\s+type[=\s]+"?([^"\s]+)"?)?'),
    ("limit", r'limit\s+value[=\s]+"?([^"\s]+)"?'),
]


def _rich_rule_to_xml(rule: str) -> str:
    """Convert a firewall-cmd rich-rule string to a zone-XML <rule> element.

    Tolerant of quoted/unquoted values. Supports family, source/destination
    address, service name, port+protocol, protocol value, the terminal action
    (accept|reject|drop) and its optional rate ``limit``. Any other clause
    (log, audit, masquerade, forward-port, NOT …) raises
    :class:`ConfigValidationError`: an access rule that cannot be represented
    losslessly must be rejected, not approximated.
    """
    rest = rule.strip()
    m = re.match(r'rule\b', rest)
    if not m:
        raise ConfigValidationError(f"rich rule must start with 'rule': {rule!r}")
    rest = rest[m.end():]

    parsed: dict = {}
    while rest.strip():
        rest = rest.lstrip()
        for key, pattern in _CLAUSES:
            m = re.match(pattern, rest)
            if not m:
                continue
            if key in parsed or (key in ("action", "reject") and
                                 ("action" in parsed or "reject" in parsed)):
                raise ConfigValidationError(
                    f"duplicate '{key}' clause in rich rule: {rule!r}")
            parsed[key] = m.groups()
            rest = rest[m.end():]
            break
        else:
            raise ConfigValidationError(
                f"unsupported clause in rich rule: {rest.strip()!r} (rule: {rule!r})")

    if "action" not in parsed and "reject" not in parsed:
        raise ConfigValidationError(f"rich rule has no action: {rule!r}")

    inner: List[str] = []
    if "source" in parsed:
        inner.append(f'<source address="{parsed["source"][0]}"/>')
    if "destination" in parsed:
        inner.append(f'<destination address="{parsed["destination"][0]}"/>')
    if "service" in parsed:
        inner.append(f'<service name="{parsed["service"][0]}"/>')
    if "port" in parsed:
        port, proto = parsed["port"]
        inner.append(f'<port port="{port}" protocol="{proto}"/>')
    if "protocol" in parsed:
        inner.append(f'<protocol value="{parsed["protocol"][0]}"/>')

    limit = f'<limit value="{parsed["limit"][0]}"/>' if "limit" in parsed else ""
    if "reject" in parsed:
        rtype = parsed["reject"][0]
        attrs = f' type="{rtype}"' if rtype else ""
        inner.append(f'<reject{attrs}>{limit}</reject>' if limit
                     else f'<reject{attrs}/>')
    else:
        act = parsed["action"][0]
        inner.append(f'<{act}>{limit}</{act}>' if limit else f'<{act}/>')

    attrs = f' family="{parsed["family"][0]}"' if "family" in parsed else ""
    return f'<rule{attrs}>' + "".join(inner) + "</rule>"


class FirewallAction(AbstractAction):
    """Own the firewalld public zone file declaratively."""

    _DOMAIN = "firewall"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg = config if isinstance(config, dict) else {}
        self.enable: bool = cfg.get("enable", False)
        self.allowed: List[str] = cfg.get("allowed_services", [])
        self.rich: List[str] = cfg.get("rich_rules", [])
        self.remove: List[str] = cfg.get("remove_services", [])
        self.backend: str = cfg.get("backend", "firewalld")
        self.rules: List[str] = cfg.get("rules", [])

    @property
    def name(self) -> str:
        return "Firewall Rules"

    @property
    def is_optional(self) -> bool:
        return True

    @classmethod
    def empty_config(cls):
        return {}

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    # --- ufw backend ---------------------------------------------------- #
    #
    # firewalld's whole zone is a file dasik owns. ufw's state is generated by
    # the tool, so this backend reads the machine through `ufw status` and
    # writes through the CLI — never into /etc/ufw/user.rules.

    def _is_ufw(self) -> bool:
        return self.backend == "ufw"

    def _desired_ufw_rules(self) -> List[str]:
        """Declared rules, plus one `allow <profile>` per allowed service.

        Order-preserving and de-duplicated, so the manifest and the plan agree
        on the item names.
        """
        wanted: List[str] = []
        for rule in list(self.rules) + [f"allow {s}" for s in self.allowed]:
            if rule not in wanted:
                wanted.append(rule)
        return wanted

    def _ufw_status(self) -> Optional[str]:
        """`ufw status` output, or None when it cannot be asked.

        None is NOT convergence: at install time there is no running firewall,
        and claiming the rules were there would skip them forever. `ufw allow`
        is idempotent, so re-applying a rule that already exists costs nothing.
        """
        try:
            result = Command.execute("ufw", ["status"], target=self._target())
        except Exception:      # nosec B110 - a failed probe means "unknown"
            return None
        if getattr(result, "returncode", 1) != 0:
            return None
        return self._decode(result.stdout)

    @staticmethod
    def _parse_ufw_status(text: str) -> List[str]:
        """The live rules, in the `<action> <target>` form a config declares."""
        rules: List[str] = []
        for raw in text.splitlines():
            fields = raw.split()
            if len(fields) < 2 or fields[0] in ("To", "--", "Status:", "Logging:",
                                                "Default:"):
                continue
            action = _UFW_ACTIONS.get(fields[1].upper())
            if not action:
                continue
            rule = f"{action} {fields[0]}"
            if rule not in rules:
                rules.append(rule)
        return rules

    def _live_ufw_rules(self) -> List[str]:
        status = self._ufw_status()
        return self._parse_ufw_status(status) if status else []

    def _plan_ufw(self) -> List[Change]:
        live = set(self._live_ufw_rules())
        return [Change(self._DOMAIN, Op.INSTALL, rule, reason="ufw rule")
                for rule in self._desired_ufw_rules() if rule not in live]

    def _apply_ufw(self, changes) -> None:
        for change in changes:
            # Split here, never in the shell: `ufw allow 22/tcp` is two
            # arguments, and this string comes from the config.
            # check=True: a rule ufw refused is a port left open (or shut) while
            # the plan reports it applied.
            Command.execute("ufw", change.item.split(), target=self._target(), check=True)
        # Non-interactive: plain `ufw enable` asks for confirmation and would
        # hang an unattended apply.
        Command.execute("ufw", ["--force", "enable"], target=self._target(), check=True)

    def _ufw_installed(self) -> bool:
        target = self._target()
        try:
            path = target.path(_UFW_BIN) if target is not None else _UFW_BIN
        except AttributeError:          # a target double with no path() (tests)
            path = _UFW_BIN
        return os.path.exists(path)

    def _zone_file(self) -> str:
        t = self._target()
        return t.path(_ZONE_PATH) if t is not None else "/mnt" + _ZONE_PATH

    def _desired_xml(self) -> str:
        services = sorted((set(_DEFAULT_SERVICES) - set(self.remove)) | set(self.allowed))
        lines = ['<?xml version="1.0" encoding="utf-8"?>', "<zone>", "  <short>Public</short>"]
        lines += [f'  <service name="{s}"/>' for s in services]
        lines += ["  " + _rich_rule_to_xml(r) for r in self.rich]
        lines.append("</zone>")
        return "\n".join(lines) + "\n"

    def _current_xml(self):
        try:
            with open(self._zone_file(), "r") as f:
                return f.read()
        except FileNotFoundError:
            return None

    # --- v3 contract -------------------------------------------------- #

    def actual(self) -> set:
        if self._is_ufw():
            live = set(self._live_ufw_rules())
            return {r for r in self._desired_ufw_rules() if r in live}
        return {"public"} if (self.enable and self._current_xml() is not None) else set()

    def plan(self, managed):
        if not self.enable:
            return []
        if self._is_ufw():
            return self._plan_ufw()
        if self._current_xml() == self._desired_xml():
            return []
        return [Change(self._DOMAIN, Op.MODIFY, "public", reason="zone rules")]

    def apply(self, changes) -> None:
        if not changes:
            return
        if self._is_ufw():
            self._apply_ufw(changes)
            return
        path = self._zone_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(self._desired_xml())

    def managed_keys(self) -> dict:
        if self._is_ufw():
            return {self._DOMAIN: self._desired_ufw_rules() if self.enable else []}
        return {self._DOMAIN: ["public"] if self.enable else []}

    @staticmethod
    def _decode(out) -> str:
        return out.decode("utf-8", "replace") if isinstance(out, bytes) else (out or "")

    def _fw_query(self, *args) -> "Any":
        """Run `firewall-offline-cmd <args>` against the target; return its stdout
        text, or None if it fails (best-effort). Offline (not `firewall-cmd`) on
        purpose: it reads /etc/firewalld directly, so it needs no running daemon /
        D-Bus session — the reliable path for sync (root) and for a /mnt install
        target reached via arch-chroot. It DOES require root, which sync has."""
        try:
            res = Command.execute("firewall-offline-cmd", list(args), target=self._target())
        except Exception:
            return None
        if getattr(res, "returncode", 1) != 0:
            return None
        return self._decode(res.stdout)

    def import_state(self, managed=None) -> dict:
        """Capture whichever firewall this machine actually runs.

        ufw first, and only when it is installed AND reports rules: a machine
        with both packages present runs one of them, and the one with live rules
        is the one describing reality.
        """
        if self._ufw_installed():
            rules = self._live_ufw_rules()
            if rules:
                return {"firewall": {"enable": True, "backend": "ufw",
                                     "rules": rules}}
        return self._import_firewalld()

    def _import_firewalld(self, managed=None) -> dict:
        """Capture the live firewalld permanent public zone back into a `firewall`
        block. `--list-rich-rules` returns rules in the same syntax `rich_rules`
        expects, so they round-trip; allowed/removed services are the diff against
        firewalld's upstream `public` defaults. Nothing captured when
        firewall-offline-cmd is unavailable (sync leaves the section untouched)."""
        services_txt = self._fw_query("--zone=public", "--list-services")
        if services_txt is None:
            return {}
        services = set(services_txt.split())
        rich_txt = self._fw_query("--zone=public", "--list-rich-rules") or ""
        rich = [ln.strip() for ln in rich_txt.splitlines() if ln.strip()]

        frag: dict = {"enable": True}
        allowed = sorted(services - set(_DEFAULT_SERVICES))
        removed = sorted(set(_DEFAULT_SERVICES) - services)
        if allowed:
            frag["allowed_services"] = allowed
        if removed:
            frag["remove_services"] = removed
        if rich:
            frag["rich_rules"] = rich
        return {"firewall": frag}

    def is_needed(self) -> bool:
        return bool(self.plan(managed=[]))

    def execute(self) -> None:
        self.apply(self.plan(managed=[]))

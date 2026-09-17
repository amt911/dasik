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
from typing import Any, Dict, List, Optional

from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command
from ..exceptions.exceptions import ConfigValidationError
from ..logging import run_logger
from ..state.change import Change, Op

_ZONES_DIR = "/etc/firewalld/zones"
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
        # S3: which backend plan() decided teardown/removal targets, so
        # apply() reuses that SAME decision instead of re-deriving one from
        # the shape of the changes it is handed. Set by plan(); persisted by
        # state_metadata() so a later disabled/absent plan can read back
        # what was actually applied rather than guessing.
        self._teardown_backend: Optional[str] = None
        # SF-3: set by `_reload_firewalld` when the immediate reload attempted
        # from inside `apply()` failed (typically because the SAME apply also
        # needs to (re)install `firewalld` -- this action runs before
        # PackagesAction/SystemdAction). `finalize_apply()` retries once the
        # whole apply has settled, when the package/unit are guaranteed there.
        self._reload_pending: bool = False

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

    def _plan_ufw(self, managed=()) -> List[Change]:
        """Set-math over the rules, both directions.

        The install half was here from the start; the removal half was not, so a
        port you stopped declaring stayed open for ever — the plan never
        mentioned it and `sync` captured it back into the config as if it had
        been asked for. Removal is scoped to what dasik added (the manifest), so
        somebody else's rule is drift and stays.

        ``desired`` is forced empty when the block is disabled — one of the
        three ways "the firewall disappears" (the others: the whole block
        gone, or a rule dropped from ``rules`` while ``enable`` stays true) —
        even though ``self.rules``/``self.allowed`` are not themselves
        enable-gated in ``__init__``. Only the REMOVE half still runs then, so
        an owned rule still live on the machine goes, and nothing new installs.
        """
        live = set(self._live_ufw_rules())
        desired = self._desired_ufw_rules() if self.enable else []
        changes = [Change(self._DOMAIN, Op.INSTALL, rule, reason="ufw rule")
                   for rule in desired if rule not in live]
        changes += [Change(self._DOMAIN, Op.REMOVE, rule, reason="no longer declared")
                    for rule in sorted(set(managed) - set(desired))
                    if rule in live]
        return changes

    def _plan_firewalld_disabled(self, managed) -> List[Change]:
        """The firewalld half of "disabled/absent -> REMOVE what is owned":
        every managed zone that still has a file on disk goes."""
        return [Change(self._DOMAIN, Op.REMOVE, zone, reason="firewall disabled")
                for zone in sorted(set(managed or ()))
                if self._current_xml(zone) is not None]

    @staticmethod
    def _looks_like_ufw_items(managed) -> bool:
        """True when *managed* has the shape of ufw rule strings, not
        firewalld zone names.

        A ufw rule always has the "<action> <target>" shape ``_desired_ufw_rules``
        / ``_parse_ufw_status`` produce (e.g. ``"allow ssh"``); a firewalld zone
        name is a bare identifier with no spaces (``public``, ``home``, …). When
        the `firewall` block is entirely absent, ``self.backend`` defaults to
        "firewalld" and cannot be trusted to tell the two apart — so classify
        from the manifest's own shape instead. Never guesses when there is
        nothing to classify (empty ``managed``): with nothing owned there is
        nothing to remove either way.
        """
        items = list(managed or ())
        if not items:
            return False
        return all(" " in item and item.split()[0].lower() in _UFW_ACTIONS.values()
                   for item in items)

    def _action_state(self) -> dict:
        """Per-action state the last apply recorded (mirrors
        ``PackagesAction._action_state``): ``manifest.action_state["firewall"]``."""
        manifest = getattr(self.context, "manifest", None) if self.context else None
        if not isinstance(manifest, dict):
            return {}
        state = manifest.get("action_state", {}).get(self._DOMAIN, {})
        return state if isinstance(state, dict) else {}

    def _recorded_backend(self) -> "Optional[str]":
        backend = self._action_state().get("backend")
        return backend if backend in ("ufw", "firewalld") else None

    def _resolved_backend(self, managed) -> str:
        """Which backend teardown/removal-planning targets, decided ONCE and
        reused by both ``plan()`` and ``apply()`` (S3) -- never re-derived
        independently, which is exactly the mismatch that let ``plan()``
        announce firewalld while ``apply()`` drove ufw (PROBE-4).

        While the block is enabled, ``self.backend`` is the current,
        trustworthy declaration. While it is disabled or the whole block is
        absent, the parsed config's own ``backend`` field defaults to
        "firewalld" (a ``FirewallModel`` default, not a fact about history)
        and cannot be trusted, so: an EXPLICIT ``backend: ufw`` while merely
        disabled is still honored; failing that, the backend dasik actually
        applied last time, recorded in the manifest's ``action_state``
        (``state_metadata()``); failing THAT (a manifest written by dasik
        <= 0.18.0, before this field existed), fall back to classifying the
        managed items by shape.
        """
        if self.enable:
            return self.backend
        if self._is_ufw():
            return "ufw"
        recorded = self._recorded_backend()
        if recorded is not None:
            return recorded
        return "ufw" if self._looks_like_ufw_items(managed) else "firewalld"

    def _apply_ufw(self, changes) -> None:
        # FirewallAction runs BEFORE PackagesAction (branch
        # feat/snapper-firewall-removal, mirroring SnapperAction's own
        # pre-Packages placement for the identical reason) precisely so this
        # is safe on a REMOVE: when the whole
        # `firewall` block goes undeclared, PackagesAction (which runs AFTER
        # this action) is the one that uninstalls `ufw` — so the binary is
        # still guaranteed to be here right now, whichever direction changes
        # go. On a fresh INSTALL, `ufw` may not be installed yet either (this
        # action now runs before Packages), so it installs its own
        # prerequisite the same way SnapperAction does for `snapper`.
        #
        # N-7: only actually NEEDED for an INSTALL. `_plan_ufw`'s REMOVE half
        # only ever fires for a rule currently `in live` (`_live_ufw_rules()`
        # read straight off `ufw status`), so a REMOVE-only apply can never be
        # the first thing to touch a machine without `ufw` on it — by
        # construction, ufw is already there. Gating this on an INSTALL being
        # present makes the wiki's "never installs the package just to remove
        # something from it" sentence true in the stronger, code-enforced
        # sense, not merely true of the configs anyone happens to write.
        if any(c.op is Op.INSTALL for c in changes):
            self._ensure_ufw_installed()
        for change in changes:
            # Split here, never in the shell: `ufw allow 22/tcp` is two
            # arguments, and this string comes from the config.
            argv = change.item.split()
            if change.op is Op.REMOVE:
                # --force so `ufw delete` does not stop to ask.
                argv = ["--force", "delete", *argv]
            # check=True: a rule ufw refused is a port left open (or shut) while
            # the plan reports it applied — and a REMOVE that failed leaves the
            # manifest claiming a rule the firewall still enforces.
            Command.execute("ufw", argv, target=self._target(), check=True)
        # Non-interactive: plain `ufw enable` asks for confirmation and would
        # hang an unattended apply. Only when something is actually being
        # INSTALLed — a pure teardown (the block just went undeclared) has no
        # business re-enabling the firewall it is decommissioning.
        if any(c.op is Op.INSTALL for c in changes):
            Command.execute("ufw", ["--force", "enable"], target=self._target(), check=True)

    def _ensure_ufw_installed(self) -> None:
        """Install ufw if it is not there yet — mirrors
        ``SnapperAction._ensure_snapper_installed``. ``--needed`` makes it a
        no-op once installed, so this costs nothing on the common path where
        PackagesAction already put `ufw` there before this action runs."""
        target = self._target()
        probe = Command.execute("pacman", ["-Qq", "ufw"], target=target)
        if getattr(probe, "returncode", 0) == 0:
            return
        Command.execute("pacman", ["--noconfirm", "--needed", "-S", "ufw"],
                        target=target, check=True, stream=True)

    def _ufw_installed(self) -> bool:
        target = self._target()
        try:
            path = target.path(_UFW_BIN) if target is not None else _UFW_BIN
        except AttributeError:          # a target double with no path() (tests)
            path = _UFW_BIN
        return os.path.exists(path)

    def _rooted(self, path: str) -> str:
        """*path* under the target root, tolerating a target double with no
        ``path()`` (the same allowance ``_ufw_installed`` already makes)."""
        t = self._target()
        if t is None:
            return "/mnt" + path
        try:
            return t.path(path)
        except AttributeError:
            return "/mnt" + path

    def _zone_file(self, zone: str = "public") -> str:
        return self._rooted(f"{_ZONES_DIR}/{zone}.xml")

    def _extra_zones(self) -> "Dict[str, Any]":
        """The declared zones other than `public`, as {name: spec-dict}."""
        zones = self.config.get("zones") if isinstance(self.config, dict) else None
        return dict(zones) if isinstance(zones, dict) else {}

    def _declared_zones(self) -> "List[str]":
        return ["public"] + sorted(self._extra_zones())

    def _desired_xml(self, zone: str = "public") -> str:
        if zone == "public":
            services = sorted((set(_DEFAULT_SERVICES) - set(self.remove))
                              | set(self.allowed))
            rich = self.rich
        else:
            # An extra zone's allowed_services IS the list: naming a zone is
            # already the whole statement, so nothing is merged in underneath it.
            spec = self._extra_zones().get(zone) or {}
            services = sorted(set(spec.get("allowed_services") or []))
            rich = list(spec.get("rich_rules") or [])
        lines = ['<?xml version="1.0" encoding="utf-8"?>', "<zone>",
                 f"  <short>{zone.capitalize()}</short>"]
        lines += [f'  <service name="{s}"/>' for s in services]
        lines += ["  " + _rich_rule_to_xml(r) for r in rich]
        lines.append("</zone>")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _same_zone(current: "Optional[str]", desired: str) -> bool:
        """Whether a zone file says the same thing as the declaration.

        By CONTENT, never byte-for-byte: `sync` captures `rich_rules` in the
        order firewalld lists them, which is not the order the config declared
        them in, and a text comparison then proposed the same MODIFY on every
        plan — sync -> plan was never silent on a machine with more than one
        rich rule (measured in a guest). Services and rules are a set: two
        files with the same lines in another order enforce the same zone.
        """
        if current is None:
            return False
        def lines(xml: str) -> set:
            return {line.strip() for line in xml.splitlines() if line.strip()}
        return lines(current) == lines(desired)

    def _current_xml(self, zone: str = "public"):
        try:
            with open(self._zone_file(zone), "r") as f:
                return f.read()
        except FileNotFoundError:
            return None

    # --- v3 contract -------------------------------------------------- #

    def actual(self) -> set:
        """Every rule/zone this backend reports right now, REGARDLESS of
        `enable` -- the "A = all" convention `SystemdAction.actual()`
        already follows. Gating this on `enable` (as it did before) meant a
        `sync` run while the block was disabled/absent computed
        `actual ∩ (claimable ∪ declared)` with actual=set(), dispossessing
        the manifest of a rule/zone it still owned (S2). The reconciler's
        own intersection with claimable/declared still scopes this: a live
        rule/zone dasik never touched is never falsely claimed.

        Which backend to READ is the same question `plan()`/`apply()` answer
        via `_resolved_backend()` (S3): while the block is absent, `backend`
        defaults to "firewalld" and cannot be trusted on its own, so a
        manifest recording which backend was actually applied settles it
        here too -- otherwise a `sync` on a ufw-only machine whose `firewall`
        block just got fully deleted would probe an empty
        `/etc/firewalld/zones` and lose ownership the same way the `enable`
        gate did.

        SF-4: `_resolved_backend()`'s own shape-heuristic fallback cannot run
        HERE, because `actual()` has no `managed` to classify (it answers
        "what does the machine have", not "what does the manifest own") --
        calling it with an empty tuple always answers "firewalld". So when
        the question is genuinely unresolved (disabled/absent, no explicit
        `backend`, and no manifest recording either -- a manifest predating
        S3, or a converged apply that never persisted a decision), report
        reality from BOTH backends rather than silently picking one: the
        reconciler's own intersection with claimable/declared still scopes
        this down to what dasik actually owns, exactly as it does for the
        resolved cases above.
        """
        backend: Optional[str]
        if self.enable:
            backend = self.backend
        elif self._is_ufw():
            backend = "ufw"
        else:
            backend = self._recorded_backend()
        if backend == "ufw":
            return set(self._live_ufw_rules())
        if backend == "firewalld":
            return set(self._customised_zones())
        return set(self._customised_zones()) | set(self._live_ufw_rules())

    def plan(self, managed):
        managed = list(managed or ())
        self._teardown_backend = self._resolved_backend(managed)
        if self._teardown_backend == "ufw":
            return self._plan_ufw(managed)
        if not self.enable:
            return self._plan_firewalld_disabled(managed)
        declared = self._declared_zones()
        changes = [Change(self._DOMAIN, Op.MODIFY, zone, reason="zone rules")
                   for zone in declared
                   if not self._same_zone(self._current_xml(zone),
                                          self._desired_xml(zone))]
        # A zone dasik wrote and the config no longer names keeps enforcing
        # rules nothing declares; its file goes with the declaration.
        for zone in sorted(set(managed) - set(declared)):
            if self._current_xml(zone) is not None:
                changes.append(Change(self._DOMAIN, Op.REMOVE, zone,
                                      reason="no longer declared"))
        return changes

    def apply(self, changes) -> None:
        if not changes:
            return
        # S3: reuse the SAME decision plan() made — never re-derive one from
        # the shape of these changes (that mismatch is exactly what let plan
        # announce one backend while apply drove another). `_teardown_backend`
        # is only ever None here when apply() is called without a preceding
        # plan(), which no real path does; the fallback keeps that defensive.
        backend = self._teardown_backend
        if backend is None:
            backend = self._resolved_backend([c.item for c in changes])
        if backend == "ufw":
            self._apply_ufw(changes)
            return
        touched = False
        for change in changes:
            path = self._zone_file(change.item)
            if change.op is Op.REMOVE:
                try:
                    os.remove(path)
                    touched = True
                except FileNotFoundError:
                    pass
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(self._desired_xml(change.item))
            touched = True
        if touched:
            self._reload_firewalld()

    def _reload_firewalld(self) -> None:
        """A zone file on disk is not what firewalld enforces until the
        running daemon re-reads it -- on a LIVE target only (N4, mirrors
        ``DropFilesAction._reload_systemd`` / issue #300's same lesson):
        there is no running firewalld under an install target at ``/mnt`` to
        reload, and its first boot reads the zone fresh.

        Best-effort, like ``_ufw_status``'s own probe (VM-caught): dropping
        the whole `firewall` block undeclares the `firewalld` package too,
        and PackagesAction runs AFTER this action (same registry order that
        keeps a ufw REMOVE working while `ufw` is still installed) — so a
        `rollback` that restores the block runs this reload BEFORE Packages
        reinstalls `firewalld`. `systemctl is-active` can still report the
        unit as active (a resident process from before the uninstall) while
        `/usr/bin/firewall-cmd` is genuinely gone from disk at that exact
        moment, and a bare `FileNotFoundError` from `subprocess.run` used to
        propagate out of `apply()` and abort the WHOLE apply/rollback over a
        cosmetic reload. The zone file is already written either way; a
        failed reload only delays the daemon noticing, never loses data.

        Best-effort does not mean silent (SF-2): a swallowed rc!=0 or
        exception used to leave no trace anywhere — no raise, no log line —
        so "Applied" was reported while the running daemon still enforced the
        stale zone. Every failure to reload is now a warning naming the
        remediation, through the same ``run_logger`` every other action uses
        for a non-fatal problem (e.g. ``LibvirtNetworkAction``,
        ``DropFilesAction``).
        """
        target = self._target()
        if target is None or getattr(target, "is_chroot", True):
            return
        try:
            probe = Command.execute("systemctl", ["is-active", "firewalld"], target=target)
            if getattr(probe, "returncode", 1) != 0:
                return
            result = Command.execute("firewall-cmd", ["--reload"], target=target)
        except Exception as exc:      # nosec B110 - best-effort reload, see docstring
            self._warn_reload_failed(f"running `firewall-cmd --reload` raised {exc!r}")
            # SF-3: MEASURED live -- this is exactly the transient-package
            # window (a `drop block -> rollback` reinstalling `firewalld` in
            # the SAME apply): retry once everything has settled, via
            # `finalize_apply()`.
            self._reload_pending = True
            return
        if getattr(result, "returncode", 1) != 0:
            # SF2-2: NO retry here. firewalld refuses a reload over bad
            # on-disk config BEFORE flushing, so the daemon keeps its
            # last-good runtime; a restart would flush it (CleanupOnExit) and
            # come back in the stock failsafe config. Only the exception
            # branch above — the binary itself transiently missing — is worth
            # retrying once the apply has settled.
            self._warn_reload_failed(
                f"`firewall-cmd --reload` exited {result.returncode}"
            )

    def _warn_reload_failed(self, cause: str) -> None:
        run_logger.get().warning(
            f"firewalld: zone written but the running daemon could not be "
            f"reloaded ({cause}).",
            detail="the daemon keeps enforcing the previous zone until it is "
                   "restarted by hand: run `systemctl restart firewalld`.",
        )

    def finalize_apply(self) -> None:
        """SF-3: retry a reload that failed during `apply()` itself, once
        every action in this apply has run — called by the reconciler after
        the whole apply succeeds (never on a failed/partial one). MEASURED
        live: a `drop firewall block -> rollback` writes the zone and
        attempts a reload from `apply()` BEFORE `PackagesAction` reinstalls
        `firewalld` (this action runs first, same registry order that keeps
        a ufw REMOVE working while `ufw` is still installed) and
        `SystemdAction` only `enable`s the unit (never `--now`) — so the
        immediate reload hits a transiently-missing binary, and nothing
        after ever tells the resident daemon to reload; it keeps enforcing
        the previous (often default) zone indefinitely.

        `systemctl try-restart` rather than `is-active` + `--reload`: by now
        the package and unit are guaranteed present (Packages/Systemd have
        already run), and `try-restart` only restarts a unit that is
        ALREADY active — a no-op on a fresh install where nothing runs the
        daemon yet, and a real restart (picking up both the new binary and
        the already-written zone file) for exactly this stale-daemon case.
        Still best-effort, with the SAME SF-2 warning on failure.
        """
        if not self._reload_pending:
            return
        target = self._target()
        if target is None or getattr(target, "is_chroot", True):
            return
        try:
            result = Command.execute("systemctl", ["try-restart", "firewalld"],
                                     target=target)
        except Exception as exc:      # nosec B110 - best-effort retry, see docstring
            self._warn_reload_failed(
                f"retrying via `systemctl try-restart firewalld` raised {exc!r}"
            )
            return
        if getattr(result, "returncode", 1) != 0:
            self._warn_reload_failed(
                f"`systemctl try-restart firewalld` exited {result.returncode}"
            )
            return
        self._reload_pending = False

    def managed_keys(self) -> dict:
        if self._is_ufw():
            return {self._DOMAIN: self._desired_ufw_rules() if self.enable else []}
        return {self._DOMAIN: self._declared_zones() if self.enable else []}

    def state_metadata(self) -> dict:
        """Which backend dasik actually applied (S3), for the reconciler to
        persist into the manifest — so a later plan on a disabled/absent
        block, whose own ``backend`` field defaults to "firewalld" and cannot
        be trusted, can read back the true history via
        ``_recorded_backend()`` instead of guessing from the shape of
        ``managed`` (kept only as the fallback for a manifest written before
        this field existed, dasik <= 0.18.0).

        N-13: this method's RETURN VALUE is computed on every ``plan()``, but
        it is only actually written to disk by the next apply that produces a
        non-empty plan — ``Reconciler.apply()`` returns before persisting a
        manifest when ``plan.is_empty()``, and ``_cmd_apply`` returns before
        even calling ``reconciler.apply()`` in that case. A no-op plan
        therefore never updates the recorded backend (there is nothing new to
        record — the existing one, if any, is still correct).
        """
        if self._teardown_backend not in ("ufw", "firewalld"):
            return {}
        return {self._DOMAIN: {"backend": self._teardown_backend}}

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

    def _customised_zones(self) -> "List[str]":
        """Zone names with a file in /etc/firewalld/zones on the target.

        firewalld only writes a file there once a zone has been *changed* — the
        untouched ones live in /usr/lib/firewalld/zones — so this is exactly
        "the zones somebody customised". It also leaves `<zone>.xml.old` behind
        when it rewrites one, hence the exact-suffix check: there is no zone
        called `home.xml`.
        """
        try:
            base = self._rooted(_ZONES_DIR)
            names = sorted(os.listdir(base))
        except OSError:
            return []
        return [n[:-len(".xml")] for n in names if n.endswith(".xml")]

    def _live_zone(self, zone: str):
        """`(services, rich_rules)` firewalld holds for *zone*, or None."""
        services_txt = self._fw_query(f"--zone={zone}", "--list-services")
        if services_txt is None:
            return None
        rich_txt = self._fw_query(f"--zone={zone}", "--list-rich-rules") or ""
        return (set(services_txt.split()),
                [ln.strip() for ln in rich_txt.splitlines() if ln.strip()])

    def _import_firewalld(self, managed=None) -> dict:
        """Capture the live firewalld permanent zones back into a `firewall` block.

        `--list-rich-rules` returns rules in the same syntax `rich_rules`
        expects, so they round-trip; for `public`, allowed/removed services are
        the diff against firewalld's upstream defaults. Every OTHER customised
        zone comes back under `zones` with its complete service list — dasik used
        to report only `public`, so a machine carrying a customised `home` lost
        it the moment its capture was re-applied. Nothing is captured when
        firewall-offline-cmd is unavailable (sync leaves the section untouched).
        """
        live = self._live_zone("public")
        if live is None:
            return {}
        services, rich = live

        frag: dict = {"enable": True}
        allowed = sorted(services - set(_DEFAULT_SERVICES))
        removed = sorted(set(_DEFAULT_SERVICES) - services)
        if allowed:
            frag["allowed_services"] = allowed
        if removed:
            frag["remove_services"] = removed
        if rich:
            frag["rich_rules"] = rich

        zones: dict = {}
        for zone in self._customised_zones():
            if zone == "public":
                continue
            other = self._live_zone(zone)
            if other is None:
                continue
            zone_services, zone_rich = other
            spec: dict = {"allowed_services": sorted(zone_services)}
            if zone_rich:
                spec["rich_rules"] = zone_rich
            zones[zone] = spec
        if zones:
            frag["zones"] = zones
        return {"firewall": frag}



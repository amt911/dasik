"""Action: install packages (pacman + AUR via makepkg in chroot).

The user declares **real package names only** — ``firefox``, ``yay``,
``claude-desktop-bin``. dasik resolves each name's origin (official repo,
pacman group, or AUR) at apply time via :class:`PackageResolver`; the config
never encodes the source. The same name keeps working if a package moves from
AUR into a repo (repo wins on the next apply).

Install routing at ``apply()``:
  1. Resolve the INSTALL set against the target's pacman sync DBs + the AUR RPC.
  2. **Abort before touching the target** if any name is unknown (a typo / a
     removed or purely-local package) or its source was unavailable (AUR
     unreachable — retryable). This is what stops a single bad name from aborting
     the whole ``pacman -S`` transaction and installing nothing.
  3. Install repo packages + groups in one ``pacman -S`` (``check=True`` so a
     real pacman failure surfaces in red and aborts instead of silently
     "succeeding").
  4. Install AUR packages via the makepkg dance (temp build user).

Legacy compatibility: an ``aur-<name>`` entry (the format written by pre-#161
syncs) is still accepted with a deprecation warning — normalized to ``<name>``
and forced to the AUR path for that read. ``sync`` re-emits it as the plain name.

Idempotent: a package already installed is not reinstalled.
"""
from __future__ import annotations
from typing import Any, Dict, List
from .abstract_action import AbstractAction
from .package_resolver import (
    AurUnavailableError,
    PackageResolution,
    PackageResolver,
)
from ..command_worker.command_worker import Command
from ..exceptions.exceptions import CommandExecutionError, ConfigValidationError
from ..logging import run_logger
import os
import re
import subprocess
import warnings
from contextlib import contextmanager


AUR_PREFIX = "aur-"

# Arch package names allow lowercase alphanumerics plus @ . _ + -, and must not
# start with - or . (pacman.conf(5) / PKGBUILD(5)). We accept upper-case too (some
# repos historically used it) but nothing else — anything with a shell metacharacter
# or a leading dash is refused so a config value can never reach a shell/argv unsafe.
_VALID_PKG_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9@._+-]*")


def _validate_pkg_name(name: str) -> str:
    """Return *name* if it is a safe Arch package name, else raise.

    Guards the AUR build path (name interpolated into `su -c "git clone …"`) and
    pacman's argv (a leading `-` would be parsed as a flag).
    """
    if not isinstance(name, str) or not _VALID_PKG_NAME.fullmatch(name):
        raise ConfigValidationError(
            f"Invalid package name {name!r}: package names must match "
            f"[A-Za-z0-9][A-Za-z0-9@._+-]* (no shell metacharacters, no leading '-')."
        )
    return name


class PackagesAction(AbstractAction):
    """Install pacman and AUR packages declaratively (source auto-resolved)."""

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        # Registered as ``__root__`` so it can read the sibling ``package_sources``
        # and ``package_policy`` maps. A plain list is still accepted (existing
        # unit tests / internal call-sites) and means "no sources, default policy".
        if isinstance(config, dict):
            raw: List[Any] = config.get("packages", []) or []
            self.package_sources: dict[str, Any] = config.get("package_sources", {}) or {}
            policy = config.get("package_policy", {}) or {}
            self.unknown_policy: str = policy.get("unknown", "warn-and-skip")
            self.build_failure_policy: str = policy.get("build_failure", "abort")
        else:
            raw = config if isinstance(config, list) else []
            self.package_sources = {}
            self.unknown_policy = "warn-and-skip"
            self.build_failure_policy = "abort"
        self._original = raw
        self._resolver = PackageResolver()
        # Names dropped by warn-and-skip during apply() (confirmed to exist in no
        # repo/group/AUR/source). Excluded from managed_keys() so the manifest
        # never claims dasik installed them; retried on the next apply.
        self._skipped_unknown: List[str] = []

        # desired: bare real names in declared order (deduped).
        # pacman_pkgs / aur_pkgs: informational split — aur_pkgs holds only names
        #   declared with the deprecated aur- prefix (legacy configs). New plain
        #   names all land in pacman_pkgs; their true origin is resolved at apply.
        self.desired: List[str] = []
        self.pacman_pkgs: List[str] = []
        self.aur_pkgs: List[str] = []
        self._reason: dict[str, str] = {}   # bare name -> "explicit"|"dep"
        self._legacy_aur: set[str] = set()  # names to force onto the AUR path
        # frozenset of queried names -> {group: {member, …}}; see _group_members.
        self._group_cache: dict[frozenset, dict[str, set[str]]] = {}
        # Packages declared `{"name": …, "optional": true}`: their install may
        # fail without aborting the apply (see apply()). Never claimed as managed.
        self.optional_packages: set[str] = set()
        # Optional packages whose install actually failed in this apply.
        self.failed_packages: List[str] = []
        seen: set[str] = set()

        for entry in raw:
            if isinstance(entry, dict):
                name, reason = entry["name"], entry.get("reason", "explicit")
                is_optional = bool(entry.get("optional", False))
            else:
                name, reason = entry, "explicit"
                is_optional = False
            _validate_pkg_name(name)

            is_legacy = name.startswith(AUR_PREFIX)
            bare = name[len(AUR_PREFIX):] if is_legacy else name
            if is_legacy:
                _validate_pkg_name(bare)
                warnings.warn(
                    f"The 'aur-' package prefix is deprecated ({name!r}); declare "
                    f"the plain name {bare!r} — dasik resolves the AUR source "
                    "automatically. sync will rewrite it without the prefix.",
                    DeprecationWarning,
                    stacklevel=2,
                )
                self._legacy_aur.add(bare)

            if bare in seen:
                continue
            seen.add(bare)
            self.desired.append(bare)
            if is_optional:
                self.optional_packages.add(bare)
            if is_legacy:
                self.aur_pkgs.append(bare)   # AUR: reason-exempt
            else:
                self.pacman_pkgs.append(bare)
                self._reason[bare] = reason

    @classmethod
    def empty_config(cls) -> Any:
        """Dict shape now that the action reads root config; ``__init__`` reads
        ``config['packages']`` so an empty dict means "no packages, default policy"."""
        return {}

    @property
    def name(self) -> str:
        return "Package Installation"

    @property
    def is_optional(self) -> bool:
        return True

    # ------------------------------------------------------------------ #
    #  helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _is_installed(pkg: str) -> bool:
        """Check if *pkg* is installed inside the chroot (legacy /mnt path)."""
        result = subprocess.run(
            ["arch-chroot", "/mnt", "pacman", "-Qi", pkg],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0

    def _missing(self, pkgs: List[str]) -> List[str]:
        return [p for p in pkgs if not self._is_installed(p)]

    # ------------------------------------------------------------------ #
    #  AUR helpers (legacy execute path)
    # ------------------------------------------------------------------ #

    _AUR_USER = "_aurbuilder"

    def _ensure_aur_prerequisites(self) -> None:
        """Install base-devel, git and create a temp build user."""
        Command.execute("pacman", ["--noconfirm", "--needed", "-S", "base-devel", "git"], run_as_chroot=True)

        # Create build user if it does not exist
        result = subprocess.run(
            ["arch-chroot", "/mnt", "id", self._AUR_USER],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            Command.execute("useradd", ["-m", "-r", "-s", "/bin/bash", self._AUR_USER], run_as_chroot=True)

        # Grant passwordless sudo
        sudoers_line = f"{self._AUR_USER} ALL=(ALL) NOPASSWD: ALL\n"
        sudoers_path = f"/mnt/etc/sudoers.d/{self._AUR_USER}"
        with open(sudoers_path, "w") as f:
            f.write(sudoers_line)

    def _install_aur_helper(self) -> str | None:
        """Install yay or paru if listed, return helper name or None."""
        for helper in ("yay", "paru"):
            if helper in self.aur_pkgs:
                if not self._is_installed(helper):
                    self._install_single_aur_pkg(helper)
                self.aur_pkgs.remove(helper)
                return helper
        return None

    @staticmethod
    def _su_argv(user: str, script: str, *args: str) -> List[str]:
        """Argv for ``su - <user> -c <script> -- sh`` with values passed as
        ``$1``.. positional parameters, NEVER interpolated into *script*.

        ``--`` terminates util-linux ``su`` option parsing before the shell's
        positional argv. Without it, ``su`` permutes and consumes helper flags
        such as ``-S`` itself (``su: invalid option -- 'S'``) instead of letting
        them reach ``exec "$@"``.

        Defense-in-depth on top of the package-name validation: even if a name
        with shell metacharacters ever slipped past _validate_pkg_name, it would
        arrive as inert data ($1, $2, …) and could not be executed as code. $0 is
        a conventional placeholder.
        """
        return ["su", "-", user, "-c", script, "--", "sh", *args]

    def _install_single_aur_pkg(self, pkg: str) -> None:
        """Clone and build a single AUR package as the build user."""
        build_dir = f"/home/{self._AUR_USER}/{pkg}"
        url = f"https://aur.archlinux.org/{pkg}.git"
        # Clean previous build
        subprocess.run(
            ["arch-chroot", "/mnt", "rm", "-rf", build_dir],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # Clone (url + dir are $1/$2, not interpolated into the shell)
        subprocess.run(
            ["arch-chroot", "/mnt", *self._su_argv(
                self._AUR_USER, 'git clone "$1" "$2"', url, build_dir)],
            check=True,
        )
        # Build and install ($1 = build dir)
        subprocess.run(
            ["arch-chroot", "/mnt", *self._su_argv(
                self._AUR_USER, 'cd "$1" && makepkg -sri --noconfirm', build_dir)],
            check=True,
        )

    def _install_aur_with_helper(self, helper: str, pkgs: List[str]) -> None:
        """Use yay/paru inside chroot to install AUR packages."""
        if not pkgs:
            return
        # `exec "$@"` runs helper + args straight from argv (no shell parsing of pkgs)
        subprocess.run(
            ["arch-chroot", "/mnt", *self._su_argv(
                self._AUR_USER, 'exec "$@"',
                helper, "-S", "--noconfirm", "--needed", *pkgs)],
            check=True,
        )

    def _cleanup_aur_user(self) -> None:
        """Remove the temp build user and its sudoers file."""
        import os
        subprocess.run(
            ["arch-chroot", "/mnt", "userdel", "-r", self._AUR_USER],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        sudoers_path = f"/mnt/etc/sudoers.d/{self._AUR_USER}"
        if os.path.exists(sudoers_path):
            os.remove(sudoers_path)

    # ------------------------------------------------------------------ #
    #  idempotency (legacy v2 shims)
    # ------------------------------------------------------------------ #


    # ------------------------------------------------------------------ #
    #  execute (legacy v2 path)
    # ------------------------------------------------------------------ #


    def verify(self) -> bool:
        return not self._missing(self.desired)

    # ------------------------------------------------------------------ #
    #  v3 interface                                                       #
    # ------------------------------------------------------------------ #

    _PACMAN_DOMAIN = "packages"

    def _explicit_raw(self) -> set[str]:
        """``pacman -Qqe`` and nothing else: the packages pacman calls EXPLICIT.

        Deliberately NOT :meth:`actual`, which widens the set with groups and
        with declared names a *provider* satisfies. That widening is what makes
        ownership work, and it is poison for the install-REASON question:
        ``pacman -T avahi`` says "satisfied" for an installed avahi whether it is
        explicit or a dependency, so every ``reason: dep`` package came back
        looking explicit and the reason MODIFY was re-planned on every single
        apply — plan, apply, plan, forever, running ``pacman -D --asdeps`` each
        time (found on a VM driving a real 243-package config).

        "Is this package explicit?" has exactly one source of truth, and this is
        it.
        """
        target = getattr(self.context, "target", None) if self.context else None
        if target is None:
            return set()
        result = Command.execute("pacman", ["-Qqe"], target=target)
        stdout = getattr(result, "stdout", b"") or b""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        return {line.strip() for line in stdout.splitlines() if line.strip()}

    def actual(self) -> set[str]:
        """Explicitly-installed packages (``pacman -Qqe``), plus every declared
        pacman **group** whose members are all installed.

        The group half is what keeps ownership. After a sync the reconciler
        records ``actual ∩ (owned ∪ declared)`` (``_owned_after_sync``), and no
        group name is ever an installed package — so without this every sync
        quietly dispossessed the group, and dropping it from the config
        afterwards removed nothing at all. Found by driving the matrix in a VM:
        every other step passed and only the removal was silent.

        A group is reported only when complete, for the same reason
        :meth:`plan` calls it converged only then.
        """
        target = getattr(self.context, "target", None) if self.context else None
        if target is None:
            return set()
        explicit = self._explicit_raw()
        groups = self._group_members(self.desired)
        if groups:
            installed = self._installed_all()
            explicit |= {name for name, members in groups.items()
                         if not members - installed}
        # Same reason as the group half: a DECLARED name that only a provider
        # satisfies is never in `pacman -Qqe`, so without this the reconciler
        # cannot own it — and dropping it from the config later removes nothing.
        # Declared names only: actual() answers for what was asked about.
        undeclared_by_name = [n for n in self.desired
                              if n not in explicit and n not in groups]
        explicit |= self._satisfied(undeclared_by_name, known_installed=explicit)
        return explicit

    def _installed_all(self) -> set[str]:
        """All installed packages (any reason): pacman -Qq."""
        target = getattr(self.context, "target", None) if self.context else None
        if target is None:
            return set()
        result = Command.execute("pacman", ["-Qq"], target=target)
        stdout = getattr(result, "stdout", b"") or b""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        return {line.strip() for line in stdout.splitlines() if line.strip()}

    def _satisfied(self, names: "set[str] | list[str]",
                   known_installed: "set[str] | None" = None) -> set:
        """Of *names*, the ones pacman already considers satisfied.

        `pacman -Qq` answers with package NAMES, so a name that survives only as
        a ``Provides`` — `iptables-nft`, which `iptables` in core provides and
        replaces — reads as missing forever: planned, applied, planned again,
        with every apply reporting success. `pacman -T` is the question that
        honours providers: it prints the dependencies NOT satisfied, so what
        disappears from its output is present.

        One call for the whole set, and only for names the installed list
        already failed to explain.
        """
        target = getattr(self.context, "target", None) if self.context else None
        wanted = sorted(names)
        if target is None or not wanted:
            return set()
        # An empty machine cannot be providing anything, and this is the case
        # that matters: on a fresh install the target is still an empty
        # directory, arch-chroot cannot set it up, and the probe comes back
        # rc!=0 with no output. Reading that as "nothing is unsatisfied" deleted
        # the ENTIRE packages domain from the plan — a guest installed base and
        # a bootloader, not one declared package, and reported rc=0.
        if not known_installed:
            return set()
        # Fail-safe in ONE direction: an answer we cannot read means "not
        # satisfied", so the name is planned and installed. The opposite would
        # silently skip an install because a probe failed, and `pacman -S` on
        # something already present is a no-op anyway. pacman's deptest speaks
        # exactly two exit codes — 0 (all satisfied) and 127 (these are missing)
        # — so anything else is not an answer to the question we asked.
        try:
            result = Command.execute("pacman", ["-T", *wanted], target=target)
            if getattr(result, "returncode", 1) not in (0, 127):
                return set()
            stdout = getattr(result, "stdout", b"") or b""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if not isinstance(stdout, str):
                return set()
            missing = {line.strip() for line in stdout.splitlines() if line.strip()}
        except Exception:      # nosec B110 - unreadable probe: nothing is satisfied
            return set()
        return set(wanted) - missing

    def _reason_of(self, pkg: str) -> str:
        """Install reason of an installed package: explicit if in -Qqe else dep."""
        return "explicit" if pkg in self.actual() else "dep"

    def _group_members(self, names) -> "dict[str, set[str]]":
        """``{group: {member, …}}`` for whichever *names* are pacman groups.

        `apply` has always understood groups (``PackageResolver.repo_groups``);
        `plan` and `import_state` did not, because both work from
        ``pacman -Qq``/``-Qqe``, which list *packages*. A declared ``xorg`` was
        therefore planned forever and rewritten into its members by the first
        sync. This is the missing half.

        One ``pacman -Sg a b c`` answers for every name at once, two columns
        (``<group> <member>``) per line. A name that is not a group contributes
        no rows — pacman reports it on stderr and carries on with the rest — so
        nothing here has to tell the two cases apart.

        A probe that cannot answer yields ``{}``, and every name then behaves as
        it did before groups were understood: planned as a package. That fails
        towards over-reporting a change; claiming convergence for a group nobody
        managed to check is the error worth avoiding. The membership map is
        therefore published only once the output has been read to the end — a
        read that dies half way through would otherwise leave a group holding
        part of its members, which is exactly the shape that reads as converged.
        """
        wanted = frozenset(names)
        if not wanted:
            return {}
        cached = self._group_cache.get(wanted)
        if cached is not None:
            return cached
        target = getattr(self.context, "target", None) if self.context else None
        members: dict[str, set[str]] = {}
        if target is not None:
            try:
                result = Command.execute(
                    "pacman", ["-Sg", *sorted(wanted)], target=target)
                out = getattr(result, "stdout", b"") or b""
                if isinstance(out, bytes):
                    out = out.decode("utf-8", errors="replace")
                parsed: dict[str, set[str]] = {}
                for line in out.splitlines():
                    parts = line.split()
                    if len(parts) == 2 and parts[0] in wanted:
                        parsed.setdefault(parts[0], set()).add(parts[1])
                members = parsed    # only a complete read is published
            except Exception:   # noqa: BLE001 - unanswerable probe = no groups
                members = {}
        self._group_cache[wanted] = members
        return members

    @staticmethod
    def _expand_group_removals(names, groups, installed) -> "list[str]":
        """*names* with every group replaced by its **installed** members.

        ``pacman -R xorg`` expands the group itself, so a plan that announced
        the bare group name would hide which packages actually leave — and
        would slip past :meth:`_removable`, whose whole job is to keep one
        undeletable member from aborting the entire apply.
        """
        out: list[str] = []
        for name in names:
            members = groups.get(name)
            if members is None:
                out.append(name)
                continue
            out.extend(sorted(m for m in members if m in installed))
        return out

    def _enabled_units(self) -> list[str]:
        """Unit files the target has enabled, bare templates excluded.

        `systemctl show` refuses a bare template (`getty@.service` is "neither a
        valid invocation ID nor unit name") and fails the WHOLE batch with it,
        so they are filtered here rather than downstream.
        """
        target = getattr(self.context, "target", None) if self.context else None
        if target is None:
            return []
        result = Command.execute(
            "systemctl", ["list-unit-files", "--state=enabled", "--no-legend"],
            target=target,
        )
        stdout = getattr(result, "stdout", b"") or b""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        units = [line.split()[0] for line in stdout.splitlines() if line.split()]
        return [u for u in units if "@." not in u]

    def _base_guaranteed(self) -> set[str]:
        """`base` and its direct dependencies.

        BaseInstallAction pacstraps `base` on every machine dasik builds, so
        these are present whether or not the config names them — capturing one
        adds an entry that changes nothing. Direct dependencies only: it is one
        cheap query and it covers what actually owns units (`systemd` owns
        systemd-oomd.service, `util-linux` owns fstrim.timer). An unreadable
        answer filters nothing, because capturing a redundant entry is a smaller
        error than dropping a real provider.
        """
        target = getattr(self.context, "target", None) if self.context else None
        if target is None:
            return set()
        try:
            result = Command.execute("pacman", ["-Qi", "base"], target=target)
            out = getattr(result, "stdout", b"") or b""
            if isinstance(out, bytes):
                out = out.decode("utf-8", errors="replace")
        except Exception:   # noqa: BLE001 - no base metapackage, no filtering
            return set()
        deps: set[str] = {"base"}
        for line in out.splitlines():
            field, _, value = line.partition(":")
            if field.strip() != "Depends On":
                continue
            for dep in value.split():
                # "glibc>=2.3" is still the package glibc.
                name = re.split(r"[<>=]", dep, maxsplit=1)[0]
                if name and name != "None":
                    deps.add(name)
        return deps

    def _unit_provider_packages(self) -> set[str]:
        """Packages owning the unit file of an enabled unit.

        `pacman -Qqe` lists only EXPLICIT packages, so a service pulled in as a
        dependency is invisible to the capture — on the machine that found this,
        `sddm` was a dependency of an orphaned `sddm-kcm` and the captured config
        re-installed a system with no graphical login. The enabled unit is the
        evidence that the package belongs in the config; ask pacman who owns the
        unit file rather than keeping a unit→package table.

        Two batched queries, not two per unit. Any probe failing (no systemctl
        on a half-built target, a path no package owns) yields nothing rather
        than losing the capture — this only ever ADDS to it.
        """
        target = getattr(self.context, "target", None) if self.context else None
        if target is None:
            return set()
        try:
            units = self._enabled_units()
            if not units:
                return set()
            shown = Command.execute(
                "systemctl", ["show", "-p", "FragmentPath", "--value", *units],
                target=target,
            )
            out = getattr(shown, "stdout", b"") or b""
            if isinstance(out, bytes):
                out = out.decode("utf-8", errors="replace")
            # A masked unit or an alias reports an empty FragmentPath; /etc is
            # the admin's own, where pacman owns nothing.
            paths = [p for p in (line.strip() for line in out.splitlines())
                     if p.startswith("/usr/")]
            if not paths:
                return set()
            owned = Command.execute("pacman", ["-Qqo", *paths], target=target)
            out = getattr(owned, "stdout", b"") or b""
            if isinstance(out, bytes):
                out = out.decode("utf-8", errors="replace")
            names = {line.strip() for line in out.splitlines() if line.strip()}
        except Exception:   # noqa: BLE001 - a probe failure must not lose packages
            return set()
        return (names & self._installed_all()) - self._base_guaranteed()

    def plan(self, managed):
        """Compute INSTALL/REMOVE/MODIFY over the desired (bare) package set.

        Source-agnostic: the plan lists names to install/remove; ``apply()``
        resolves each name's origin. ``pacman -Qqe`` sees repo and AUR packages
        alike once installed, so the installed check is uniform.

        A declared **pacman group** is the exception, because no group name is
        ever an installed package: it counts as satisfied when every one of its
        members is installed, and is planned as the group — one
        ``pacman -S xorg`` is the transaction ``apply`` actually runs.
        """
        from ..state.change import Change, Op

        desired = list(self.desired)
        installed = self._installed_all()
        explicit = self.actual()
        owned_not_declared = set(managed) - set(desired)
        # One query covering both directions, so a plan costs one `pacman -Sg`.
        groups = self._group_members(set(desired) | owned_not_declared)
        # A member of a DECLARED group is still declared — by the group. Without
        # this, replacing a captured member list with the group it came from
        # makes one apply install the group and then delete packages out of it,
        # because `apply` installs before it removes.
        covered: set = set()
        for name in desired:
            covered |= groups.get(name, set())
        removals = sorted(owned_not_declared - covered)

        changes: list = []
        candidates = sorted(n for n in desired if n not in installed)
        # A declared name can be satisfied by a PROVIDER rather than by a package
        # of that name, and then it is never in `pacman -Qq`. Asking only about
        # what the installed list failed to explain keeps this to one call.
        provided = self._satisfied([n for n in candidates if n not in groups],
                                   known_installed=installed)
        for name in candidates:
            if name in provided:
                continue    # an installed package provides it: converged
            members = groups.get(name)
            if members is not None and not members - installed:
                continue    # every member present: the group is converged
            changes.append(Change(self._PACMAN_DOMAIN, Op.INSTALL, name))
        # reason MODIFY: names carrying a declared reason (never the legacy AUR
        # ones), installed, whose reason drifted.
        # `explicit_raw`, not `explicit`: the latter is widened with providers
        # and groups for OWNERSHIP, and a provider-satisfied name is not the
        # same fact as an explicitly-installed one. See `_explicit_raw`.
        # Probed only when some package actually declares a reason — a config
        # without one must not pay for a `pacman -Qqe`, and on a machine that
        # has no pacman at all (CI) the query would raise.
        explicit_raw = self._explicit_raw() if self._reason else set()
        for name in sorted(self._reason):
            if name in installed:
                current = "explicit" if name in explicit_raw else "dep"
                if current != self._reason.get(name, "explicit"):
                    changes.append(Change(self._PACMAN_DOMAIN, Op.MODIFY, name,
                                          reason="install reason"))
        # Git source ref drift: an already-installed package whose declared
        # package_sources ref differs from the applied one (or was never
        # recorded) must be rebuilt at the pinned commit, name unchanged.
        applied_refs = self._applied_refs()
        for name in sorted(self.package_sources):
            if name in installed and applied_refs.get(name) != self.package_sources[name].get("ref"):
                changes.append(Change(self._PACMAN_DOMAIN, Op.MODIFY, name,
                                      reason=self._REF_CHANGED))
        expanded = self._expand_group_removals(removals, groups, installed)
        for name in self._removable(expanded, installed):
            changes.append(Change(self._PACMAN_DOMAIN, Op.REMOVE, name,
                                  reason="no longer declared"))
        return changes

    def _removable(self, names: "list[str]", installed: set) -> "list[str]":
        """*names* minus the ones pacman would refuse to remove.

        A package another INSTALLED package still requires cannot go, and
        `pacman -Rns` fails the whole transaction when one name in it is like
        that — so the apply aborts before any other domain runs and the same
        plan comes back forever. `audit` is the real case: dasik declares it for
        the `apparmor` block, and pam, systemd, shadow, dbus and NetworkManager
        all require it, so it can never leave an Arch system.

        A requirer that is itself being removed does not count — removing both
        together is a transaction pacman accepts.

        A probe that cannot answer changes nothing: the removal is planned and
        pacman gets to refuse it, exactly as before.
        """
        present = [n for n in names if n in installed]
        if not present:
            return list(names)
        required_by = self._required_by(present)
        going = set(names)
        keep: list = []
        for name in names:
            blockers = sorted(set(required_by.get(name, ())) - going)
            if not blockers:
                keep.append(name)
                continue
            run_logger.get().warning(
                f"not removing {name}: still required by "
                f"{', '.join(blockers)}",
                detail="pacman refuses a transaction that would break a "
                       "dependency, and one such name aborts the whole apply. "
                       "It stays installed and out of the plan; remove whatever "
                       "requires it first if you really want it gone.",
            )
        return keep

    def _required_by(self, names: "list[str]") -> "dict[str, list[str]]":
        """{package: [installed packages that require it]}, from one -Qi call."""
        target = getattr(self.context, "target", None) if self.context else None
        if target is None:
            return {}
        try:
            res = Command.execute("pacman", ["-Qi", *names], target=target)
        except Exception:      # nosec B110 - no pacman to ask: plan as before
            return {}
        out = getattr(res, "stdout", b"") or b""
        if isinstance(out, bytes):
            out = out.decode("utf-8", errors="replace")
        result: dict[str, list[str]] = {}
        current = None
        for line in out.splitlines():
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            if key == "Name":
                current = value
            elif key == "Required By" and current:
                result[current] = [] if value in ("None", "") else value.split()
        return result

    _REF_CHANGED = "source ref changed"

    def state_metadata(self) -> dict:
        """Per-action state for the manifest: what was built, and from where.

        ``source_refs`` (name -> applied SHA) answers "must this be rebuilt?".
        ``sources`` records the **whole** declaration (url/ref/subdir) because
        the SHA alone cannot rebuild anything: a package built from a Git
        PKGBUILD exists in no repo and no AUR, so a ``sync`` that cannot name
        its URL produces a config that silently drops it (PLAN v3 §10).

        Iterating only the current sources drops entries for packages no longer
        declared or no longer Git-sourced. Returns ``{}`` when there is nothing
        to record so the reconciler merge stays clean."""
        installed = self._installed_all()
        sources = {
            name: dict(src)
            for name, src in self.package_sources.items()
            if name in installed and isinstance(src, dict) and src.get("ref")
        }
        if not sources:
            return {}
        return {self._PACMAN_DOMAIN: {
            "source_refs": {name: src["ref"] for name, src in sources.items()},
            "sources": sources,
        }}

    def _action_state(self) -> dict:
        manifest = getattr(self.context, "manifest", None) if self.context else None
        if not isinstance(manifest, dict):
            return {}
        state = manifest.get("action_state", {}).get(self._PACMAN_DOMAIN, {})
        return state if isinstance(state, dict) else {}

    def _recorded_sources(self) -> dict:
        """{name: source} recorded by the last apply. Empty for a manifest
        written before ``sources`` existed — those hold a SHA and no URL, and a
        source cannot be invented from a SHA."""
        sources = self._action_state().get("sources", {})
        return sources if isinstance(sources, dict) else {}

    def _applied_refs(self) -> dict:
        """{name: applied_sha} recorded by the last apply, from the manifest.
        Reads the legacy ``source_refs`` map first so a manifest written by an
        older dasik still answers the ref-drift question."""
        refs = self._action_state().get("source_refs", {})
        if isinstance(refs, dict) and refs:
            return refs
        return {name: src["ref"] for name, src in self._recorded_sources().items()
                if isinstance(src, dict) and src.get("ref")}

    def managed_keys(self) -> dict:
        """Packages this action owns after apply (bare names).

        Excludes names dropped by warn-and-skip (``_skipped_unknown``) so the
        manifest never claims dasik installed a package it never could. Before
        apply, ``_skipped_unknown`` is empty and this is the full desired set."""
        skipped = set(self._skipped_unknown) | set(self.failed_packages)
        return {self._PACMAN_DOMAIN: [n for n in self.desired if n not in skipped]}

    def import_state(self, managed: "list[str] | None" = None) -> dict:
        """Capture reality into the config fragment (sync) as **real names**.

        Declared entries are kept as intent (a declared ``aur-`` prefix is dropped
        — the plain name is re-emitted). A repo package installed as a dependency
        becomes ``{name, reason: "dep"}``; everything else is a plain string.
        Undeclared explicit packages (``pacman -Qqe`` \\ declared, incl. AUR) are
        appended as plain names — no ``aur-`` prefix, because ``apply`` now
        resolves the source. Transitive dependencies are never captured.

        A declared **pacman group** is kept as the group, and its members are
        not re-emitted beside it: ``pacman -Qqe`` reports the members, and
        writing those back would replace the declaration with the thing it
        stands for — the next save would then have nothing left to keep. Members
        are covered whether or not the group is complete, because the config is
        intent: a half-installed group is a divergence for ``plan`` to report,
        not a reason for the capture to rewrite what the admin wrote.
        """
        explicit = self.actual()
        installed = self._installed_all()

        def _bare(name: str) -> str:
            return name[len(AUR_PREFIX):] if name.startswith(AUR_PREFIX) else name

        declared_names = {_bare(e["name"] if isinstance(e, dict) else e)
                          for e in self._original}
        covered: set = set()
        for members in self._group_members(declared_names).values():
            covered |= members

        result: list = []
        declared: set = set()
        for entry in self._original:
            raw_name = entry["name"] if isinstance(entry, dict) else entry
            bare = _bare(raw_name)
            declared.add(bare)
            spec: dict = {}
            if bare in installed and bare not in explicit:
                spec["reason"] = "dep"
            if bare in self.optional_packages:
                # `optional` is INTENT, not reality — keep it across a sync or the
                # next apply would abort on the very package marked non-blocking.
                spec["optional"] = True
            if spec:
                result.append({"name": bare, **spec})
            else:
                result.append(bare)   # explicit / intent (not installed)

        for name in sorted(explicit - declared - covered):   # new explicit packages
            if self._is_debug_by_product(name, installed):
                # `makepkg -si` builds and installs a split `-debug` package
                # alongside the real one (Arch's default makepkg.conf asks for
                # it). There is no `yay-debug` in any repo or in the AUR — it
                # only exists as a by-product of building `yay` on THIS machine
                # — so writing it into the config produces a capture that
                # cannot be applied anywhere: the name resolves nowhere.
                continue
            result.append(name)
        # …and whatever an enabled unit proves is there without being explicit.
        for name in sorted(self._unit_provider_packages()
                           - declared - explicit - covered):
            result.append({"name": name, "reason": "dep"})

        captured: Dict[str, Any] = {self._PACMAN_DOMAIN: result}
        sources = self._captured_sources(installed)
        if sources:
            captured["package_sources"] = sources
        return captured

    def _captured_sources(self, installed: set) -> dict:
        """The ``package_sources`` a sync must carry back.

        Declared beats recorded: the config is intent, so a ref the admin just
        bumped survives the capture instead of being overwritten by whatever the
        last apply happened to build. Only installed packages are reported —
        a source for something absent would describe a machine that does not
        exist.
        """
        out: Dict[str, Any] = {}
        recorded = self._recorded_sources()
        for name in sorted(set(recorded) | set(self.package_sources)):
            if name not in installed:
                continue
            src = self.package_sources.get(name) or recorded.get(name)
            if isinstance(src, dict) and src.get("url") and src.get("ref"):
                out[name] = dict(src)
        return out

    @staticmethod
    def _is_debug_by_product(name: str, installed: set) -> bool:
        """True for a `<pkg>-debug` whose `<pkg>` is installed beside it.

        A package that merely ends in -debug with no base next to it is
        somebody's real package and is captured like any other.
        """
        base = name[: -len("-debug")] if name.endswith("-debug") else ""
        return bool(base) and base in installed

    # ------------------------------------------------------------------ #
    #  v3 apply() — destructive                                          #
    # ------------------------------------------------------------------ #

    def _resolve_sources(self, names: List[str], target) -> PackageResolution:
        """Resolve INSTALL *names* into repo/group/AUR/unknown/unavailable.

        Names declared with the deprecated ``aur-`` prefix bypass the resolver and
        are forced onto the AUR path; every other name is classified live."""
        to_resolve = [n for n in names if n not in self._legacy_aur]
        if to_resolve:
            resolution = self._resolver.resolve(
                to_resolve, target, sources=self.package_sources)
        else:
            resolution = PackageResolution()
        for name in names:
            if name in self._legacy_aur and name not in resolution.aur:
                resolution.aur.append(name)
        return resolution

    @staticmethod
    def _abort_unavailable(resolution: PackageResolution) -> None:
        """Abort because a source could not be *reached* (AUR unavailable).

        Always blocking, regardless of package_policy: we do not know whether the
        package exists, so skipping would be wrong. Retry once the source is back.
        """
        raise CommandExecutionError(
            "Refusing to install — source unavailable (existence could not be "
            "checked, retry): " + ", ".join(sorted(resolution.unavailable))
        )

    @staticmethod
    def _abort_unknown(resolution: PackageResolution) -> None:
        """Abort on a confirmed-unknown name under the strict ``error`` policy."""
        raise CommandExecutionError(
            "Refusing to install — unknown (not found in any configured repo, "
            "group, package_sources or the AUR): "
            + ", ".join(sorted(resolution.unknown))
        )

    def _handle_unknown(self, resolution: PackageResolution) -> None:
        """Apply ``package_policy.unknown`` to confirmed-unknown names.

        ``error`` aborts before any mutation; ``warn-and-skip`` records them,
        warns once (visible + logged), and lets the resolvable names install.
        A name declared ``optional`` is always skipped — being non-blocking is
        exactly what the flag declares, so even the strict policy honours it."""
        if not resolution.unknown:
            return
        required_unknown = [n for n in resolution.unknown
                            if n not in self.optional_packages]
        if required_unknown and self.unknown_policy == "error":
            self._abort_unknown(resolution)
        skipped = sorted(resolution.unknown)
        self._skipped_unknown = skipped
        run_logger.get().warning(
            "packages skipped because no source was found: " + ", ".join(skipped),
            detail="They were not installed; dasik will retry them on the next apply.",
        )

    @staticmethod
    def _print_resolution_split(resolution: PackageResolution) -> None:
        """Say where every INSTALL name resolved, before anything runs.

        On 2026-08-18 four lib32 packages silently migrated from multilib to
        the AUR and nobody could tell until yay ran. One line makes the split
        visible in every apply log."""
        counts = (f"{len(resolution.repo)} repo, {len(resolution.groups)} "
                  f"groups, {len(resolution.git)} git, "
                  f"{len(resolution.aur)} AUR")
        aur = ", ".join(resolution.aur) if resolution.aur else "none"
        print(f"[packages] resolved sources: {counts}; AUR packages: {aur}")

    def _gate_aur_closure(self, aur_installs: "list[str]", target) -> "list[str]":
        """Validate the transitive dependency closure of the AUR roots.

        A broken chain rooted in a REQUIRED package aborts with every chain in
        the message; chains rooted only in ``optional: true`` packages degrade
        to a warning, the roots drop out of the batch and land in
        ``failed_packages`` (excluded from ``managed_keys``, retried next
        apply). An unreachable RPC always aborts: we do not know whether the
        closure is satisfiable, and the helper would need the RPC anyway."""
        if not aur_installs:
            return aur_installs
        from ..validation.aur_closure import validate_aur_closure
        try:
            broken = validate_aur_closure(aur_installs, self._resolver, target)
        except AurUnavailableError as e:
            raise CommandExecutionError(
                "Refusing to install — AUR unavailable while validating the "
                f"dependency closure (existence could not be checked, retry): {e}"
            ) from e
        if not broken:
            return aur_installs
        rendered = [b.render() for b in broken]
        required_broken = any(b.chain[0] not in self.optional_packages
                              for b in broken)
        if required_broken and not self._continue_on_failure:
            raise CommandExecutionError(
                "Refusing to install — unsatisfiable AUR dependency chain(s):\n  "
                + "\n  ".join(rendered)
            )
        bad_roots = {b.chain[0] for b in broken}
        run_logger.get().warning(
            "AUR packages skipped — unsatisfiable dependency chain: "
            + "; ".join(rendered),
            detail="They were not installed; dasik will retry them on the next "
                   "apply.",
        )
        self.failed_packages.extend(
            n for n in aur_installs
            if n in bad_roots and n not in self.failed_packages)
        return [n for n in aur_installs if n not in bad_roots]

    def apply(self, changes) -> None:
        """Execute a list of ``Change`` objects against the target.

        Installs (repo first, then AUR) run before removals so an additive step
        keeps the system working if a destructive step fails midway. The INSTALL
        set is resolved and validated *before* any mutation: an unknown or
        unavailable name aborts the whole apply with nothing installed.
        """
        from ..state.change import Op

        target = getattr(self.context, "target", None) if self.context else None
        if target is None or not changes:
            return

        self._refuse_a_locked_database(target)
        install_names = [c.item for c in changes if c.op is Op.INSTALL]
        # Two kinds of MODIFY: a Git source-ref change (rebuild) vs an install-
        # reason change (pacman -D). Keep them apart — a rebuild is not a -D.
        ref_modifies = [c.item for c in changes
                        if c.op is Op.MODIFY and c.reason == self._REF_CHANGED]
        modifies = [c.item for c in changes
                    if c.op is Op.MODIFY and c.reason != self._REF_CHANGED]
        removes = [c.item for c in changes if c.op is Op.REMOVE]

        repo_installs: list[str] = []
        aur_installs: list[str] = []
        git_installs: list = []
        self._skipped_unknown = []
        self.failed_packages = []
        if install_names:
            resolution = self._resolve_sources(install_names, target)
            # unavailable is ALWAYS blocking; unknown follows package_policy.
            # Both are decided before the first mutation.
            if resolution.unavailable:
                self._abort_unavailable(resolution)
            self._handle_unknown(resolution)
            repo_installs = resolution.repo + resolution.groups
            aur_installs = resolution.aur
            git_installs = resolution.git
            self._print_resolution_split(resolution)
            # The 2026-08-18 gate: a declared AUR package whose transitive dep
            # chain ends in a name nothing satisfies must abort HERE — with the
            # chain in the error — not 25 minutes in, mid-yay-transaction.
            aur_installs = self._gate_aur_closure(aur_installs, target)

        required_repo, optional_repo = self._split_optional(repo_installs)
        if required_repo:
            try:
                Command.execute(
                    "pacman",
                    ["--noconfirm", "--needed", "-S", *required_repo],
                    target=target,
                    check=True,
                    stream=True,
                )
            except (CommandExecutionError, ConfigValidationError):
                if not self._continue_on_failure:
                    raise
                # Salvage: one transaction per package (--needed makes the
                # already-installed ones free), recording exactly the failures.
                for pkg in required_repo:
                    try:
                        Command.execute(
                            "pacman",
                            ["--noconfirm", "--needed", "-S", pkg],
                            target=target,
                            check=True,
                            stream=True,
                        )
                    except (CommandExecutionError, ConfigValidationError) as exc:
                        self._record_failure(pkg, exc)
        # Optional repo packages go one at a time, AFTER the required transaction:
        # a single broken name must not take the others (or the install) down.
        for pkg in optional_repo:
            with self._failure_guard([pkg]):
                Command.execute(
                    "pacman",
                    ["--noconfirm", "--needed", "-S", pkg],
                    target=target,
                    check=True,
                    stream=True,
                )

        # Git builds: fresh installs (resolution.git) + ref-change rebuilds. The
        # rebuild sources come from package_sources by name.
        from .package_resolver import ResolvedGitPackage
        rebuilds = [ResolvedGitPackage(name=n, source=self.package_sources[n])
                    for n in ref_modifies if n in self.package_sources]
        all_git = list(git_installs) + rebuilds
        if all_git and self._continue_on_failure:
            for pkg in all_git:
                try:
                    self._apply_git_install([pkg])
                except (CommandExecutionError, ConfigValidationError) as exc:
                    self._record_failure(pkg.name, exc)
        elif all_git:
            self._apply_git_install(all_git)

        if aur_installs:
            # The helper is chosen from the full DESIRED set, not from the delta:
            # a previous failed apply may already have installed it, in which case
            # it is not an INSTALL change but must still drive the rest.
            from .aur_installer import AurInstaller
            skipped = set(self._skipped_unknown)
            helper = next(
                (name for name in AurInstaller.HELPERS
                 if name in self.desired and name not in skipped),
                None,
            )
            required_aur, optional_aur = self._split_optional(aur_installs)
            if required_aur and self._continue_on_failure:
                # The helper already salvages within its batch (it builds what
                # it can and lists the failures); the guard turns its non-zero
                # exit into recorded failures instead of an abort.
                with self._failure_guard(required_aur):
                    self._apply_aur_install(required_aur, helper=helper)
            elif required_aur:
                self._apply_aur_install(required_aur, helper=helper)
            # Optional AUR packages build in their own batch: on 2026-07-19 three
            # peripheral packages (sunshine, two Epson drivers) made yay exit 1 and
            # that exception stopped the reconciler before users, initramfs and
            # bootloader ever ran.
            if optional_aur:
                with self._failure_guard(optional_aur):
                    self._apply_aur_install(optional_aur, helper=helper)

        self._enforce_reasons(target, planned_modifies=modifies,
                              installed_now=repo_installs)

        if removes:
            Command.execute(
                "pacman",
                ["--noconfirm", "-Rns", *removes],
                target=target,
                check=True,
                stream=True,
            )

        if self.failed_packages:
            # The reviewable summary the policy promises: one line, at the end
            # of the domain, naming exactly what is NOT on the machine. These
            # names stay out of the manifest, so the next plan shows them too.
            print("[packages] not installed this apply (will be retried): "
                  + ", ".join(self.failed_packages))

    @property
    def _continue_on_failure(self) -> bool:
        return self.build_failure_policy == "warn-and-continue"

    def _record_failure(self, name: str, exc: Exception) -> None:
        if name not in self.failed_packages:
            self.failed_packages.append(name)
        run_logger.get().error(
            f"package not installed: {name}",
            detail=f"{exc}\nIt is NOT recorded as installed; the next apply "
                   "retries it. Convergence continues.",
        )

    _DB_LOCK = "/var/lib/pacman/db.lck"

    def _refuse_a_locked_database(self, target) -> None:
        """Say what the lock is before pacman says `File exists`.

        A machine that lost power mid-apply comes back with db.lck still there —
        pacman never got to remove it — and every apply after that dies with

            error: could not lock database: File exists

        which names no file, no cause and no fix, at exactly the moment somebody
        is trying to recover a half-installed machine.

        dasik does NOT delete it: a lock can also mean a pacman is genuinely
        running, and guessing wrong there corrupts a package database.
        """
        try:
            path = target.path(self._DB_LOCK)
        except AttributeError:          # a target double without path()
            return
        if not os.path.exists(path):
            return
        if self._pacman_is_running():
            raise CommandExecutionError(
                f"another pacman is already running (lock: {self._DB_LOCK}). "
                "Wait for it to finish and apply again."
            )
        raise CommandExecutionError(
            f"pacman's database is locked by {self._DB_LOCK}, and no pacman is "
            "running — the lock is left over from a run that was interrupted "
            "(a crash, a power cut, a killed apply). Check that nothing is "
            f"installing, remove the file (`rm {self._DB_LOCK}`) and apply "
            "again. dasik will not remove it for you: if a pacman really is "
            "running, deleting it corrupts the package database."
        )

    @staticmethod
    def _pacman_is_running() -> bool:
        """Best effort: any live pacman process, host-side (arch-chroot runs it
        there too). Unknowable -> assume none, so the message is the stale one."""
        try:
            res = subprocess.run(["pgrep", "-x", "pacman"],  # nosec B603, B607
                                 capture_output=True, check=False)
            return res.returncode == 0
        except Exception:      # noqa: BLE001 - no pgrep: cannot tell
            return False

    def _enforce_reasons(self, target, planned_modifies: "list[str]",
                         installed_now: "list[str]") -> None:
        """Make every declared package's install reason true, now.

        Read from REALITY after the transaction, not from the plan. The plan is
        computed before anything runs, so it cannot know that pacman will bring
        a declared package in as a dependency of another one — `audit` arrives
        with `apparmor`, and `pacman -S --needed` leaves an already-present
        package's reason alone. Computing this from the plan left the correction
        for the *next* apply: an apply that exits 0 and is not a no-op (#188).

        Two things are known without probing and stay: the plan's own reason
        MODIFY set, and what `pacman -S` just installed — it marks every one of
        them explicit, so a dep-declared install needs the correction whatever
        the probes report (and an explicit one needs nothing).
        """
        try:
            installed = self._installed_all()
            explicit = self._explicit_raw()
        except Exception:      # noqa: BLE001 - no pacman to ask (a half-built
            # target, a unit test): fall back to what this apply already knows.
            # A probe that cannot answer must not abort an otherwise good apply.
            installed, explicit = set(), set()
        planned = set(planned_modifies)
        fresh = set(installed_now)
        candidates = set(self._reason) | planned | fresh

        to_dep = sorted(
            p for p in candidates
            if self._reason.get(p, "explicit") == "dep"
            and (p in explicit or p in planned or p in fresh)
        )
        to_explicit = sorted(
            p for p in candidates
            if self._reason.get(p, "explicit") == "explicit"
            and ((p in installed and p not in explicit) or p in planned)
        )
        if to_dep:
            Command.execute("pacman", ["-D", "--asdeps", *to_dep], target=target, check=True)
        if to_explicit:
            Command.execute("pacman", ["-D", "--asexplicit", *to_explicit],
                            target=target, check=True)

    def _split_optional(self, names: "list[str]") -> "tuple[list[str], list[str]]":
        """(required, optional) preserving order."""
        required = [n for n in names if n not in self.optional_packages]
        optional = [n for n in names if n in self.optional_packages]
        return required, optional

    @contextmanager
    def _failure_guard(self, batch: "list[str]"):
        """Run an optional install batch; on failure record the packages that are
        still missing and continue.

        The failure is loud (red + log) and the packages are excluded from
        ``managed_keys()``, so the manifest never claims them and the next plan
        retries them — a visible divergence, not a silent "converged"."""
        try:
            yield
        except (CommandExecutionError, ConfigValidationError) as exc:
            installed = self._installed_all()
            missing = [p for p in batch if p not in installed]
            self.failed_packages.extend(m for m in missing
                                        if m not in self.failed_packages)
            run_logger.get().error(
                "optional packages not installed: " + ", ".join(missing),
                detail=f"{exc}\nThey are NOT recorded as installed; the next "
                       "apply retries them. Convergence continues.",
            )

    def _apply_git_install(self, git_pkgs: list) -> None:
        """Build+install ``package_sources`` (pkgbuild-git) packages via the
        dedicated installer (pinned checkout, identity check, unprivileged build)."""
        if self.context is None or self.context.target is None:
            raise CommandExecutionError(
                "Git package install requires an action context with a target."
            )
        from .pkgbuild_git_installer import PkgbuildGitInstaller
        PkgbuildGitInstaller(self.context.target,
                             build_deps=self._install_aur_build_deps).install(git_pkgs)

    def _install_aur_build_deps(self, deps: list) -> None:
        """Install a git package's declared build deps that no repository has.

        `makepkg -s` syncs dependencies with pacman, so a makedepends living in
        the AUR aborts the build ("target not found") no matter where the
        package sits in the install order — git builds run before the AUR batch,
        and reordering them would only move the problem to AUR packages that
        depend on a git one. Repo dependencies are left alone: makepkg syncs
        those itself, and doing it twice only slows the build down.
        """
        target = getattr(self.context, "target", None) if self.context else None
        if target is None or not deps:
            return
        resolution = self._resolve_sources(list(deps), target)
        if resolution.aur:
            # The git installer wrote the sudoers fragment and still needs it
            # after we return, so its presence is not a dead build's leftover.
            self._apply_aur_install(list(resolution.aur), fragment_is_ours=True)

    def _apply_aur_install(self, pkgs: list[str], *,
                           helper: "str | None" = None,
                           fragment_is_ours: bool = False) -> None:
        """Install ``resolution.aur`` via the hybrid :class:`AurInstaller`.

        *helper* is the declared yay/paru chosen from the full desired set (it may
        already be installed from an earlier partial apply); ``None`` means the
        installer resolves transitive AUR deps itself. It builds unprivileged,
        streams makepkg output, and always cleans up the temp build user + sudoers
        on the way out. The heavy logic lives there so this action stays a thin
        router (see ``aur_installer.py``).
        """
        if self.context is None or self.context.target is None:
            raise CommandExecutionError(
                "AUR install requires an action context with a target."
            )
        from .aur_installer import AurInstaller
        AurInstaller(self.context.target, resolver=self._resolver).install(
            pkgs, helper=helper, fragment_is_ours=fragment_is_ours)

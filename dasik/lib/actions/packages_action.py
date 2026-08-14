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
        else:
            raw = config if isinstance(config, list) else []
            self.package_sources = {}
            self.unknown_policy = "warn-and-skip"
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
        # Packages declared `{"name": …, "optional": true}`: their install may
        # fail without aborting the apply (see apply()). Never claimed as managed.
        self.optional_packages: set[str] = set()
        # Optional packages whose install actually failed in this apply.
        self.failed_optional: List[str] = []
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

    def is_needed(self) -> bool:
        return bool(self._missing(self.desired))

    # ------------------------------------------------------------------ #
    #  execute (legacy v2 path)
    # ------------------------------------------------------------------ #

    def execute(self) -> None:
        # 1. Official packages ------------------------------------------------
        missing_pacman = self._missing(self.pacman_pkgs)
        if missing_pacman:
            print(f"  Installing {len(missing_pacman)} official packages …")
            Command.execute(
                "pacman",
                ["--noconfirm", "--needed", "-S"] + missing_pacman,
                run_as_chroot=True,
            )

        # 2. AUR packages -----------------------------------------------------
        missing_aur = self._missing(self.aur_pkgs)
        if not missing_aur:
            return

        print(f"  Installing {len(missing_aur)} AUR packages …")
        self._ensure_aur_prerequisites()

        # Try to install an AUR helper first (yay/paru)
        helper = self._install_aur_helper()

        if helper:
            self._install_aur_with_helper(helper, missing_aur)
        else:
            # Fallback: build each AUR package individually via makepkg
            for pkg in missing_aur:
                if not self._is_installed(pkg):
                    print(f"    Building AUR package: {pkg}")
                    self._install_single_aur_pkg(pkg)

        self._cleanup_aur_user()

    def verify(self) -> bool:
        return not self._missing(self.desired)

    # ------------------------------------------------------------------ #
    #  v3 interface                                                       #
    # ------------------------------------------------------------------ #

    _PACMAN_DOMAIN = "packages"

    def actual(self) -> set[str]:
        """Set of explicitly-installed packages on the target (``pacman -Qqe``)."""
        target = getattr(self.context, "target", None) if self.context else None
        if target is None:
            return set()
        result = Command.execute("pacman", ["-Qqe"], target=target)
        stdout = getattr(result, "stdout", b"") or b""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        return {line.strip() for line in stdout.splitlines() if line.strip()}

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

    def _reason_of(self, pkg: str) -> str:
        """Install reason of an installed package: explicit if in -Qqe else dep."""
        return "explicit" if pkg in self.actual() else "dep"

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
        """
        from ..state.change import Change, Op

        desired = list(self.desired)
        installed = self._installed_all()
        explicit = self.actual()

        changes: list = []
        for name in sorted(n for n in desired if n not in installed):
            changes.append(Change(self._PACMAN_DOMAIN, Op.INSTALL, name))
        # reason MODIFY: names carrying a declared reason (never the legacy AUR
        # ones), installed, whose reason drifted.
        for name in sorted(self._reason):
            if name in installed:
                current = "explicit" if name in explicit else "dep"
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
        removals = sorted(set(managed) - set(desired))
        for name in self._removable(removals, installed):
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
        skipped = set(self._skipped_unknown) | set(self.failed_optional)
        return {self._PACMAN_DOMAIN: [n for n in self.desired if n not in skipped]}

    def import_state(self, managed: "list[str] | None" = None) -> dict:
        """Capture reality into the config fragment (sync) as **real names**.

        Declared entries are kept as intent (a declared ``aur-`` prefix is dropped
        — the plain name is re-emitted). A repo package installed as a dependency
        becomes ``{name, reason: "dep"}``; everything else is a plain string.
        Undeclared explicit packages (``pacman -Qqe`` \\ declared, incl. AUR) are
        appended as plain names — no ``aur-`` prefix, because ``apply`` now
        resolves the source. Transitive dependencies are never captured.
        """
        explicit = self.actual()
        installed = self._installed_all()

        def _bare(name: str) -> str:
            return name[len(AUR_PREFIX):] if name.startswith(AUR_PREFIX) else name

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

        for name in sorted(explicit - declared):   # new explicit packages
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
        for name in sorted(self._unit_provider_packages() - declared - explicit):
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
        self.failed_optional = []
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

        required_repo, optional_repo = self._split_optional(repo_installs)
        if required_repo:
            Command.execute(
                "pacman",
                ["--noconfirm", "--needed", "-S", *required_repo],
                target=target,
                check=True,
                stream=True,
            )
        # Optional repo packages go one at a time, AFTER the required transaction:
        # a single broken name must not take the others (or the install) down.
        for pkg in optional_repo:
            with self._optional_guard([pkg]):
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
        if all_git:
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
            if required_aur:
                self._apply_aur_install(required_aur, helper=helper)
            # Optional AUR packages build in their own batch: on 2026-07-19 three
            # peripheral packages (sunshine, two Epson drivers) made yay exit 1 and
            # that exception stopped the reconciler before users, initramfs and
            # bootloader ever ran.
            if optional_aur:
                with self._optional_guard(optional_aur):
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
            explicit = self.actual()
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
    def _optional_guard(self, batch: "list[str]"):
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
            self.failed_optional.extend(m for m in missing
                                        if m not in self.failed_optional)
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
        PkgbuildGitInstaller(self.context.target).install(git_pkgs)

    def _apply_aur_install(self, pkgs: list[str], *,
                           helper: "str | None" = None) -> None:
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
            pkgs, helper=helper)

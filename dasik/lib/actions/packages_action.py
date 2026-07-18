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
from typing import Any, List
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
        seen: set[str] = set()

        for entry in raw:
            if isinstance(entry, dict):
                name, reason = entry["name"], entry.get("reason", "explicit")
            else:
                name, reason = entry, "explicit"
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
        """Argv for ``su - <user> -c <script>`` with values passed as ``$1``..
        positional parameters, NEVER interpolated into *script*.

        Defense-in-depth on top of the package-name validation: even if a name
        with shell metacharacters ever slipped past _validate_pkg_name, it would
        arrive as inert data ($1, $2, …) and could not be executed as code. $0 is
        a conventional placeholder.
        """
        return ["su", "-", user, "-c", script, "sh", *args]

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
        for name in sorted(set(managed) - set(desired)):
            changes.append(Change(self._PACMAN_DOMAIN, Op.REMOVE, name,
                                  reason="no longer declared"))
        return changes

    _REF_CHANGED = "source ref changed"

    def state_metadata(self) -> dict:
        """Per-action state for the manifest: the applied Git SHA of every
        declared ``package_sources`` package that is installed (PLAN v3 §10).

        Iterating only the current sources drops refs for packages no longer
        declared or no longer Git-sourced. Returns ``{}`` when there is nothing
        to record so the reconciler merge stays clean."""
        installed = self._installed_all()
        refs = {
            name: src["ref"]
            for name, src in self.package_sources.items()
            if name in installed and isinstance(src, dict) and src.get("ref")
        }
        if not refs:
            return {}
        return {self._PACMAN_DOMAIN: {"source_refs": refs}}

    def _applied_refs(self) -> dict:
        """{name: applied_sha} recorded by the last apply, from the manifest."""
        manifest = getattr(self.context, "manifest", None) if self.context else None
        if not isinstance(manifest, dict):
            return {}
        return (manifest.get("action_state", {})
                        .get(self._PACMAN_DOMAIN, {})
                        .get("source_refs", {}))

    def managed_keys(self) -> dict:
        """Packages this action owns after apply (bare names).

        Excludes names dropped by warn-and-skip (``_skipped_unknown``) so the
        manifest never claims dasik installed a package it never could. Before
        apply, ``_skipped_unknown`` is empty and this is the full desired set."""
        skipped = set(self._skipped_unknown)
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
            if bare in installed and bare not in explicit:
                result.append({"name": bare, "reason": "dep"})
            else:
                result.append(bare)   # explicit / intent (not installed)

        for name in sorted(explicit - declared):   # new explicit packages
            result.append(name)
        return {self._PACMAN_DOMAIN: result}

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
        warns once (visible + logged), and lets the resolvable names install."""
        if not resolution.unknown:
            return
        if self.unknown_policy == "error":
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

        if repo_installs:
            Command.execute(
                "pacman",
                ["--noconfirm", "--needed", "-S", *repo_installs],
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
            self._apply_aur_install(aur_installs)

        # Enforce install reason (repo packages only). -S marks explicit by
        # default, so a fresh explicit install needs no -D; a dep install does.
        to_dep = [p for p in repo_installs if self._reason.get(p, "explicit") == "dep"]
        to_dep += [p for p in modifies if self._reason.get(p, "explicit") == "dep"]
        to_explicit = [p for p in modifies if self._reason.get(p, "explicit") == "explicit"]
        if to_dep:
            Command.execute("pacman", ["-D", "--asdeps", *to_dep], target=target, check=True)
        if to_explicit:
            Command.execute("pacman", ["-D", "--asexplicit", *to_explicit], target=target, check=True)

        if removes:
            Command.execute(
                "pacman",
                ["--noconfirm", "-Rns", *removes],
                target=target,
                check=True,
                stream=True,
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

    def _apply_aur_install(self, pkgs: list[str]) -> None:
        """Install AUR packages via the makepkg dance (target-aware).

        Steps:
          1. Ensure base-devel + git installed on the target.
          2. Ensure the temp build user exists (passwordless sudo via sudoers.d).
          3. For each pkg: clone + makepkg -sri as the build user.
          4. Remove the temp build user + sudoers fragment.
        """
        if self.context is None or self.context.target is None:
            raise CommandExecutionError(
                "AUR install requires an action context with a target."
            )
        target = self.context.target

        # 1. Prerequisites
        Command.execute(
            "pacman",
            ["--noconfirm", "--needed", "-S", "base-devel", "git"],
            target=target,
        )

        # 2. Build user
        id_check = subprocess.run(
            self._target_argv(target, ["id", self._AUR_USER]),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if id_check.returncode != 0:
            Command.execute(
                "useradd",
                ["-m", "-r", "-s", "/bin/bash", self._AUR_USER],
                target=target,
            )

        sudoers_path = target.path(f"/etc/sudoers.d/{self._AUR_USER}")
        with open(sudoers_path, "w") as f:
            f.write(f"{self._AUR_USER} ALL=(ALL) NOPASSWD: ALL\n")

        # 3. Build each (url + build dir are $1/$2, never in the shell script)
        for pkg in pkgs:
            build_dir = f"/home/{self._AUR_USER}/{pkg}"
            url = f"https://aur.archlinux.org/{pkg}.git"
            subprocess.run(
                self._target_argv(target, ["rm", "-rf", build_dir]),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                self._target_argv(target, self._su_argv(
                    self._AUR_USER, 'git clone "$1" "$2"', url, build_dir)),
                check=True,
            )
            subprocess.run(
                self._target_argv(target, self._su_argv(
                    self._AUR_USER, 'cd "$1" && makepkg -sri --noconfirm', build_dir)),
                check=True,
            )

        # 4. Cleanup
        subprocess.run(
            self._target_argv(target, ["userdel", "-r", self._AUR_USER]),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if os.path.exists(sudoers_path):
            os.remove(sudoers_path)

    @staticmethod
    def _target_argv(target, cmd: list[str]) -> list[str]:
        """Prefix ``arch-chroot <root>`` when target is a chroot, else passthrough."""
        if target.is_chroot:
            return ["arch-chroot", target.root, *cmd]
        return list(cmd)

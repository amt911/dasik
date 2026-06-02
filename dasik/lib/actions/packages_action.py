"""Action: install packages (pacman + AUR via makepkg in chroot).

The package list mixes normal pacman packages with AUR ones.
AUR packages are identified by the ``aur-`` prefix; the prefix is
stripped before installation.

AUR strategy **from inside arch-chroot**:
  1. Ensure ``base-devel git`` are installed.
  2. Create a temporary build user (``_aurbuilder``) with passwordless sudo.
  3. For each AUR package, clone the PKGBUILD and run ``makepkg -sri``
     as that user.  Dependencies that are themselves AUR are resolved
     recursively by sorting the list so that deps come first (or by
     installing paru/yay first if it is in the list).
  4. Remove the temp user at the end.

Idempotent: a package is skipped if ``pacman -Qi <pkg>`` inside the
chroot already shows it installed.
"""
from __future__ import annotations
from typing import Any, List
from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command
import os
import subprocess


AUR_PREFIX = "aur-"


class PackagesAction(AbstractAction):
    """Install pacman and AUR packages declaratively."""

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        raw: List[str] = config if isinstance(config, list) else []
        self.pacman_pkgs: List[str] = []
        self.aur_pkgs: List[str] = []
        for pkg in raw:
            if pkg.startswith(AUR_PREFIX):
                self.aur_pkgs.append(pkg[len(AUR_PREFIX):])
            else:
                self.pacman_pkgs.append(pkg)

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
        """Check if *pkg* is installed inside the chroot."""
        result = subprocess.run(
            ["arch-chroot", "/mnt", "pacman", "-Qi", pkg],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0

    def _missing(self, pkgs: List[str]) -> List[str]:
        return [p for p in pkgs if not self._is_installed(p)]

    # ------------------------------------------------------------------ #
    #  AUR helpers
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

    def _install_single_aur_pkg(self, pkg: str) -> None:
        """Clone and build a single AUR package as the build user."""
        build_dir = f"/home/{self._AUR_USER}/{pkg}"
        # Clean previous build
        subprocess.run(
            ["arch-chroot", "/mnt", "rm", "-rf", build_dir],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # Clone
        subprocess.run(
            ["arch-chroot", "/mnt", "su", "-", self._AUR_USER, "-c",
             f"git clone https://aur.archlinux.org/{pkg}.git {build_dir}"],
            check=True,
        )
        # Build and install
        subprocess.run(
            ["arch-chroot", "/mnt", "su", "-", self._AUR_USER, "-c",
             f"cd {build_dir} && makepkg -sri --noconfirm"],
            check=True,
        )

    def _install_aur_with_helper(self, helper: str, pkgs: List[str]) -> None:
        """Use yay/paru inside chroot to install AUR packages."""
        if not pkgs:
            return
        subprocess.run(
            ["arch-chroot", "/mnt", "su", "-", self._AUR_USER, "-c",
             f"{helper} -S --noconfirm --needed {' '.join(pkgs)}"],
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
    #  idempotency
    # ------------------------------------------------------------------ #

    def is_needed(self) -> bool:
        if self._missing(self.pacman_pkgs):
            return True
        if self._missing(self.aur_pkgs):
            return True
        return False

    # ------------------------------------------------------------------ #
    #  execute
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
        return not self._missing(self.pacman_pkgs + self.aur_pkgs)

    # ------------------------------------------------------------------ #
    #  v3 interface (read-only; apply() lands in Plan 4 with AUR support) #
    # ------------------------------------------------------------------ #

    _PACMAN_DOMAIN = "packages"

    def actual(self) -> set[str]:
        """Set of explicitly-installed packages on the target.

        Runs ``pacman -Qqe`` via ``Command.execute`` against
        ``self.context.target``. Returns an empty set if the context or
        target is missing (legacy call-sites).
        """
        target = getattr(self.context, "target", None) if self.context else None
        if target is None:
            return set()
        result = Command.execute("pacman", ["-Qqe"], target=target)
        stdout = getattr(result, "stdout", b"") or b""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        return {line.strip() for line in stdout.splitlines() if line.strip()}

    def plan(self, managed):
        """Compute INSTALL/REMOVE for both pacman and AUR packages.

        Both kinds land in the pacman DB once installed, so ``pacman -Qqe``
        (which ``actual()`` parses) sees them together. The action carries
        the original split via ``self.pacman_pkgs`` / ``self.aur_pkgs`` so
        ``apply()`` can route INSTALLs to the right tool.
        """
        from ..state.set_math import compute_changes
        desired = list(self.pacman_pkgs) + list(self.aur_pkgs)
        changes, _drift = compute_changes(
            self._PACMAN_DOMAIN,
            desired=desired,
            managed=managed,
            actual=self.actual(),
        )
        return changes

    def managed_keys(self) -> dict:
        """The full set of packages this action owns after apply
        (pacman + AUR, both under the ``packages`` domain).
        """
        return {self._PACMAN_DOMAIN: list(self.pacman_pkgs) + list(self.aur_pkgs)}

    def import_state(self, managed: "list[str] | None" = None) -> dict:
        """Capture reality into the config fragment (sync).

        Keeps every declared token (intent, ``aur-`` prefix preserved — even if
        not currently installed) and appends everything present that is not
        declared. Independent of the manifest M: ``sync`` reflects reality.

        Note: ``pacman -Qqe`` cannot distinguish AUR packages, so captured
        (undeclared) packages are plain names; the ``aur-`` prefix is only
        preserved on entries that were already declared with it.
        """
        actual = self.actual()
        original: List[str] = list(self.config) if isinstance(self.config, list) else []

        def _strip(token: str) -> str:
            return token[len(AUR_PREFIX):] if token.startswith(AUR_PREFIX) else token

        declared_stripped = {_strip(t) for t in original}
        extra = sorted(actual - declared_stripped)   # present, not declared
        return {self._PACMAN_DOMAIN: original + extra}

    # ------------------------------------------------------------------ #
    #  v3 apply() — destructive (Plan 4)                                 #
    # ------------------------------------------------------------------ #

    def apply(self, changes) -> None:
        """Execute a list of ``Change`` objects against the target.

        Routing rules:
        - ``Op.INSTALL`` and item in ``self.pacman_pkgs`` → ``pacman -S``.
        - ``Op.INSTALL`` and item in ``self.aur_pkgs``    → ``_apply_aur_install``.
        - ``Op.REMOVE`` → ``pacman -Rns`` (handles both pacman + AUR pkgs).

        Installs (pacman, then AUR) run before removals — additive steps
        first keep the system in a working state if a destructive step
        fails midway.
        """
        from ..state.change import Op

        target = getattr(self.context, "target", None) if self.context else None
        if target is None:
            return

        if not changes:
            return

        pacman_installs: list[str] = []
        aur_installs: list[str] = []
        removes: list[str] = []
        aur_set = set(self.aur_pkgs)
        pacman_set = set(self.pacman_pkgs)

        for change in changes:
            if change.op is Op.INSTALL:
                if change.item in pacman_set:
                    pacman_installs.append(change.item)
                elif change.item in aur_set:
                    aur_installs.append(change.item)
                else:
                    raise ValueError(
                        f"apply() received INSTALL for unknown package "
                        f"{change.item!r}: not in pacman_pkgs or aur_pkgs"
                    )
            elif change.op is Op.REMOVE:
                removes.append(change.item)

        if pacman_installs:
            Command.execute(
                "pacman",
                ["--noconfirm", "--needed", "-S", *pacman_installs],
                target=target,
            )

        if aur_installs:
            self._apply_aur_install(aur_installs)

        if removes:
            Command.execute(
                "pacman",
                ["--noconfirm", "-Rns", *removes],
                target=target,
            )

    def _apply_aur_install(self, pkgs: list[str]) -> None:
        """Install AUR packages via the makepkg dance (target-aware).

        Steps:
          1. Ensure base-devel + git installed on the target.
          2. Ensure the temp build user exists (passwordless sudo via sudoers.d).
          3. For each pkg: clone + makepkg -sri as the build user.
          4. Remove the temp build user + sudoers fragment.
        """
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

        # 3. Build each
        for pkg in pkgs:
            build_dir = f"/home/{self._AUR_USER}/{pkg}"
            subprocess.run(
                self._target_argv(target, ["rm", "-rf", build_dir]),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                self._target_argv(target, [
                    "su", "-", self._AUR_USER, "-c",
                    f"git clone https://aur.archlinux.org/{pkg}.git {build_dir}",
                ]),
                check=True,
            )
            subprocess.run(
                self._target_argv(target, [
                    "su", "-", self._AUR_USER, "-c",
                    f"cd {build_dir} && makepkg -sri --noconfirm",
                ]),
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

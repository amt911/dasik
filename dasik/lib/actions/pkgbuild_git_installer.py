"""Install a PKGBUILD pinned to an exact Git commit (PLAN v3 §8).

The resolver decides *where* a package comes from; this installer builds it. For
each :class:`ResolvedGitPackage` it clones the declared HTTPS Git URL, checks out
the exact configured commit SHA (reproducible — refuses any other commit), builds
the PKGBUILD as an **unprivileged** build user (never as root), verifies the
built package's identity (``pkgname``) matches the declared name *before*
installing, and installs the artifacts with pacman. A ``finally`` block always
removes the temp build dir, the sudoers fragment, and the build user if this run
created it.

Every shell-out goes through :class:`Command` (so the whole build lands in the
run log and streams live under ``-v``); the module uses no raw subprocess.

Security: the URL / SHA / build-dir / package name are passed to the build user's
shell as positional parameters (``$1``, ``$2``, …), never interpolated into a
shell string — a value can only ever be inert data, never code. First version
accepts only ``https://github.com/….git`` URLs (enforced by the config model).
"""
from __future__ import annotations

import os
from pathlib import Path
from posixpath import normpath
from typing import Iterable, List, Set

from . import srcinfo
from .package_resolver import ResolvedGitPackage
from ..command_worker.command_worker import Command
from ..exceptions.exceptions import CommandExecutionError


def _su_argv(user: str, script: str, *args: str) -> List[str]:
    """Argv for ``su - <user> -c <script> -- sh`` with values passed as ``$1``..
    positional parameters, NEVER interpolated into *script*. ``$0`` is a
    conventional placeholder. The ``--`` option terminator ensures a later
    dash-prefixed value belongs to the child shell rather than util-linux ``su``.
    Mirrors PackagesAction._su_argv."""
    return ["su", "-", user, "-c", script, "--", "sh", *args]


class PkgbuildGitInstaller:
    """Builds+installs ``package_sources`` (pkgbuild-git) packages on a Target."""

    BUILD_USER = "_aurbuilder"
    BUILD_ROOT = "/home/_aurbuilder/dasik-git"

    def __init__(self, target, build_deps=None):
        """*build_deps*, when given, is called with the package's declared build
        dependencies before makepkg runs.

        `makepkg -s` syncs dependencies with pacman, which only knows the
        configured repositories, so a makedepends living in the AUR aborts the
        build with "target not found" and no ordering of the package list can
        help: it has to be installed first. Deciding WHICH of the declared
        dependencies the repositories lack belongs to the caller, which owns the
        resolver; this class only reports what the PKGBUILD asks for.
        """
        self._target = target
        self._build_deps = build_deps

    # -- run --------------------------------------------------------------

    def _run(self, cmd: List[str], check: bool = True, stream: bool = False):
        """Run *cmd* on the target via :class:`Command` (arch-chroot handled by the
        target). ``stream=True`` for the long steps (clone/checkout/makepkg)."""
        return Command.execute(cmd[0], cmd[1:], target=self._target,
                               check=check, stream=stream)

    @staticmethod
    def _text(data: "bytes | str | None") -> str:
        if data is None:
            return ""
        if isinstance(data, bytes):
            return data.decode("utf-8", errors="replace")
        return data

    # -- orchestration ----------------------------------------------------

    def install(self, git_pkgs: Iterable[ResolvedGitPackage]) -> None:
        pkgs = list(git_pkgs)
        if not pkgs:
            return
        created_user = self._ensure_prerequisites()
        sudoers_path = self._target.path(f"/etc/sudoers.d/{self.BUILD_USER}")
        try:
            for pkg in pkgs:
                self._build_one(pkg)
        finally:
            self._cleanup(created_user, sudoers_path)

    def _ensure_prerequisites(self) -> bool:
        """Install base-devel + git, ensure the build user (+ passwordless sudo
        so makepkg can sync deps). Returns True if THIS run created the user."""
        Command.execute(
            "pacman", ["--noconfirm", "--needed", "-S", "base-devel", "git"],
            target=self._target,
        )
        id_check = self._run(["id", self.BUILD_USER], check=False)
        created = getattr(id_check, "returncode", 0) != 0
        if created:
            Command.execute(
                "useradd", ["-m", "-r", "-s", "/bin/bash", self.BUILD_USER],
                target=self._target,
            )
        self._write_sudoers()
        return created

    def _write_sudoers(self) -> None:
        """Grant the build user passwordless sudo, which `makepkg -s`/`-i` need.

        Written more than once on purpose: the AUR installer shares this user AND
        this fragment, and removes the fragment when it finishes — which, when a
        build dependency sends us through it, lands in the middle of this build.
        """
        sudoers_path = self._target.path(f"/etc/sudoers.d/{self.BUILD_USER}")
        with open(sudoers_path, "w", encoding="utf-8") as f:
            f.write(f"{self.BUILD_USER} ALL=(ALL) NOPASSWD: ALL\n")

    def _build_one(self, pkg: ResolvedGitPackage) -> None:
        name = pkg.name
        url = pkg.source["url"]
        ref = pkg.source["ref"]
        subdir = pkg.source.get("subdir", ".") or "."

        build_dir = f"{self.BUILD_ROOT}/{name}"
        pkg_dir = normpath(f"{build_dir}/{subdir}")
        if pkg_dir != build_dir and not pkg_dir.startswith(build_dir + "/"):
            raise CommandExecutionError(
                f"package source subdir {subdir!r} escapes the clone root; refusing"
            )

        # Clean any previous build, clone, and checkout the EXACT commit. url/dir/
        # ref are $1/$2 positional args — never spliced into the shell string.
        self._run(["rm", "-rf", build_dir], check=False)
        self._run(_su_argv(self.BUILD_USER, 'git clone "$1" "$2"', url, build_dir),
                  stream=True)
        self._run(_su_argv(
            self.BUILD_USER, 'cd "$1" && git checkout --detach "$2"', build_dir, ref),
            stream=True)

        head = self._text(self._run(_su_argv(
            self.BUILD_USER, 'cd "$1" && git rev-parse HEAD', build_dir)).stdout).strip()
        if head != ref:
            raise CommandExecutionError(
                f"PKGBUILD source for {name}: checked-out commit {head!r} does not "
                f"match the configured ref {ref!r}; refusing install"
            )

        # Identity gate: the package this PKGBUILD produces must include the
        # declared name, checked BEFORE building/installing anything.
        info = self._read_srcinfo(pkg_dir)
        names = self._parse_pkgnames(info)
        if name not in names:
            produced = ", ".join(sorted(names)) or "nothing"
            raise CommandExecutionError(
                f"PKGBUILD source for {name} produces {produced}; refusing install"
            )

        # Whatever the PKGBUILD needs to build that it does not itself produce.
        # A split package naming its own sibling is not a missing dependency.
        if self._build_deps is not None:
            declared = {srcinfo.strip_version_constraint(d)
                        for d in srcinfo.parse_depends(info)} - names
            if declared:
                self._build_deps(sorted(declared))
                # The hook may have gone through the AUR installer, whose cleanup
                # takes the shared sudoers fragment with it. Without this, the
                # `sudo pacman -U` behind `makepkg -i` prompts for a password
                # nobody can type and the build dies with exit 14.
                self._write_sudoers()

        # Build + install as the unprivileged user (makepkg -s syncs deps via the
        # build user's passwordless sudo; -i installs; never runs as root).
        self._run(_su_argv(
            self.BUILD_USER, 'cd "$1" && makepkg -sri --noconfirm', pkg_dir),
            stream=True)

        verify = Command.execute("pacman", ["-Q", name], target=self._target)
        if getattr(verify, "returncode", 0) != 0:
            raise CommandExecutionError(
                f"PKGBUILD source for {name}: package not present after install"
            )

    def _read_srcinfo(self, pkg_dir: str) -> str:
        """The package's .SRCINFO text — pkgnames AND dependencies come from it.

        Prefers a committed ``.SRCINFO`` when present; otherwise regenerates it
        with ``makepkg --printsrcinfo`` as the build user (§8.7)."""
        host_srcinfo = self._target.path(f"{pkg_dir}/.SRCINFO")
        if os.path.exists(host_srcinfo):
            return Path(host_srcinfo).read_text()
        return self._text(self._run(_su_argv(
            self.BUILD_USER, 'cd "$1" && makepkg --printsrcinfo', pkg_dir)).stdout)

    @staticmethod
    def _parse_pkgnames(text: str) -> Set[str]:
        """Extract ``pkgname`` values from .SRCINFO text (delegates to srcinfo)."""
        return srcinfo.parse_pkgnames(text)

    def _cleanup(self, created_user: bool, sudoers_path: str) -> None:
        """Remove the sudoers fragment always; remove the build user only if THIS
        run created it (a pre-existing/AUR-shared user is left intact)."""
        if created_user:
            self._run(["userdel", "-r", self.BUILD_USER], check=False)
        if os.path.exists(sudoers_path):
            os.remove(sudoers_path)

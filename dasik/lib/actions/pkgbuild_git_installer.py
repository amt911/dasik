"""Install a PKGBUILD pinned to an exact Git commit (PLAN v3 §8).

The resolver decides *where* a package comes from; this installer builds it. For
each :class:`ResolvedGitPackage` it clones the declared HTTPS Git URL, checks out
the exact configured commit SHA (reproducible — refuses any other commit), builds
the PKGBUILD as an **unprivileged** build user (never as root), verifies the
built package's identity (``pkgname``) matches the declared name *before*
installing, and installs the artifacts with pacman. A ``finally`` block always
removes the temp build dir, the sudoers fragment, and the build user if this run
created it.

Security: the URL / SHA / build-dir / package name are passed to the build user's
shell as positional parameters (``$1``, ``$2``, …), never interpolated into a
shell string — a value can only ever be inert data, never code. First version
accepts only ``https://github.com/….git`` URLs (enforced by the config model).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from posixpath import normpath
from typing import Iterable, List, Set

from .package_resolver import ResolvedGitPackage
from ..command_worker.command_worker import Command
from ..exceptions.exceptions import CommandExecutionError


def _su_argv(user: str, script: str, *args: str) -> List[str]:
    """Argv for ``su - <user> -c <script>`` with values passed as ``$1``..
    positional parameters, NEVER interpolated into *script*. ``$0`` is a
    conventional placeholder. Mirrors PackagesAction._su_argv."""
    return ["su", "-", user, "-c", script, "sh", *args]


class PkgbuildGitInstaller:
    """Builds+installs ``package_sources`` (pkgbuild-git) packages on a Target."""

    BUILD_USER = "_aurbuilder"
    BUILD_ROOT = "/home/_aurbuilder/dasik-git"

    def __init__(self, target):
        self._target = target

    # -- argv / chroot ----------------------------------------------------

    def _argv(self, cmd: List[str]) -> List[str]:
        """Prefix ``arch-chroot <root>`` when the target is a chroot."""
        if self._target.is_chroot:
            return ["arch-chroot", self._target.root, *cmd]
        return list(cmd)

    def _run(self, cmd: List[str], check: bool = True) -> "subprocess.CompletedProcess":
        result = subprocess.run(self._argv(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if check and getattr(result, "returncode", 0) != 0:
            stderr = getattr(result, "stderr", b"") or b""
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            raise CommandExecutionError(
                f"command failed (exit {result.returncode}): {' '.join(cmd)}\n{stderr.strip()[-2000:]}"
            )
        return result

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
        id_check = subprocess.run(
            self._argv(["id", self.BUILD_USER]),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        created = id_check.returncode != 0
        if created:
            Command.execute(
                "useradd", ["-m", "-r", "-s", "/bin/bash", self.BUILD_USER],
                target=self._target,
            )
        sudoers_path = self._target.path(f"/etc/sudoers.d/{self.BUILD_USER}")
        with open(sudoers_path, "w") as f:
            f.write(f"{self.BUILD_USER} ALL=(ALL) NOPASSWD: ALL\n")
        return created

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
        self._run(_su_argv(self.BUILD_USER, 'git clone "$1" "$2"', url, build_dir))
        self._run(_su_argv(
            self.BUILD_USER, 'cd "$1" && git checkout --detach "$2"', build_dir, ref))

        head = self._text(self._run(_su_argv(
            self.BUILD_USER, 'cd "$1" && git rev-parse HEAD', build_dir)).stdout).strip()
        if head != ref:
            raise CommandExecutionError(
                f"PKGBUILD source for {name}: checked-out commit {head!r} does not "
                f"match the configured ref {ref!r}; refusing install"
            )

        # Identity gate: the package this PKGBUILD produces must include the
        # declared name, checked BEFORE building/installing anything.
        names = self._read_pkgnames(pkg_dir)
        if name not in names:
            produced = ", ".join(sorted(names)) or "nothing"
            raise CommandExecutionError(
                f"PKGBUILD source for {name} produces {produced}; refusing install"
            )

        # Build + install as the unprivileged user (makepkg -s syncs deps via the
        # build user's passwordless sudo; -i installs; never runs as root).
        self._run(_su_argv(
            self.BUILD_USER, 'cd "$1" && makepkg -sri --noconfirm', pkg_dir))

        verify = Command.execute("pacman", ["-Q", name], target=self._target)
        if getattr(verify, "returncode", 0) != 0:
            raise CommandExecutionError(
                f"PKGBUILD source for {name}: package not present after install"
            )

    def _read_pkgnames(self, pkg_dir: str) -> Set[str]:
        """The ``pkgname`` set the PKGBUILD produces.

        Prefers a committed ``.SRCINFO`` when present; otherwise regenerates it
        with ``makepkg --printsrcinfo`` as the build user (§8.7)."""
        host_srcinfo = self._target.path(f"{pkg_dir}/.SRCINFO")
        if os.path.exists(host_srcinfo):
            return self._parse_pkgnames(Path(host_srcinfo).read_text())
        out = self._text(self._run(_su_argv(
            self.BUILD_USER, 'cd "$1" && makepkg --printsrcinfo', pkg_dir)).stdout)
        return self._parse_pkgnames(out)

    @staticmethod
    def _parse_pkgnames(srcinfo: str) -> Set[str]:
        """Extract ``pkgname`` values from .SRCINFO / printsrcinfo text.

        Only exact ``pkgname = X`` keys count — ``pkgbase`` and ``depends`` are
        ignored — so a split-package PKGBUILD yields all its subpackage names."""
        names: Set[str] = set()
        for line in srcinfo.splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == "pkgname":
                v = value.strip()
                if v:
                    names.add(v)
        return names

    def _cleanup(self, created_user: bool, sudoers_path: str) -> None:
        """Remove the sudoers fragment always; remove the build user only if THIS
        run created it (a pre-existing/AUR-shared user is left intact)."""
        if created_user:
            subprocess.run(
                self._argv(["userdel", "-r", self.BUILD_USER]),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        if os.path.exists(sudoers_path):
            os.remove(sudoers_path)

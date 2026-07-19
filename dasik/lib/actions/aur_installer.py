"""AUR installer — hybrid: a declared helper (yay/paru) or dasik's own resolution.

Installs ``resolution.aur``. Two paths:

* **Helper** — if the list declares ``yay`` or ``paru``, build that helper from
  source with makepkg (its own deps are all in the official repos, so ``makepkg
  -s`` covers them), then let the helper install the rest: it resolves transitive
  AUR dependencies, ``provides`` and split packages on its own.
* **Own resolution** — no helper: clone and classify *every* package's
  dependencies first (already-satisfied / official-repo / virtual-provides /
  another AUR package / unknown), topologically order the AUR build graph, then
  ``makepkg`` each node in order. A dependency discovered in the AUR (not declared
  by the user) is marked ``--asdeps`` after install so it does not pollute
  ``pacman -Qqe`` or a later ``sync``.

Every shell-out goes through :class:`Command` (``check=True`` on mutations,
``stream=True`` on clone/makepkg/helper) so the full build output lands in the
run log and streams live under ``-v``. The clones done *during resolution* run
before the first ``makepkg``; the only prior mutation is installing the build
prerequisites, which ``_cleanup`` always reverts (sudoers removed; the build user
removed only if this run created it).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Sequence, Set, Tuple

from . import srcinfo
from .packages_action import PackagesAction, _validate_pkg_name
from .package_resolver import AurUnavailableError, PackageResolver
from ..command_worker.command_worker import Command
from ..exceptions.exceptions import CommandExecutionError


class AurInstaller:
    """Install ``resolution.aur`` on a Target: helper path or own resolution."""

    BUILD_USER = "_aurbuilder"
    BUILD_ROOT = "/home/_aurbuilder/dasik-aur"
    HELPERS = ("yay", "paru")

    def __init__(self, target, resolver: "PackageResolver | None" = None) -> None:
        self._target = target
        self._resolver = resolver or PackageResolver()

    # -- argv / run -------------------------------------------------------

    def _run(self, cmd: str, args: List[str], check: bool = True,
             stream: bool = False):
        return Command.execute(cmd, args, target=self._target, check=check,
                               stream=stream)

    def _run_as_builder(self, script: str, *args: str, check: bool = True,
                        stream: bool = False):
        """Run *script* as the build user with values passed as ``$1``.. positional
        parameters (never interpolated into the shell string)."""
        argv = PackagesAction._su_argv(self.BUILD_USER, script, *args)
        return self._run(argv[0], argv[1:], check=check, stream=stream)

    @staticmethod
    def _text(data: "bytes | str | None") -> str:
        if data is None:
            return ""
        if isinstance(data, bytes):
            return data.decode("utf-8", errors="replace")
        return data

    def _clone_dir(self, pkg: str) -> str:
        return f"{self.BUILD_ROOT}/{pkg}"

    # -- entry point ------------------------------------------------------

    def install(self, pkgs: List[str], *, helper: "str | None" = None) -> None:
        """Install *pkgs*. *helper* is a declared yay/paru chosen by the caller
        from the full desired set — it may be pending in *pkgs* (build it) or
        already installed by an earlier partial apply (reuse it). ``None`` falls
        back to a helper found inside *pkgs*, else own resolution."""
        pkgs = list(dict.fromkeys(pkgs))          # dedup, keep declared order
        if not pkgs:
            return
        for p in pkgs:
            _validate_pkg_name(p)

        if helper is not None and helper not in self.HELPERS:
            raise CommandExecutionError(f"Unsupported AUR helper {helper!r}")
        selected = helper or next((h for h in self.HELPERS if h in pkgs), None)
        created = self._ensure_prerequisites()
        sudoers_path = self._target.path(f"/etc/sudoers.d/{self.BUILD_USER}")
        try:
            if selected is not None:
                self._install_with_helper(selected, pkgs)
            else:
                self._install_via_resolution(pkgs)
            self._verify_installed(pkgs)
        finally:
            self._cleanup(created, sudoers_path)

    # -- prerequisites / cleanup -----------------------------------------

    def _ensure_prerequisites(self) -> bool:
        """Install base-devel + git and ensure the build user (+ passwordless
        sudo so makepkg can sync repo deps). Returns True if THIS run created the
        user (so cleanup only removes a user we made)."""
        self._run("pacman", ["--noconfirm", "--needed", "-S", "base-devel", "git"],
                  check=True)
        id_check = self._run("id", [self.BUILD_USER], check=False)
        created = getattr(id_check, "returncode", 0) != 0
        if created:
            self._run("useradd", ["-m", "-r", "-s", "/bin/bash", self.BUILD_USER],
                      check=True)
        sudoers_path = self._target.path(f"/etc/sudoers.d/{self.BUILD_USER}")
        with open(sudoers_path, "w", encoding="utf-8") as f:
            f.write(f"{self.BUILD_USER} ALL=(ALL) NOPASSWD: ALL\n")
        return created

    def _cleanup(self, created_user: bool, sudoers_path: str) -> None:
        """Remove the sudoers fragment always; remove the build user only if THIS
        run created it; drop the build root best-effort."""
        if os.path.exists(sudoers_path):
            os.remove(sudoers_path)
        self._run("rm", ["-rf", self.BUILD_ROOT], check=False)
        if created_user:
            self._run("userdel", ["-r", self.BUILD_USER], check=False)

    # -- helper path ------------------------------------------------------

    def _install_with_helper(self, helper: str, pkgs: List[str]) -> None:
        if helper in pkgs:
            # Build the helper itself from source; its deps are all official-repo,
            # so makepkg -s covers them (no bootstrap chicken-and-egg).
            self._clone(helper)
            self._build_one(helper)
        else:
            # Not part of this delta: an earlier apply must have installed it.
            # Verify rather than silently switching strategy mid-install.
            installed = self._run("pacman", ["-Q", helper], check=False)
            if getattr(installed, "returncode", 0) != 0:
                raise CommandExecutionError(
                    f"declared AUR helper {helper!r} is not installed and is not "
                    f"part of the current install delta; re-run plan/apply"
                )
        rest = [p for p in pkgs if p != helper]
        if not rest:
            return
        # The helper resolves transitive AUR deps / provides / split packages for
        # the rest. `exec "$@"` runs helper + args straight from argv (the package
        # names are positional parameters, never spliced into a shell string); the
        # build user's NOPASSWD sudoers lets the helper elevate to install.
        self._run_as_builder(
            'exec "$@"', helper, "-S", "--noconfirm", "--needed", *rest,
            check=True, stream=True,
        )

    # -- own resolution path ---------------------------------------------

    def _install_via_resolution(self, pkgs: List[str]) -> None:
        order, discovered = self._resolve_build_order(pkgs)
        for node in order:
            # A previous build (via provides) may already satisfy this node; skip
            # rather than rebuild. pacman -T rc 0 == satisfied.
            check = self._run("pacman", ["-T", node], check=False)
            if getattr(check, "returncode", 0) == 0:
                continue
            self._build_one(node)
            if node in discovered:
                # makepkg -i installs explicit (pacman -U); correct a discovered
                # dependency's install reason so it doesn't leak into pacman -Qqe.
                self._run("pacman", ["-D", "--asdeps", node], check=True)

    def _resolve_build_order(self, pkgs: List[str]) -> Tuple[List[str], Set[str]]:
        """Clone + classify every package (and transitively its AUR deps) BEFORE
        any build. Returns ``(build_order, discovered_deps)`` where build_order is
        a topological order (deps before dependents) and discovered_deps are the
        AUR nodes the user did not declare."""
        repo = self._resolver.repo_names(self._target)
        declared = set(pkgs)
        graph: Dict[str, List[str]] = {}
        discovered: Set[str] = set()
        seen: Set[str] = set()
        pending: List[str] = list(pkgs)

        while pending:
            node = pending.pop()
            if node in seen:
                continue
            seen.add(node)
            self._clone(node)
            raw_deps = self._read_deps(self._clone_dir(node))

            aur_deps: List[str] = []
            candidates: List[str] = []
            for dep in sorted(raw_deps):
                kind, bare = self._classify_dep(dep, repo)
                if kind == "candidate":
                    candidates.append(bare)
                # "satisfied" / "repo" / "virtual" -> left to makepkg -s / already
                # installed; nothing to build.

            if candidates:
                self._resolve_candidates(
                    node, candidates, declared, aur_deps, discovered, seen, pending)
            graph[node] = aur_deps

        return self._topo_sort(graph), discovered

    def _resolve_candidates(self, node: str, candidates: List[str],
                            declared: Set[str], aur_deps: List[str],
                            discovered: Set[str], seen: Set[str],
                            pending: List[str]) -> None:
        """Batch-resolve the not-yet-classified deps of *node* against the AUR in a
        single RPC. Found -> an AUR build dependency (recurse); missing -> abort."""
        wanted = list(dict.fromkeys(candidates))
        try:
            found = self._resolver.aur_info(wanted)
        except AurUnavailableError:
            self._abort_unavailable(wanted)
        for bare in wanted:
            if bare not in found:
                raise CommandExecutionError(
                    f"AUR dependency {bare!r} required by {node!r} not found in "
                    f"repos, AUR or installed system"
                )
            aur_deps.append(bare)
            if bare not in declared:
                discovered.add(bare)
            if bare not in seen and bare not in pending:
                pending.append(bare)

    def _classify_dep(self, dep: str, repo: Set[str]) -> Tuple[str, str]:
        """Classify one dependency spec. Returns ``(kind, bare_name)`` where kind
        is ``satisfied`` / ``repo`` / ``virtual`` / ``candidate``.

        The bare name is validated FIRST, so a name with shell metacharacters is
        rejected before it can reach any argv, URL or clone."""
        bare = srcinfo.strip_version_constraint(dep)
        _validate_pkg_name(bare)

        # (a) already satisfied by an installed package/provides (honours the
        # version constraint) — pacman -T rc 0 = satisfied, 127 = missing.
        probe = self._run("pacman", ["-T", dep], check=False)
        rc = getattr(probe, "returncode", 0)
        if rc == 0:
            return ("satisfied", bare)
        if rc not in (0, 127):
            raise CommandExecutionError(
                f"pacman -T {dep!r} failed unexpectedly (rc={rc})"
            )
        # (b) an official-repo package -> makepkg -s installs it.
        if bare in repo:
            return ("repo", bare)
        # (c) a virtual/provides resolvable in the sync DBs (e.g. `sh`) -> makepkg.
        sp = self._run("pacman", ["-Sp", "--print-format", "%n", bare], check=False)
        if getattr(sp, "returncode", 0) == 0:
            return ("virtual", bare)
        # (d) neither -> candidate for the batched AUR lookup.
        return ("candidate", bare)

    def _topo_sort(self, graph: Dict[str, List[str]]) -> List[str]:
        """Tricolor DFS post-order: dependencies before dependents. A re-entry into
        a grey node is a cycle -> abort naming it."""
        WHITE, GREY, BLACK = 0, 1, 2
        color: Dict[str, int] = {n: WHITE for n in graph}
        order: List[str] = []

        def visit(n: str, stack: List[str]) -> None:
            color[n] = GREY
            for dep in graph.get(n, []):
                if color.get(dep, WHITE) == GREY:
                    cycle = " -> ".join(stack + [n, dep])
                    raise CommandExecutionError(
                        f"AUR dependency cycle detected: {cycle}"
                    )
                if color.get(dep, WHITE) == WHITE:
                    visit(dep, stack + [n])
            color[n] = BLACK
            order.append(n)

        for n in list(graph):
            if color[n] == WHITE:
                visit(n, [])
        return order

    # -- primitives -------------------------------------------------------

    def _clone(self, pkg: str) -> str:
        _validate_pkg_name(pkg)
        build_dir = self._clone_dir(pkg)
        url = f"https://aur.archlinux.org/{pkg}.git"
        self._run("rm", ["-rf", build_dir], check=False)
        # url/dir are $1/$2 positional args — never spliced into the shell string.
        self._run_as_builder('git clone "$1" "$2"', url, build_dir,
                             check=True, stream=True)
        return build_dir

    def _read_deps(self, pkg_dir: str) -> Set[str]:
        """The build/make/check deps of a cloned package, from a committed
        ``.SRCINFO`` when present else ``makepkg --printsrcinfo``."""
        host_srcinfo = self._target.path(f"{pkg_dir}/.SRCINFO")
        if os.path.exists(host_srcinfo):
            return srcinfo.parse_depends(Path(host_srcinfo).read_text())
        out = self._text(self._run_as_builder(
            'cd "$1" && makepkg --printsrcinfo', pkg_dir).stdout)
        return srcinfo.parse_depends(out)

    def _build_one(self, pkg: str) -> None:
        build_dir = self._clone_dir(pkg)
        self._run_as_builder('cd "$1" && makepkg -sri --noconfirm', build_dir,
                             check=True, stream=True)

    def _verify_installed(self, pkgs: Sequence[str]) -> None:
        for pkg in pkgs:
            q = self._run("pacman", ["-Q", pkg], check=False)
            if getattr(q, "returncode", 0) != 0:
                raise CommandExecutionError(
                    f"AUR package {pkg!r} not present after install"
                )

    @staticmethod
    def _abort_unavailable(names: Sequence[str]) -> None:
        raise CommandExecutionError(
            "Refusing to install AUR dependencies — AUR unavailable (existence "
            "could not be checked, retry): " + ", ".join(sorted(names))
        )

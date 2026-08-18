"""Walk the transitive dependency closure of the declared AUR packages and
name every chain that ends in a dependency nothing can satisfy — BEFORE the
first mutation, instead of 25 minutes into an install.

The 2026-08-18 incident this guards against: ``lib32-gst-libav`` (declared) →
``lib32-ffmpeg`` (AUR dep) → ``lib32-libdav1d`` (a name that exists nowhere:
an upstream PKGBUILD typo for ``lib32-dav1d``). yay refused the whole
27-package transaction and the apply died with the disks already written.

Per dependency, satisfiability is decided in this order (first hit wins):

1. already satisfied on the target — one batched ``pacman -T`` per level,
   full version specs, honouring pacman's 0/127 protocol (any other exit
   answers nothing and the dep falls through — never "satisfied");
2. a repo package by name (one ``pacman -Slq`` for the whole walk);
3. a repo provider (``pacman -Sp``, via the resolver's memoized probe);
4. an AUR package by exact name — which becomes a node of the closure and is
   walked in turn;
5. an AUR provider (``rpc/v5/search?by=provides``) — satisfiable, but NOT
   recursed into: which provider gets picked belongs to the helper.

Dependency data comes from the AUR RPC's last published .SRCINFO
(``PackageResolver.aur_depends``); the install-time topo path keeps its
authoritative clone-and-read. An unreachable RPC raises
``AurUnavailableError`` — the caller must retry, never install.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from ..actions.package_resolver import PackageResolver
from ..command_worker.command_worker import Command
from ..exceptions.exceptions import CommandExecutionError

# A dependency spec is NAME[<>=VERSION]; sonames ride the same grammar
# (libfoo.so=7-64). Split on the first comparison operator.
_VERSION_SPLIT = re.compile(r"[<>=]")

# A runaway closure (or a pathological AUR graph) stops loudly instead of
# hammering the RPC. Real configs stay two orders of magnitude below this.
_MAX_CLOSURE_NODES = 500

_SATISFIED_RCS = (0, 127)  # pacman -T's protocol: 0 = all satisfied, 127 = some missing


def strip_version_constraint(spec: str) -> str:
    """``ffmpeg>=2:8.1.2`` → ``ffmpeg``; ``libfoo.so=7-64`` → ``libfoo.so``."""
    return _VERSION_SPLIT.split(spec.strip(), maxsplit=1)[0]


@dataclass(frozen=True)
class BrokenDep:
    """One unsatisfiable dependency, with the declared-package chain to it.

    ``chain`` runs from the declared root to the bare unsatisfiable name;
    ``spec`` is the dependency as the PKGBUILD wrote it (version and all);
    ``detail`` says why nothing satisfies it."""

    chain: Tuple[str, ...]
    spec: str
    detail: str

    def render(self) -> str:
        return f"{' → '.join(self.chain)}: {self.detail}"


_NOWHERE = ("not in the configured repos, not in the AUR, and no repo or AUR "
            "package provides it")


def validate_aur_closure(
    roots: Sequence[str],
    resolver: PackageResolver,
    target,
    *,
    max_nodes: int = _MAX_CLOSURE_NODES,
) -> List[BrokenDep]:
    """Validate the dependency closure of *roots* (declared AUR names).

    Returns every broken chain found (never fail-fast: the user fixes them in
    one round). An empty list means the whole closure is satisfiable today.
    Raises ``AurUnavailableError`` when the RPC cannot answer and
    ``CommandExecutionError`` when the closure exceeds *max_nodes*."""
    frontier = list(dict.fromkeys(roots))
    if not frontier:
        return []

    repo = resolver.repo_names(target)
    broken: List[BrokenDep] = []
    parent: Dict[str, str] = {}
    expanded = set(frontier)
    # bare name -> True (satisfiable outside the walk) | False (already
    # reported broken). AUR nodes never enter it; they are walked instead.
    verdict: Dict[str, bool] = {}

    known = resolver.aur_depends(frontier)
    for name in frontier:
        if name not in known:
            broken.append(BrokenDep(chain=(name,), spec=name,
                                    detail="no longer in the AUR"))
    frontier = [n for n in frontier if n in known]

    while frontier:
        deps_map = resolver.aur_depends(frontier)
        level: List[Tuple[str, str, str]] = []   # (node, spec, bare)
        for node in frontier:
            for spec in deps_map.get(node, []):
                level.append((node, spec, strip_version_constraint(spec)))

        satisfied = _target_satisfied([spec for _, spec, _ in level], target)
        pending: List[Tuple[str, str, str]] = []
        for node, spec, bare in level:
            if spec in satisfied:
                continue
            if bare in repo:
                continue
            if bare in expanded:                 # diamond/cycle: already a node
                continue
            if bare in verdict:
                continue                         # decided (or reported) once
            pending.append((node, spec, bare))

        aur_found = resolver.aur_depends(
            list(dict.fromkeys(bare for _, _, bare in pending)))
        next_frontier: List[str] = []
        for node, spec, bare in pending:
            if bare in expanded or verdict.get(bare) is not None:
                continue                         # a sibling got here first
            if bare in aur_found:
                parent[bare] = node
                expanded.add(bare)
                next_frontier.append(bare)
                if len(expanded) > max_nodes:
                    raise CommandExecutionError(
                        f"AUR dependency closure exceeded {max_nodes} packages "
                        f"while expanding {bare!r} — refusing to walk further. "
                        f"Raise the cap only if this graph is genuinely that big."
                    )
                continue
            if resolver.repo_provides(bare, target):
                verdict[bare] = True
                continue
            if resolver.aur_providers(bare):
                verdict[bare] = True             # a helper can pick a provider
                continue
            verdict[bare] = False
            broken.append(BrokenDep(chain=_chain(parent, node) + (bare,),
                                    spec=spec, detail=_NOWHERE))
        frontier = next_frontier

    return broken


def _target_satisfied(specs: List[str], target) -> set:
    """The subset of *specs* the target already satisfies (``pacman -T``).

    pacman prints the deps that are NOT satisfied and exits 0 (none missing)
    or 127 (some missing). Any other exit code answers nothing: return the
    empty set so every spec falls through to the stricter checks."""
    ordered = list(dict.fromkeys(specs))
    if not ordered:
        return set()
    result = Command.execute("pacman", ["-T", *ordered], target=target)
    if getattr(result, "returncode", -1) not in _SATISFIED_RCS:
        return set()
    stdout = getattr(result, "stdout", b"") or b""
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", errors="replace")
    missing = {line.strip() for line in stdout.splitlines() if line.strip()}
    return {s for s in ordered if s not in missing}


def _chain(parent: Dict[str, str], node: str) -> Tuple[str, ...]:
    """The declared-root → *node* path recorded by the BFS parent map."""
    path = [node]
    while path[-1] in parent:
        path.append(parent[path[-1]])
    return tuple(reversed(path))

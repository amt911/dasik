"""A pacman double that refuses to answer questions it does not model.

Every fake in this suite used to end in a catch-all::

    return MagicMock(stdout=b"", returncode=0)

which answers *anything*, including questions the production code had not
learned to ask yet. That is not a neutral default: for `pacman -T`, whose
output is the list of dependencies NOT satisfied, an empty answer means
"everything is already installed". When dasik grew that probe, eight doubles
said exactly that, and the suite stayed green while `plan` quietly proposed
nothing at all.

So this double models the read-only pacman surface dasik actually uses and
raises on the rest. The day someone adds `pacman -Qkk`, the tests that would
have silently agreed with it fail instead, naming the flag.

Mutating operations (`-S`, `-D`, `-R`, `-Sy`) succeed and are recorded rather
than refused: tests assert on what was installed or removed, and refusing them
would only mean re-teaching the double what pacman already promises. Non-pacman
commands are outside this double's remit and get a bland success unless the
caller passes ``other``.
"""
from __future__ import annotations

from typing import Callable, Iterable, Mapping, Optional, Sequence
from unittest.mock import MagicMock


class UnmodelledPacmanQuery(AssertionError):
    """A test asked the double a pacman question it does not model."""


# Read-only queries this double knows how to answer.
_MODELLED = ("-Qq", "-Qqe", "-Qqm", "-Qqo", "-Qo", "-Qi", "-Q",
             "-Slq", "-Sgq", "-Sg", "-T", "-Sp")
# Operations that CHANGE the machine. A test drives them and asserts on the
# call; the double just has to let them through.
_MUTATING = ("-S", "-Sy", "-Syu", "-U", "-R", "-Rns", "-Rs", "-D")


def _result(text: str = "", returncode: int = 0) -> MagicMock:
    return MagicMock(stdout=text.encode(), stderr=b"", returncode=returncode)


def _lines(names: Iterable[str]) -> str:
    joined = "\n".join(names)
    return joined + "\n" if joined else ""


def pacman_double(
    *,
    installed: Sequence[str] = (),
    explicit: Optional[Sequence[str]] = None,
    foreign: Sequence[str] = (),
    repo: Sequence[str] = (),
    groups: Optional[Mapping[str, Sequence[str]]] = None,
    satisfied: Sequence[str] = (),
    provided: Sequence[str] = (),
    owners: Optional[Mapping[str, str]] = None,
    required_by: Optional[Mapping[str, Sequence[str]]] = None,
    other: Optional[Callable[[str, list], Optional[str]]] = None,
):
    """A ``Command.execute``/``subprocess`` replacement for pacman.

    Every argument states a fact about the machine being simulated:

    ``installed``   what ``pacman -Qq`` lists (any install reason)
    ``explicit``    what ``pacman -Qqe`` lists. Omitted means "same as
                    *installed*"; an empty list means EMPTY — a machine with
                    nothing installed explicitly is a real state, and folding it
                    back into *installed* is the double inventing an answer
    ``foreign``     what ``pacman -Qqm`` lists (AUR/local packages)
    ``repo``        what ``pacman -Slq`` lists (every name in the sync DBs)
    ``groups``      ``{group: [member, …]}`` for ``-Sgq`` and ``-Sg``
    ``satisfied``   names ``pacman -T`` considers satisfied — INCLUDING via a
                    provider. Everything else comes back as missing, which is
                    the safe default: a plan still plans it.
    ``provided``    names ``pacman -Sp`` can resolve (a provider exists)
    ``owners``      ``{path: package}`` for ``-Qo``/``-Qqo``
    ``required_by`` ``{package: [dependent, …]}`` rendered into ``-Qi``
    ``other``       called for non-pacman commands; return a string for stdout,
                    or ``None`` to accept the bland default.

    The returned callable records every invocation in ``.calls`` and offers
    ``.calls_for(cmd)``.
    """
    explicit = tuple(installed) if explicit is None else tuple(explicit)
    groups = dict(groups or {})
    owners = dict(owners or {})
    required_by = dict(required_by or {})
    calls: list = []

    def run(cmd, args=None, *_a, **_kw):
        # subprocess-style: run(["arch-chroot", "/mnt", "pacman", "-Qq"])
        if isinstance(cmd, (list, tuple)):
            argv = list(cmd)
            cmd, args = (argv[0], argv[1:]) if argv else ("", [])
            if "pacman" in argv:
                cmd, args = "pacman", argv[argv.index("pacman") + 1:]
        args = list(args or [])
        calls.append([cmd, *args])

        if cmd != "pacman":
            if other is not None:
                answer = other(cmd, args)
                if answer is not None:
                    return _result(answer)
            return _result()

        flag = args[0] if args else ""
        rest = [a for a in args[1:] if not a.startswith("-")]

        if flag == "-Qq":
            return _result(_lines(installed))
        if flag == "-Qqe":
            return _result(_lines(explicit))
        if flag == "-Qqm":
            return _result(_lines(foreign))
        if flag == "-Slq":
            return _result(_lines(repo))
        if flag == "-Sgq":
            return _result(_lines(groups))
        if flag == "-Sg":
            asked = rest or list(groups)
            return _result(_lines(f"{g} {m}" for g in asked
                                 for m in groups.get(g, ())))
        if flag == "-T":
            # pacman prints what is NOT satisfied, and exits 127 when any is.
            missing = [n for n in rest if n not in set(satisfied)]
            return _result(_lines(missing), 127 if missing else 0)
        if flag == "-Sp":
            wanted = rest[-1] if rest else ""
            return _result("", 0 if wanted in set(provided) else 1)
        if flag in ("-Qo", "-Qqo"):
            path = rest[-1] if rest else ""
            owner = owners.get(path)
            if owner is None:
                return _result("", 1)
            return _result(owner if flag == "-Qqo"
                           else f"{path} is owned by {owner} 1.0-1\n")
        if flag == "-Q":
            pkg = rest[-1] if rest else ""
            return _result("", 0 if pkg in set(installed) else 1)
        if flag == "-Qi":
            pkg = rest[-1] if rest else ""
            if pkg and pkg not in set(installed):
                return _result("", 1)
            dependents = required_by.get(pkg, ())
            return _result("Required By     : "
                           + (" ".join(dependents) if dependents else "None") + "\n")
        if flag in _MUTATING or any(a in _MUTATING for a in args):
            return _result()

        raise UnmodelledPacmanQuery(
            f"this double was asked `pacman {' '.join(args)}` and does not model "
            f"{flag!r}. An empty answer is NOT neutral — for `-T` it means "
            f"'everything is satisfied'. Model it in pacman_double (tests/support/"
            f"pacman.py) or state the answer in the test. Modelled today: "
            f"{', '.join(_MODELLED)}."
        )

    run.calls = calls                                   # type: ignore[attr-defined]
    run.calls_for = lambda c: [a[1:] for a in calls if a and a[0] == c]  # type: ignore[attr-defined]
    return run

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List

from ..target.target import Target
from .state_store import write_json_atomically


@dataclass
class GenInfo:
    number: int
    is_current: bool
    # True when the apply that produced it failed part-way: the system was
    # mutated but never converged, so this is a record of progress, not a state
    # to return to (see Manifest.partial).
    partial: bool = False


class GenerationStore:
    """Records/lists/restores generations under <target>/var/lib/dasik/generations.

    Each generation N is a directory holding the config snapshot and the state
    manifest that produced it. A ``current`` symlink points at the active one.
    """

    def __init__(self, target: Target):
        self._target = target

    @property
    def base_dir(self) -> Path:
        return Path(self._target.path("/var/lib/dasik/generations"))

    @property
    def current_link(self) -> Path:
        return self.base_dir / "current"

    def _next_number(self) -> int:
        if not self.base_dir.exists():
            return 1
        nums = [int(p.name) for p in self.base_dir.iterdir()
                if p.is_dir() and p.name.isdigit()]
        return (max(nums) + 1) if nums else 1

    def _point_current_at(self, number: int) -> None:
        link = self.current_link
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(str(number))

    def new(self, config: dict[str, Any], manifest_dict: dict[str, Any]) -> int:
        n = self._next_number()
        gen_dir = self.base_dir / str(n)
        gen_dir.mkdir(parents=True, exist_ok=True)
        # Both land atomically: a generation half-written by a power cut is a
        # config `rollback` would happily restore.
        write_json_atomically(gen_dir / "config.json", config)
        write_json_atomically(gen_dir / "state.json", manifest_dict)
        self._point_current_at(n)
        return n

    def _is_partial(self, gen_dir: Path) -> bool:
        try:
            state = json.loads((gen_dir / "state.json").read_text())
        except (OSError, json.JSONDecodeError):
            return False
        return bool(state.get("partial", False))

    def list(self) -> list[GenInfo]:
        if not self.base_dir.exists():
            return []
        current = None
        if self.current_link.is_symlink():
            current = self.current_link.readlink().name
        dirs = [p for p in self.base_dir.iterdir() if p.is_dir() and p.name.isdigit()]
        gens: list[GenInfo] = []
        for p in sorted(dirs, key=lambda p: int(p.name)):
            gens.append(GenInfo(number=int(p.name), is_current=(p.name == current),
                                partial=self._is_partial(p)))
        return gens

    def prune(self, keep: int) -> List[int]:
        """Delete all but the *keep* most recent generations. Returns what went.

        Explicit only — there is no automatic cap on ``apply`` and no
        ``keep_generations`` in the config, because both delete history as a
        side effect of something else, and the generation somebody is about to
        roll back to is exactly the one an automatic policy takes.

        Two things are never deleted whatever *keep* says:

        * the **current** generation, which is what the machine is running;
        * the newest **complete** one, because ``restore`` refuses a partial
          generation — pruning down to nothing but partials would leave a
          history that cannot be rolled back to at all.
        """
        if keep < 1:
            raise ValueError("keep must be at least 1: a history with no "
                             "generations in it cannot be rolled back to.")
        gens = self.list()
        if len(gens) <= keep:
            return []

        survivors = {g.number for g in gens[-keep:]}
        survivors |= {g.number for g in gens if g.is_current}
        newest_complete = next(
            (g.number for g in reversed(gens) if not g.partial), None)
        if newest_complete is not None:
            survivors.add(newest_complete)

        removed: List[int] = []
        for gen in gens:
            if gen.number in survivors:
                continue
            shutil.rmtree(self.base_dir / str(gen.number), ignore_errors=True)
            removed.append(gen.number)
        return removed

    def restore(self, number: int) -> tuple[dict[str, Any], dict[str, Any]]:
        gen_dir = self.base_dir / str(number)
        if not gen_dir.is_dir():
            raise FileNotFoundError(f"Generation {number} not found")
        if self._is_partial(gen_dir):
            # Rolling back TO a half-applied state would re-apply a config that
            # was never fully converged and hand it a manifest claiming ownership
            # dasik never established. Roll back to an earlier COMPLETE generation
            # instead, or fix the failure and re-run apply.
            raise ValueError(
                f"Generation {number} is partial (its apply failed part-way) and "
                "cannot be rolled back to; pick an earlier complete generation "
                "or fix the failure and run `dasik apply` again."
            )
        config = json.loads((gen_dir / "config.json").read_text())
        manifest = json.loads((gen_dir / "state.json").read_text())
        self._point_current_at(number)
        return config, manifest

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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

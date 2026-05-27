import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..target.target import Target


@dataclass
class GenInfo:
    number: int
    is_current: bool


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
        (gen_dir / "config.json").write_text(json.dumps(config, indent=2))
        (gen_dir / "state.json").write_text(json.dumps(manifest_dict, indent=2))
        self._point_current_at(n)
        return n

    def list(self) -> list[GenInfo]:
        if not self.base_dir.exists():
            return []
        current = None
        if self.current_link.is_symlink():
            current = self.current_link.readlink().name
        dirs = [p for p in self.base_dir.iterdir() if p.is_dir() and p.name.isdigit()]
        gens: list[GenInfo] = []
        for p in sorted(dirs, key=lambda p: int(p.name)):
            gens.append(GenInfo(number=int(p.name), is_current=(p.name == current)))
        return gens

    def restore(self, number: int) -> tuple[dict[str, Any], dict[str, Any]]:
        gen_dir = self.base_dir / str(number)
        if not gen_dir.is_dir():
            raise FileNotFoundError(f"Generation {number} not found")
        config = json.loads((gen_dir / "config.json").read_text())
        manifest = json.loads((gen_dir / "state.json").read_text())
        self._point_current_at(number)
        return config, manifest

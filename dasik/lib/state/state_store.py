import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from ..target.target import Target

STATE_VERSION = 1


@dataclass
class Manifest:
    """What dasik manages/owns on the target (the active generation's record)."""

    version: int = STATE_VERSION
    generation: int = 0
    applied_at: str | None = None
    config_hash: str | None = None
    managed: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Manifest":
        return cls(
            version=data.get("version", STATE_VERSION),
            generation=data.get("generation", 0),
            applied_at=data.get("applied_at"),
            config_hash=data.get("config_hash"),
            managed=data.get("managed", {}),
        )


class StateStore:
    """Reads/writes the dasik state manifest under <target>/var/lib/dasik."""

    def __init__(self, target: Target):
        self._target = target

    @property
    def state_path(self) -> Path:
        return Path(self._target.path("/var/lib/dasik/state.json"))

    def load(self) -> Manifest:
        p = self.state_path
        if not p.exists():
            return Manifest()
        return Manifest.from_dict(json.loads(p.read_text()))

    def save(self, manifest: Manifest) -> None:
        p = self.state_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(manifest.to_dict(), indent=2))

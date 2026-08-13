import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from ..target.target import Target

STATE_VERSION = 2


@dataclass
class Manifest:
    """What dasik manages/owns on the target (the active generation's record).

    ``action_state`` (schema v2) holds per-action free-form state keyed by domain,
    e.g. ``{"packages": {"source_refs": {name: applied_sha}}}`` — this lets a
    changed Git ref be detected even when the package name is already installed.

    ``partial`` marks a manifest written by an apply that FAILED part-way: the
    system was mutated, but it is not the declared state. Such a record exists so
    the progress is visible (and ownership is not silently lost); it is never a
    convergence claim, and `rollback` refuses to restore one.
    """

    version: int = STATE_VERSION
    generation: int = 0
    applied_at: str | None = None
    config_hash: str | None = None
    managed: dict[str, Any] = field(default_factory=dict)
    action_state: dict[str, Any] = field(default_factory=dict)
    partial: bool = False

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
            # Absent in pre-v2 manifests — default to empty so old state loads.
            action_state=data.get("action_state", {}),
            # Absent in manifests written before partial-progress recording.
            partial=bool(data.get("partial", False)),
        )


def write_json_atomically(path: Path, payload: dict[str, Any]) -> None:
    """Write *payload* so a reader sees the old file or the new one, never half.

    A power cut during an apply is not hypothetical (#214 came from one), and
    `write_text` leaves a truncated file in the window between truncate and
    flush. These files are dasik's record of what it owns and of what a
    `rollback` would restore, so: same-directory temporary, fsync, rename —
    which is atomic on POSIX.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(payload, indent=2))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


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
        try:
            data = json.loads(p.read_text())
        except json.JSONDecodeError as e:
            # A manifest written before saves became atomic, or one a full disk
            # truncated. It is recoverable — `sync` rebuilds ownership from the
            # machine — so say that instead of raising json's parser error.
            raise ValueError(
                f"{p} is not valid JSON ({e}). dasik's record of what it owns is "
                "unreadable; rebuild it from the machine with `dasik sync <config>` "
                "(or delete the file to start owning nothing).") from e
        return Manifest.from_dict(data)

    def save(self, manifest: Manifest) -> None:
        """Write the manifest atomically.

        This is the record of what dasik owns, so it lands whole or not at all.
        """
        write_json_atomically(self.state_path, manifest.to_dict())

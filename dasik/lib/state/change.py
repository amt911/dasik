from dataclasses import dataclass, field
from enum import Enum


class Op(Enum):
    INSTALL = "install"
    REMOVE = "remove"
    MODIFY = "modify"
    ENABLE = "enable"
    DISABLE = "disable"
    CREATE = "create"
    DELETE = "delete"


_DESTRUCTIVE_OPS = frozenset({Op.REMOVE, Op.DISABLE, Op.DELETE})

_SIGNS = {
    Op.INSTALL: "+", Op.CREATE: "+", Op.ENABLE: "+",
    Op.REMOVE: "-", Op.DELETE: "-", Op.DISABLE: "-",
    Op.MODIFY: "~",
}


@dataclass(frozen=True)
class Change:
    """A single proposed change in one domain."""

    domain: str
    op: Op
    item: str
    reason: str = ""
    # Some changes are destructive in a way the OP cannot express: the disk
    # domain emits its repartition as Op.INSTALL, yet it runs `wipefs --all` +
    # `sgdisk --zap-all` + mkfs. Only the domain knows, so it says so here. The
    # flag only ever ADDS destructiveness — __post_init__ forces it True for the
    # ops that are destructive by definition, so destructive=False cannot make a
    # REMOVE look safe.
    destructive: bool = False

    def __post_init__(self) -> None:
        if self.op in _DESTRUCTIVE_OPS and not self.destructive:
            object.__setattr__(self, "destructive", True)

    def render(self) -> str:
        sign = _SIGNS[self.op]
        tail = f"  ({self.reason})" if self.reason else ""
        # An Op.INSTALL that erases a device must not read like any other "+".
        mark = ("  ** DESTRUCTIVE **"
                if self.destructive and self.op not in _DESTRUCTIVE_OPS else "")
        return f"  {sign} [{self.domain}] {self.op.value} {self.item}{tail}{mark}"


@dataclass
class Plan:
    """An ordered aggregate of Changes across domains."""

    changes: list[Change] = field(default_factory=list)

    def add(self, change: Change) -> None:
        self.changes.append(change)

    def extend(self, changes: list[Change]) -> None:
        self.changes.extend(changes)

    def is_empty(self) -> bool:
        return not self.changes

    def destructive(self) -> list[Change]:
        return [c for c in self.changes if c.destructive]

    def render(self) -> str:
        if not self.changes:
            return "No changes - system matches config."
        return "\n".join(c.render() for c in self.changes)

from typing import Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..target.target import Target


class ActionContext:
    """Shared context between actions during installation.

    This allows actions to share state and communicate with each other.
    For example, disk partitioning action can store partition mappings
    that will be used by the base installation action.

    v3 additions (spec §3.1, §3.5):
    - ``target``: the root commands run against (``/`` for day-2, ``/mnt`` for
      install). Read by v3 actions and forwarded to ``Command.execute(target=…)``.
    - ``manifest``: the active state manifest as a plain ``dict`` (pass
      ``StateStore.load().to_dict()`` — **not** the ``Manifest`` dataclass
      directly, so v3 actions can index it with
      ``ctx.manifest["managed"][domain]``). Read by v3 actions inside
      ``plan()`` so they can compute REMOVE = M \\ D.

    - ``assume_yes``: the CLI's ``--yes``. An action that would otherwise ask
      the human something during ``apply`` must not: ``--yes`` is the promise
      that nobody is there to answer. Found in a VM, where the guest installer
      runs on a serial console — stdin IS a terminal, so a terminal check alone
      was not enough and `dasik apply --yes` sat for ever at "plug in FIDO2 key
      1 of 2".

    All three default so legacy actions and existing call-sites that do
    ``ActionContext()`` keep working unchanged.
    """

    def __init__(
        self,
        target: Optional["Target"] = None,
        manifest: Optional[Dict[str, Any]] = None,
        assume_yes: bool = False,
    ):
        """Initialize empty context."""
        self._data: Dict[str, Any] = {}
        self.partition_map: Dict[str, str] = {}
        self.target = target
        self.manifest = manifest
        self.assume_yes = assume_yes
    
    def set(self, key: str, value: Any) -> None:
        """Store a value in the context.
        
        Args:
            key: Context key
            value: Value to store
        """
        self._data[key] = value
    
    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a value from the context.
        
        Args:
            key: Context key
            default: Default value if key not found
            
        Returns:
            Stored value or default
        """
        return self._data.get(key, default)
    
    def has(self, key: str) -> bool:
        """Check if a key exists in the context.
        
        Args:
            key: Context key to check
            
        Returns:
            True if key exists
        """
        return key in self._data
    
    def set_partition(self, label: str, device: str) -> None:
        """Store a partition mapping.
        
        Args:
            label: Partition label (e.g., 'root', 'boot')
            device: Device path (e.g., '/dev/sda1')
        """
        self.partition_map[label] = device
    
    def get_partition(self, label: str) -> Optional[str]:
        """Get device path for a partition label.
        
        Args:
            label: Partition label
            
        Returns:
            Device path or None if not found
        """
        return self.partition_map.get(label)
    
    def get_all_partitions(self) -> Dict[str, str]:
        """Get all partition mappings.
        
        Returns:
            Dictionary of label -> device mappings
        """
        return self.partition_map.copy()

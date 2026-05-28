from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    """The root filesystem dasik operates on.

    - root == "/"   : day-2 management of the running host; commands run directly.
    - root == "/mnt": install target; commands run via ``arch-chroot <root>``.
    """

    root: str = "/mnt"

    @property
    def is_chroot(self) -> bool:
        """True when commands must run inside ``arch-chroot <root>``."""
        return self.root != "/"

    def path(self, absolute: str) -> str:
        """Map an in-target absolute path to the corresponding host path.

        For root="/" the path is returned unchanged. For root="/mnt" and
        absolute="/etc/hostname" returns "/mnt/etc/hostname".
        """
        if not absolute.startswith("/"):
            raise ValueError(f"path must be absolute, got: {absolute!r}")
        if self.root == "/":
            return absolute
        return self.root.rstrip("/") + absolute

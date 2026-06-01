"""dracut backend: derive /etc/dracut.conf.d/dasik.conf + run dracut."""
from __future__ import annotations
import os
from typing import List, Optional
from .base import InitramfsBackend
from ...command_worker.command_worker import Command

_CONF = "/etc/dracut.conf.d/dasik.conf"


class DracutBackend(InitramfsBackend):

    def _modules(self) -> List[str]:
        mods: List[str] = []
        if self.has_encryption:
            mods.append("crypt")
        if self.root_fs == "btrfs":
            mods.append("btrfs")
        return mods

    def desired_value(self) -> str:
        mods = self._modules()
        if not mods:
            return ""
        return f'# Managed by dasik\nadd_dracutmodules+=" {" ".join(mods)} "\n'

    def actual_value(self) -> Optional[str]:
        try:
            with open(self._path(_CONF), "r") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def apply(self) -> None:
        desired = self.desired_value()
        path = self._path(_CONF)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(desired)
        if self.target is not None:
            Command.execute("dracut", ["--regenerate-all", "--force"], target=self.target)
        else:
            Command.execute("dracut", ["--regenerate-all", "--force"], True)

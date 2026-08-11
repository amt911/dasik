"""Model for CPU frequency scaling (the old installer's `install_cpu_scaler`).

`amd_pstate=active` is what the imperative installer appended to every boot
entry on AMD; Intel's equivalent is `intel_pstate`, which the kernel enables by
default — dasik emits it explicitly anyway so the resulting cmdline is
deterministic and reviewable rather than "whatever the kernel decided".
"""
import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

_INTEL_MODES = ("active", "passive", "disable")
_GOVERNOR_RE = re.compile(r"^[a-z_]+$")


class CpuModel(BaseModel):
    """Declarative CPU scaling policy."""

    scaling_driver: Literal["auto", "amd_pstate", "intel_pstate", "acpi_cpufreq", "none"] = Field(
        "auto", description="auto detects the CPU vendor from /proc/cpuinfo")
    mode: Literal["active", "guided", "passive", "disable"] = Field(
        "active", description="driver mode (guided is AMD-only)")
    power_profiles_daemon: bool = Field(
        True, description="install and enable power-profiles-daemon")
    governor: Optional[str] = Field(
        None, description="cpupower governor, e.g. 'performance'. Leave unset to "
                          "let power-profiles-daemon own the policy.")

    @field_validator("governor")
    @classmethod
    def _plain_identifier(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _GOVERNOR_RE.match(v):
            raise ValueError("governor must be a plain identifier, e.g. 'performance'")
        return v

    @model_validator(mode="after")
    def _mode_fits_driver(self) -> "CpuModel":
        # 'guided' exists only on amd_pstate; 'disable' is not a mode dasik
        # emits for amd_pstate (use scaling_driver="none" instead).
        if self.scaling_driver == "amd_pstate" and self.mode == "disable":
            raise ValueError("use scaling_driver='none' instead of mode='disable'")
        if self.scaling_driver == "intel_pstate" and self.mode not in _INTEL_MODES:
            raise ValueError(f"intel_pstate accepts {list(_INTEL_MODES)}, not {self.mode!r}")
        return self

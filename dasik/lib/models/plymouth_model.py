"""Model for the `plymouth` block (boot splash)."""
import re
from typing import Optional
from pydantic import BaseModel, Field, field_validator

# The theme name reaches /etc/plymouth/plymouthd.conf and
# /usr/share/plymouth/themes/<name>; keep it a plain token.
_THEME_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")


class PlymouthModel(BaseModel):
    """Boot splash. An absent block means no splash at all."""
    theme: Optional[str] = Field(
        None,
        description="Plymouth theme (e.g. 'bgrt', 'spinner'). Unset leaves "
                    "plymouth's own default in place.",
    )

    @field_validator("theme")
    @classmethod
    def _validate_theme(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _THEME_RE.fullmatch(v):
            raise ValueError(
                f"Invalid plymouth theme {v!r}: must match [A-Za-z0-9_.-]{{1,64}} "
                f"(it names a directory under /usr/share/plymouth/themes)."
            )
        return v

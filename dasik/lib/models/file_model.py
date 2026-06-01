"""Model for a single declarative dropped file."""
from pydantic import BaseModel, Field, field_validator


class FileEntry(BaseModel):
    """One managed file: a filename (no path separators) and its content."""
    name: str = Field(..., description="Filename only, no path separators")
    content: str = Field(..., description="Verbatim file content")

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not v or "/" in v:
            raise ValueError("name must be a non-empty filename without '/'")
        return v

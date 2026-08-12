"""Model for the `config_saver` block.

config-saver backs up the parts of `$HOME` (and `/etc`) that a config file
cannot reasonably carry — themes, browser profiles, keyboard layouts, whole
directories. dasik declares the *policy* (which configurations exist, whose
timer runs) and, on a fresh machine, restores an archive the old one produced.

The package is not in the AUR: its PKGBUILD lives in a plain Git repository, so
the block can carry the source that builds it.
"""
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

_SHA1_HEX = re.compile(r"[0-9a-fA-F]{40}")
_CONFIG_NAME = re.compile(r"[A-Za-z0-9_.-]{1,128}")
_VALID_USERNAME = re.compile(r"[a-z_][a-z0-9_-]*\$?")


class ConfigSaverSource(BaseModel):
    """The Git PKGBUILD that builds config-saver (it is not in the AUR)."""
    url: str = Field(..., description="HTTPS Git URL of the PKGBUILD repository")
    ref: str = Field(..., description="Full 40-char commit SHA — the build is pinned")
    subdir: str = Field(default=".", description="PKGBUILD subdirectory")

    @field_validator("url")
    @classmethod
    def _https(cls, v: str) -> str:
        if not v.startswith("https://") or not v.endswith(".git"):
            raise ValueError(f"config_saver.source.url must be an https .git URL, got {v!r}")
        return v

    @field_validator("ref")
    @classmethod
    def _full_sha(cls, v: str) -> str:
        if not _SHA1_HEX.fullmatch(v):
            raise ValueError(
                f"config_saver.source.ref must be a full 40-char commit SHA, got {v!r}")
        return v


class ConfigSaverRestore(BaseModel):
    """One archive to unpack into a user's ``$HOME`` on the target."""
    user: str = Field(..., description="Whose home the archive is restored into")
    archive: str = Field(
        ...,
        description="Absolute path ON THE TARGET of the .tar.gz config-saver "
                    "produced (a mounted pendrive, a copied file).",
    )

    @field_validator("user")
    @classmethod
    def _valid_user(cls, v: str) -> str:
        if not _VALID_USERNAME.fullmatch(v or ""):
            raise ValueError(f"invalid username {v!r}")
        return v

    @field_validator("archive")
    @classmethod
    def _absolute(cls, v: str) -> str:
        if not v.startswith("/") or ".." in v.split("/"):
            raise ValueError(
                f"config_saver.restore.archive must be an absolute path on the "
                f"target with no '..' segment, got {v!r}")
        return v


class ConfigSaverModel(BaseModel):
    """config-saver: the package, its configurations, its timers, its restores.

    An empty block still means something — install config-saver.
    """
    source: Optional[ConfigSaverSource] = Field(
        default=None,
        description="Where to build the package from. Without it, the name must "
                    "resolve somewhere else (a repo, the AUR, or a "
                    "`package_sources` entry you declare yourself).",
    )
    configs: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="name -> config-saver document, written to "
                    "/etc/config-saver/configs/<name>.json (it reads JSON as "
                    "well as YAML).",
    )
    timer_users: List[str] = Field(
        default_factory=list,
        description="Users whose config-saver@<user>.timer is enabled.",
    )
    restore: List[ConfigSaverRestore] = Field(
        default_factory=list,
        description="Archives to unpack into a user's $HOME. Each one is "
                    "restored once per archive CONTENT: replacing the file with "
                    "a newer capture restores again, re-running does not.",
    )

    @field_validator("configs")
    @classmethod
    def _names_are_filenames(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        for name in v:
            if not _CONFIG_NAME.fullmatch(name):
                raise ValueError(
                    f"invalid config_saver config name {name!r}: it becomes a "
                    "file under /etc/config-saver/configs, so it must be a plain "
                    "file name")
        return v

    @field_validator("timer_users")
    @classmethod
    def _valid_users(cls, v: List[str]) -> List[str]:
        for user in v:
            if not _VALID_USERNAME.fullmatch(user or ""):
                raise ValueError(f"invalid username {user!r} in config_saver.timer_users")
        return v

# Design: complete the `files` domain — arbitrary `/etc` paths

Date: 2026-05-31
Status: approved (design), pending implementation plan

## Context

The original convergence spec (`2026-05-27-…`, §2) defines the **files** domain as
`udev_rules`, `modprobe_conf`, `profile_d`, `etc_environment`, **…** with the manifest being
a `path → hash` map and the destructive op "delete file". The `…` + path-keyed manifest mean
the domain was meant to manage **arbitrary file paths**.

Plan 8 migrated `files` to v3 but implemented only the narrow subset — three fixed
convenience sections (`{name, content}` → a fixed directory) plus `etc_environment`. Arbitrary
`/etc` paths (e.g. `sshd_config`, `smb.conf`, any drop-in) were the unbuilt `…`. This was
**slice-1 scope** (spec §7 "In": migrate files), not a future TODO — so this is finishing the
`files` domain, not new scope.

`DropFilesAction` is already **path-keyed** internally: `_desired()` returns
`{canonical_path: content}`, and `plan`/`apply`/`managed_keys`/`import_state` operate over
those paths. Adding arbitrary paths is therefore mostly a new config section that injects
`{path: content}` entries — the convergence machinery already handles them.

## Decisions (from brainstorming)

- New config field `files: list[{path, content}]` (absolute paths, user-chosen). Coexists
  with the existing convenience sections; all feed the one `files` domain.
- Ownership is unchanged: dasik owns only what is declared; DELETE = `M \ D` from the
  manifest; **no directory glob / no drift discovery** (no ownership marker in shared dirs).
- Drop-in vs overwrite is the user's choice via the path (`/etc/ssh/sshd_config.d/99-dasik.conf`
  for a drop-in that preserves package defaults, vs `/etc/ssh/sshd_config` to own the file).
- Block-managed-within-a-file (`# dasik-begin/end`) is out of scope — use drop-in dirs.

## 1. Config model

Add `EtcFile` to `dasik/lib/models/file_model.py`:

```python
class EtcFile(BaseModel):
    path: str        # absolute target path
    content: str

    @field_validator("path")
    @classmethod
    def _abs_no_traversal(cls, v: str) -> str:
        if not v.startswith("/") or ".." in v.split("/"):
            raise ValueError("path must be absolute and contain no '..' segment")
        return v
```

`JsonModel` gains `files: List[EtcFile] = Field(default_factory=list)` and exports `EtcFile`
in `dasik/lib/models/__init__.py`.

## 2. `DropFilesAction` — arbitrary paths (domain `"files"`, unchanged)

- `__init__`: read `self._etc_files = cfg.get("files", [])`.
- `_desired()`: after the existing sections, add `desired[path] = content` for each `files`
  entry (entries are plain dicts post-`JsonParser.debug()`; reuse `_entry_fields`-style access
  for `path`/`content`).
- `actual()` / `plan()` / `apply()` / `managed_keys()` are **unchanged** — already path-keyed,
  so arbitrary `/etc` paths flow through CREATE (`D\A`), DELETE (`M\D`, orphan cleanup),
  MODIFY (content drift), and the write/remove apply.
- `import_state()`: add a `"files"` key to the returned fragment — rebuild
  `[{path, content}]` from the declared `files` entries, refreshing `content` from disk when
  the path exists (capture manual edits), else keeping the declared content. The existing
  per-section + `etc_environment` rebuild is untouched.
- `_abs(path)` already resolves any absolute path via `target.path` (with the `/mnt` fallback).

## 3. Testing (TDD, 80% gate)

- `EtcFile`: accepts `{path, content}`; rejects a relative path and a path containing `..`.
- `DropFilesAction`:
  - `_desired()` includes arbitrary `files` paths alongside the convenience sections.
  - `plan()`: CREATE a missing `/etc/ssh/sshd_config.d/99-dasik.conf`; DELETE an orphan
    (`M\D`); MODIFY on content drift; empty when converged.
  - `apply()`: writes the arbitrary path (`makedirs` + content); removes an orphan path.
  - `managed_keys()` lists the arbitrary path among the canonical paths.
  - `import_state()`: rebuilds the `files` section, refreshing content from disk.

## Out of scope (future)

- Discovering undeclared files (no ownership marker → no safe glob), unchanged from Plan 8.
- Block-managed regions inside a shared file.
- `{path, source-file}` (content from a separate repo asset) — inline `content` only for now.

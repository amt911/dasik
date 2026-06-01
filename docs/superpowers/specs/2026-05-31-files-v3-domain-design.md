# Design: migrate `files` (drop_files) to the v3 contract (content-addressed)

Date: 2026-05-31
Status: approved (design), pending implementation plan

## Context

`packages`, `systemd`, `users` are the v3 domains. This slice adds `files` — the
`DropFilesAction` that writes udev rules, modprobe snippets, profile.d scripts, and
`/etc/environment`. Today entries are bare strings and the on-disk filename is derived from
the **list index** (`99-dasik-01.rules`), so reordering or removing an entry churns file
identity and **orphan files are never deleted**. The v3 value here is stable identity +
orphan cleanup (DELETE).

`DropFilesAction` is already registered `config_key="__root__"` (it reads root-level file
sections) but is **not** target-aware (paths hardcode `/mnt`).

## Decisions (from brainstorming)

- **File identity = explicit name.** Each entry becomes `{name, content}` (e.g.
  `{"name": "99-razer.rules", "content": "..."}`). Identity is the user-chosen filename,
  which also controls lexical ordering (udev/profile.d). NixOS `environment.etc` style.
  **Breaking** config change for `udev_rules`/`modprobe_conf`/`profile_d`.
- **`etc_environment`** stays a `List[str]` of lines → one managed file at the fixed path
  `/etc/environment` (content = lines joined). It joins the same `files` domain as a normal
  managed file (CREATE/MODIFY/DELETE). dasik already overwrites the whole file today.
- **`actual()` (A) = declared paths that exist on disk** (no directory glob). With
  user-chosen names there is no ownership marker, so dasik never globs — it only touches
  paths it knows from the config (D) or the manifest (M). DELETE = `M \ D` is manifest-
  driven and safe; there is no undeclared-file drift discovery.
- **MODIFY modeling (Approach A):** `compute_changes` does CREATE/DELETE on the path set;
  `DropFilesAction` adds a MODIFY layer comparing on-disk content vs desired. `set_math`
  stays unchanged.

## 1. Config model

New `FileEntry`:

```python
class FileEntry(BaseModel):
    name: str       # filename only, no path separators
    content: str
```

`name` is validated with a `field_validator`: it must be non-empty and contain no `/`
(reject path traversal / nested paths), raising `ValueError` otherwise.

`JsonModel` field type changes:

```python
udev_rules: List[FileEntry] = []      # was List[str]
modprobe_conf: List[FileEntry] = []   # was List[str]
profile_d: List[FileEntry] = []       # was List[str]
etc_environment: List[str] = []       # unchanged (lines of /etc/environment)
```

## 2. `set_math` — unchanged

`Op.CREATE` / `Op.DELETE` (destructive, gated) / `Op.MODIFY` already exist. `compute_changes`
handles CREATE/DELETE on the path set; MODIFY is layered in the action.

## 3. `DropFilesAction` v3 — domain `"files"`

Items are **canonical** paths (`/etc/udev/rules.d/<name>`, `/etc/modprobe.d/<name>`,
`/etc/profile.d/<name>`, `/etc/environment`) — target-independent identity. Real file I/O
resolves them through `target.path(p)`.

- `_target()` / `_abs(canonical)` = `target.path(canonical)` (falls back to `/mnt`+canonical
  when no target, for legacy call-sites).
- `_desired() -> dict[str, str]`: `{canonical_path: content}` for the three sections (by
  name) plus `/etc/environment` (lines joined + trailing newline). Section content is stored
  **verbatim** from `FileEntry.content` (the user controls trailing newlines).
- `actual() -> set[str]`: the subset of `_desired()` paths that exist on disk
  (`os.path.exists(self._abs(p))`). Empty without target. Self-contained — DELETE does not
  need A.
- `plan(managed)`:
  ```python
  changes, _ = compute_changes("files", desired=desired_paths, managed=managed,
                               actual=self.actual(), op_install=Op.CREATE, op_remove=Op.DELETE)
  # MODIFY layer: for p in desired∩actual whose on-disk content != desired → MODIFY
  ```
- `apply(changes)` (CREATE/MODIFY before DELETE):
  - CREATE/MODIFY `p`: `os.makedirs(dirname(self._abs(p)))`, write `_desired()[p]`.
  - DELETE `p`: `os.remove(self._abs(p))` if it exists.
  - no-op without target.
- `managed_keys() -> {"files": sorted(desired_paths)}`.
- `import_state(managed)` (sync): rebuild each section list from the declared entries,
  **refreshing `content` from disk** for files that exist (captures manual edits to owned
  files); keep declared intent for files not present; no glob/drift discovery. Returns
  `{"udev_rules": [...], "modprobe_conf": [...], "profile_d": [...], "etc_environment": [...]}`
  (top-level keys → merged by `ConfigWriter`). `/etc/environment` is split back into lines.
- Legacy `is_needed`/`execute`/`verify` kept for the old executor path, migrated to the
  `{name, content}` model + target-aware paths (create/modify only — no DELETE, matching
  today's behaviour).

## 4. Testing (TDD, 80% gate)

- `FileEntry`: accepts `{name, content}`; rejects `name` containing `/` or empty.
  `JsonModel` sections accept a list of entries.
- `DropFilesAction` v3:
  - `_desired()` maps the three sections by name + `/etc/environment` from lines.
  - `actual()` returns only declared paths that exist; empty without target.
  - `plan()`: CREATE missing, DELETE orphan (`M \ D`), MODIFY on content drift, converged → empty.
  - `apply()`: writes content + `makedirs`, removes orphan, CREATE-before-DELETE, no-op without target.
  - `managed_keys()` lists canonical paths.
  - `import_state()`: refreshes content from disk, rebuilds sections, splits `/etc/environment`.

## Out of scope (future slices)

- Discovering undeclared files in the managed dirs (no ownership marker to glob safely).
- Managed-block-within-shared-file (`# dasik-begin/end`) for files dasik does not fully own.
- Multi-domain actions (`Reconciler._domain_for` still raises on >1 domain).

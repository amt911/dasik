# Design: package install reason (explicit / dependency) — pacman only

Date: 2026-06-02
Status: approved (design), pending implementation plan
Base: builds on PR #79 (sync-reflects-reality); stack accordingly.

## Context

`packages` is a flat `List[str]` (with the `aur-` prefix marking AUR). `actual()` is
`pacman -Qqe` (explicitly installed), so `sync` only ever captures **explicit** packages.

The user marks a few of their packages **as dependencies** (`pacman -D --asdeps`) — those
leave `-Qqe`, so `sync` silently drops them and never records *why* a package is there. They
want each of their packages to carry its install reason (explicit vs dependency), **without**
pulling in the full transitive dependency tree.

AUR packages are **excluded** from the reason feature: their reason is detected/handled at
install time, so they stay exactly as today (`aur-` string, no annotation).

## Decisions (from brainstorming)

- Capture reason for the packages dasik knows (declared ∪ owned), **not** the whole dep tree.
- A declared package is "present" if installed with **any** reason (so a declared `asdeps`
  package is not treated as missing and is not dropped on sync).
- **Representation:** `packages: List[Union[str, PackageSpec]]`. A plain string is an
  **explicit** pacman package (full back-compat); a `{ "name": ..., "reason": "dep" }` object
  marks a dependency. `name` keeps the `aur-` prefix if applicable.
- **AUR is reason-exempt:** AUR entries stay plain `aur-…` strings; no reason annotation, no
  `pacman -D`, no MODIFY.

## 1. Config model

`PackageSpec` (new, in a small model module, e.g. `dasik/lib/models/package_model.py`):

```python
class PackageSpec(BaseModel):
    name: str
    reason: Literal["explicit", "dep"] = "explicit"
```

`JsonModel.packages: List[Union[str, PackageSpec]] = []` (was `List[str]`). Plain strings stay
valid (explicit).

## 2. `PackagesAction` — reason-aware (pacman only)

Parse each config entry to an internal record `(name, is_aur, reason)`:
- plain `str` → `reason="explicit"`; `aur-` prefix → `is_aur=True`.
- `dict`/`PackageSpec` → `name`/`reason`; if the name carries `aur-`, `is_aur=True` and the
  reason is **ignored** (AUR is reason-exempt) → treated as a normal AUR entry.

Helpers:
- `actual()` → `pacman -Qqe` (explicit set E). **Unchanged** — drives drift capture + the
  manifest `M`; never includes dependencies.
- `_installed_any(pkg)` → `pacman -Qq <pkg>` returncode 0 (installed with any reason).
- `_reason_of(pkg)` → `"explicit"` if `pkg in E` else `"dep"` (for an installed package).

`plan(managed)`:
- INSTALL = declared packages **not installed at all** (`not _installed_any`). (AUR + pacman.)
- MODIFY = **pacman** declared package that is installed but whose current reason ≠ desired
  reason → `Op.MODIFY` (a reason change). AUR packages never emit MODIFY.
- REMOVE = `M \ D` (owned, no longer declared) — unchanged.

`apply(changes)`:
- INSTALL: `pacman -S` (pacman) / AUR build path (as today). After installing a pacman package
  whose desired reason is `dep`, run `pacman -D --asdeps <pkg>`.
- MODIFY: `pacman -D --asdeps <pkg>` or `--asexplicit <pkg>` to match the desired reason.
- REMOVE: `pacman -Rns` (as today).
- AUR untouched by the reason logic.

`import_state(managed)` (sync) — extends the reality-reflecting capture from #79:
- Declared entries are kept (intent). For a **pacman** declared package that is installed, set
  its reason from `_reason_of`: emit a `{name, reason:"dep"}` object when it is a dependency,
  otherwise a plain string. A declared `asdeps` package therefore survives sync as
  `{name, reason:"dep"}` instead of vanishing.
- AUR declared entries are kept verbatim (`aur-…` strings).
- Drift = `E \ declared` (new explicit packages) → plain strings (explicit).
- Result: a `List[Union[str, dict]]` — strings for explicit/AUR, objects only for pacman deps.

## 3. Testing (TDD, 80% gate)

- `PackageSpec`/`JsonModel`: accepts a plain string (explicit) and a `{name, reason}` object;
  rejects an invalid `reason`; AUR string accepted.
- `PackagesAction` parse: mixed list → correct `(name, is_aur, reason)`; AUR object ignores
  reason.
- `_installed_any` / `_reason_of` against a mocked `pacman`.
- `plan`: INSTALL for not-installed; MODIFY when a pacman package's reason drifts; **no**
  MODIFY for AUR; REMOVE for owned-not-declared.
- `apply`: `pacman -D --asdeps` after installing a dep package; MODIFY runs the right
  `pacman -D` flag; AUR path unaffected.
- `import_state`: a declared `asdeps` package is captured as `{name, reason:"dep"}` and not
  dropped; explicit + AUR stay strings; new explicit drift captured as strings.

## Out of scope

- Capturing transitive dependencies (explicitly excluded by the user).
- Reason for AUR packages (detected at install time).
- `pacman`/`network` composite migration (separate slice).

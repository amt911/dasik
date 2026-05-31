# Design: migrate `users` to the v3 contract (attribute-aware)

Date: 2026-05-31
Status: approved (design), pending implementation plan

## Context

`packages` (Plan 4-5) and `systemd` (Plan 6) are the v3 domains today. This slice adds
`users`. Unlike those, users carry **attributes** (shell, groups, password), so a pure
name-set `compute_changes` is not enough — attribute drift on an existing user needs a
`MODIFY` change that set-math cannot express.

`UsersAction` currently reads `/mnt/etc/passwd` + `/mnt/etc/group` for its legacy
`is_needed`/`execute`. `UserModel` is `{username, password, shell, groups}` with a
**plaintext** password.

## Decisions (from brainstorming)

- **Password is stored hashed** in the config (`$6$salt$hash`, e.g. from `openssl passwd
  -6` / `mkpasswd -m sha-512`). dasik compares it directly against field 2 of
  `/mnt/etc/shadow`, so password drift is detectable and reconciliation stays idempotent —
  and no plaintext lives in the JSON (NixOS `hashedPassword` style). Field renamed
  `password` → `hashed_password`. **Breaking** config change (acceptable pre-1.0).
- **Reconciled attributes:** `shell`, `groups`, `hashed_password` (via `MODIFY`), plus
  `CREATE`/`DELETE` by username.
- **DELETE:** a **root-level** `remove_home_on_delete: bool = False` (not per-user — a
  per-user flag is unreadable at delete time, since the user is gone from the config). When
  true, `userdel -r` (removes `/home` + mail); otherwise `userdel` (keeps `/home`). Read at
  delete time regardless of which user. To access this root field, `UsersAction` is
  registered with `config_key="__root__"` (same pattern as `NetworkAction`).
- **`actual()` (A):** usernames with **uid ≥ 1000** (Arch `UID_MIN`) from `/etc/passwd`.
  System accounts (uid < 1000) are excluded → no system-account noise in `sync`. `root`
  (uid 0) is handled specially: never `CREATE`/`DELETE`, only password `MODIFY` if declared.
- **MODIFY modeling (Approach A):** `compute_changes` computes `CREATE`/`DELETE` on the
  username set; `UsersAction` computes `MODIFY` by comparing attributes of declared∩actual
  users. `set_math` stays pure (unchanged).

## 1. Config model — `UserModel`

```python
class UserModel(BaseModel):
    username: str
    hashed_password: str       # was: password (plaintext)
    shell: str = "/bin/bash"
    groups: List[str] = []
```

- Validator: `hashed_password` must start with `$` (crypt format `$6$…`, `$y$…`); reject
  plaintext to catch un-migrated configs early.
- `JsonModel` gains root-level `remove_home_on_delete: bool = False`.
- `config/install-megamix.json` migrated to real SHA-512 hashes.

## 2. `set_math` — unchanged

`Op.CREATE` / `Op.DELETE` / `Op.MODIFY` already exist. `DELETE` is in `_DESTRUCTIVE_OPS`
(gated by the `y/N` prompt). `CREATE`/`MODIFY` are non-destructive. `compute_changes` is
used only for the `CREATE`/`DELETE` name-set; `MODIFY` is layered in the action.

## 3. `UsersAction` v3 methods

Domain `"users"`. Registered `config_key="__root__"`. `__init__` accepts **either** a bare
list (legacy direct construction / old executor) **or** the root config dict (v3 path) —
`isinstance(config, list)` → that is the users list with `remove_home_on_delete=False`;
`isinstance(config, dict)` → `config.get("users", [])` plus
`config.get("remove_home_on_delete", False)`. The action keeps `{username: user dict}` for
attribute lookup in `plan`/`apply`.

- `actual() -> set[str]`: usernames in `/mnt/etc/passwd` with `uid ≥ 1000` (parse field 3).
  Empty set when context/target missing.
- Attribute reads (helpers, target-aware): `_shell(u)` (passwd field 7), `_groups(u)`
  (membership in `/etc/group`), `_hash(u)` (`/etc/shadow` field 2).
- `plan(managed)`:
  1. `changes, _ = compute_changes("users", desired=<declared usernames minus root>,
     managed=managed, actual=self.actual(), op_install=Op.CREATE, op_remove=Op.DELETE)`.
  2. For each declared user `u` also in `actual()` (minus root): if `shell`, `groups`
     (set), or `hashed_password` differ from system → append
     `Change("users", Op.MODIFY, u, reason="<changed fields>")`.
  3. root: if declared and `_hash("root")` differs from declared hash → append
     `Change("users", Op.MODIFY, "root", reason="password")`. Never CREATE/DELETE root.
- `apply(changes)` (CREATE/MODIFY before DELETE):
  - CREATE `u`: `useradd -m -s <shell> [-G g1,g2] u`, then set hash via
    `usermod -p '<hash>' u`.
  - MODIFY `u`: `usermod -s <shell> u`; set groups `usermod -G <g1,g2> u`; if hash differs
    `usermod -p '<hash>' u`. (root: only the hash step.)
  - DELETE `u`: `userdel u`, or `userdel -r u` when `self.remove_home_on_delete` is true.
  - No-op on empty; no-op without target.
- `managed_keys() -> {"users": [declared usernames excluding root]}` — root is never owned.
- `import_state(managed)` (sync): for each uid≥1000 user, capture `{username, shell,
  groups, hashed_password}` from the system; refresh declared+present users; keep declared
  intent not present; drop owned-and-vanished (`M \ A`); append drift (`A \ D \ M`). root
  is not captured (uid 0). Returns `{"users": [ ...user dicts... ]}`.
- Legacy `is_needed`/`execute`/`verify` kept for the old `ActionExecutor` path, extended to
  use the hashed password and honor `remove_home_on_delete`.

## 4. Testing (TDD, 80% gate)

- `UserModel`: accepts `$6$…` hash; rejects plaintext (no leading `$`); `shell`/`groups`
  defaults. `JsonModel.remove_home_on_delete` default False.
- `UsersAction` v3:
  - `actual()` includes uid≥1000, excludes uid<1000 and root; empty without target.
  - `plan()`: CREATE missing, DELETE owned-not-declared, MODIFY on shell-only / groups-only
    / password-only, root password MODIFY, converged → empty.
  - `apply()`: routes useradd/usermod/userdel, `-r` only when `remove_home_on_delete`,
    CREATE before DELETE, no-op empty/no-target. `__init__` accepts list or root dict.
  - `managed_keys()` excludes root.
  - `import_state()`: drift capture with attrs, refresh declared, drop vanished, root
    excluded.

## Out of scope (future slices)

- Users with no declared password (locked `!`) and explicit UID/GID/comment fields.
- `files` (drop_files) → v3; multi-domain actions.
- Re-hashing or rotating an existing password without changing the config hash.

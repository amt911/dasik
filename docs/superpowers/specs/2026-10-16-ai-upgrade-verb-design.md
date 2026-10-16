# `dasik upgrade` — bring the declared AI tooling to its latest version

Date: 2026-10-16. Status: design approved in chat, pending plan.

## Intent

dasik installs AI skills, agent plugins, MCP servers and uv tools, and from then on only checks that
they are **present** — never which version (`ai_skills_model.py` says "Presence, never version" on
purpose). Upstream ships releases often (impeccable is the example the user gave); the installed
copies work, but stay frozen at the day they were installed. The user wants one command that brings
everything AI they declared up to date.

`apply` stays idempotent — the same JSON twice changes nothing, `plan` stays silent. Updating is a
separate, explicit act, like NixOS keeping `nixos-rebuild switch` deterministic and fetching new
versions only on `--upgrade` / `nix flake update`. That was the user's choice after the idempotency
conflict was raised: **a separate verb, not part of `apply`**.

## Goals

- New verb: `dasik upgrade <config.json>` (same `--target-root` / `-v` / `--no-log` conventions as
  `apply`). Reads the config, and for every **declared and currently installed** item in the AI
  domains runs that item's native updater.
- Scope is **AI only** (user's decision): `ai_skills`, `uv_tools`, and whatever of `mcp_servers`
  needs it. Never pacman/AUR packages, never anything else.
- Prints a summary at the end: per item, `updated old → new`, `already latest`, `skipped (reason)`
  or `failed (reason)`. Versions shown when they can be read; `version unknown` otherwise.
- A failing item does not stop the rest. The verb exits non-zero if any item failed.

## Non-goals

- Not part of `apply`, not announced by `plan`. Knowing whether a newer version exists needs the
  network per item; `plan` stays offline and silent.
- Does not install what is missing — that is `apply`'s job. A declared-but-absent item is reported
  `skipped (not installed — run dasik apply)`.
- Does not touch anything installed by hand and absent from the JSON.
- No new generation, no manifest change: ownership does not change, only versions. Consequently
  `rollback` does **not** revert an upgrade — say so in the verb's help/docs.
- No `--dry-run` for now.
- No pacman/AUR upgrade, now or as part of this work.

## Anti-goals (would be a failure even if it "works")

- `plan` is not silent after `upgrade` (upgrade must not create drift — e.g. rewriting a marketplace
  source, changing a uv tool's Python, re-registering an MCP with different args).
- A pinned item moves. `uv_tools` entries with a PEP 440 pin (`graphifyy==0.9.53`) stay put and are
  reported `skipped (pinned)`.
- An interactive prompt hangs the run (every updater gets its non-interactive flag).
- Running as root writes into root's home instead of the declared user's (same user/HOME handling as
  the `ai_skills` / `uv_tools` apply paths).

## The updaters (measured on the host, 2026-10-16)

| Kind | Update | Notes |
| --- | --- | --- |
| `uv_tools` entry | `uv tool upgrade <dist>` | skip if pinned; `uv tool install` alone never upgrades |
| `ai_skills` `claude-plugin` | `claude plugin marketplace update <mkt>` then `claude plugin update <plugin@mkt> -s user -y` | `-y` is required when not a TTY |
| `ai_skills` `codex-plugin` | `codex plugin marketplace upgrade` (refreshes Git marketplace snapshots), then whatever makes the installed plugin follow | **no `codex plugin update` exists** — builder measures in the VM whether re-running `codex plugin add` refreshes it, or remove+add is needed |
| `ai_skills` `antigravity-plugin` | the existing fresh `git clone --depth 1` + `agy plugin install` | no update command; builder measures whether install over an existing plugin works or needs uninstall first |
| `ai_skills` `skills` (npx) | `npx -y skills update <skill> -g -y` | per declared skill; agents handled by the lock |
| `ai_skills` `tool` (graphify) | re-run the existing `<command> install --platform <agent>` | must run **after** the `uv_tools` upgrade so the skill matches the new binary |
| `mcp_servers` | nothing for `npx …@latest` (re-resolved at start); `uvx`-based ones likewise | the MCPs installed as uv tools (inkscape_mcp) are upgraded through `uv_tools` |

Order: `uv_tools` before `ai_skills`. MCP registration is never rewritten by `upgrade`.

## Constraints

- Shell out through `Command.execute`, reuse the user/HOME/PATH handling and the command builders
  already in `ai_skills_action.py` / `uv_tools_action.py`; no private copies (AGENTS.md "Reuse
  before you write").
- Version reading reuses the existing state readers (`ai_skills_state.py`: claude
  `installed_plugins.json`, skills lock, codex `config.toml`, agy manifest; uv `pyvenv.cfg` /
  `uv tool list --show-version`).
- TDD for the new logic: which items get upgraded/skipped, the command each one gets, pin detection,
  summary/exit code.
- The repo's feature checklist and VM rule apply in full (below).

## Testing (user asked explicitly: VM, package subset, every verb)

- Unit tests under `tests/lib/` for the pure decisions.
- One journey test through `main(["upgrade", ...])` against a `tmp_path` root with commands mocked.
- One guest script, `config/vm-ai-upgrade.json` + `scripts/vmtest/guest-ai-upgrade.sh`, with a
  **basic package subset** (not a machine's full list), declaring at least one `uv_tools` entry, one
  pinned `uv_tools` entry, one `skills` entry, one `claude-plugin` and one `tool` (graphify); codex
  and antigravity entries if their CLIs install in the guest. It drives **every verb**: `check`,
  `plan`, `apply`, `plan` (silent), then makes items **stale on purpose** (e.g. reinstall a uv tool
  at an older version, roll a skill back) and runs `upgrade` — asserting the stale items moved to a
  newer version, the pinned one did not, then `plan` silent, `sync` → `check` → `plan` silent,
  `generations` (no new generation from upgrade), `rollback` (behaves as before). Also `upgrade`
  with the AI blocks removed from the config: does nothing, rc 0.
- Report which verbs ran for real in the guest.

## Calls made by the designer (not discussed with the user — flagged for review)

1. **"Pinned" = exact pin only** (`==` / `===`): skipped as `skipped (pinned)`. A range (`>=`,
   `~=`, `<`, `!=`) is upgraded, and uv keeps it inside the range.
2. **Target**: same `--target-root` semantics and default as `apply` — the same chroot/user handling
   the `ai_skills` / `uv_tools` apply paths already use. In practice it runs day-2 on the installed
   machine (root `/`).
3. **Scoped calls only**: every refresh names its marketplace (`claude plugin marketplace update
   <name>`, `codex plugin marketplace upgrade <name>`), never the "all" form. Refreshing a declared
   marketplace's metadata is fine. Updating a plugin that isn't declared is not.
4. **Remove+add is allowed** for codex/antigravity if measurement shows it is the only way. If the
   add fails, the item is reported `failed`, and the next `apply` sees it missing and reinstalls it.
5. **The declaration wins over uv's receipt**: an unpinned declared tool is refreshed with the
   declared spec (e.g. `uv tool install --upgrade <declared spec>`, preserving a declared `python`),
   so a receipt left pinned by a manual install doesn't freeze it forever. The guest fakes staleness
   with `uv tool install <dist>==<old> --force`, which this path must undo. The builder measures
   which uv invocation does that without drifting `plan`.
6. **graphify `tool` runs even if its uv upgrade failed**: it reinstalls the skill for whatever
   binary is present. Each failure is counted on its own.
7. **Version unreadable before or after**: reported `refreshed (version unknown)` and counted as
   success. For those kinds, the guest asserts a change by content (file hash / lock entry), not by
   version.

## Left to the builder

- Where the per-domain upgrade logic lives (a method on the actions involved vs. a small module) —
  within the "no abstraction without a second implementation" rule; there are at least two domains.
- Exact summary formatting and colours.
- How the guest makes each kind stale, as long as the before/after versions are asserted, not
  assumed.
- The codex and antigravity mechanics in the table, measured in the VM.

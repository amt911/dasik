# opencode as a first-class AI agent — design

Date: 2026-11-23. Status: draft, awaiting approval.

## Intent

The user's three machines declare the same skills, plugins and MCP servers for
Claude Code, Codex and Antigravity (`dasik-personal-config/common/ai-skills.json`,
`common/mcp-servers.json`). opencode is installed on them too (`packages.json`)
and should get the same set, declared in the same JSON, with the same
guarantees: `plan` sees a missing or extra registration, `apply` fixes it,
`sync` reads it back, and a removed entry is removed.

Already done outside this design (dasik-personal-config `0afaf21`): `opencode`
was added to every `skills`/`tool` entry — dasik already supports it there as a
universal agent reading `~/.agents/skills`.

## Goals

1. **`mcp_servers` accepts `opencode` in `agents`.** stdio and http entries,
   `env`, and `headers`.
2. **`ai_skills` gains an `opencode-plugin` method** — e.g. superpowers, which
   the user installed by hand into `opencode.jsonc` (`"plugin":
   ["superpowers@git+https://github.com/obra/superpowers.git"]`).
3. Both detectable by `plan` in both directions, captured by `sync` as their own
   entries, removed when the entry goes — the full feature checklist
   (`docs/agents/feature-checklist.md`), every verb.

## Non-goals

- `dasik upgrade` support for opencode plugins (`opencode plugin update`).
  A follow-up if wanted.
- Project-level opencode config (`./opencode.json`). Machine (global) scope only,
  like the other agents.
- Managing the rest of opencode's config (providers, models, keys). The API keys
  are already backed up by config-saver (`opencode-data.yaml`).
- OAuth for remote MCP servers (`opencode mcp auth`) — same as figma today:
  signed in by hand once per machine.

## Anti-goals

- Touching any key of the user's opencode config other than the servers and
  plugins dasik owns. Clobbering `provider` or another server while writing
  one would be a failure even if MCP works.
- `plan` and `sync` reading a different file or shape than `apply` wrote.

## Constraints (measured 2026-11-23, opencode v2.3.0, sandboxed XDG dirs)

- **opencode loads both global files.** With `opencode.json` and
  `opencode.jsonc` both present, `opencode debug config` lists both as sources
  (`.json` first, then `.jsonc`).
- **Both shapes load.** Flat `mcp.<name>` and nested `mcp.servers.<name>` are
  both read and normalized to `mcp.servers`; the legacy `plugin` array is
  normalized to `plugins`.
- **The CLI writes only `opencode.json`, in the new shape.** `opencode mcp add
  --global` writes `mcp.servers.<name>`; `opencode plugin add <spec>` installs
  the package and appends to `plugins` — both in `opencode.json`, even when an
  `opencode.jsonc` exists. Neither touches `.jsonc`.
- `opencode mcp` has no `remove`.
- `opencode plugin remove` needs the **exact specifier**
  (`superpowers@git+https://…`; `superpowers` alone is "not configured"), only
  edits `opencode.json`, and **exits 0 when it removed nothing**.
- `opencode plugin add` worked with no service running (it spawns its own);
  `opencode mcp list` / `debug config` need the service and its port, so they
  are not a read path for `plan`.
- The user's own file is `opencode.jsonc` (hand-written `provider` and the
  legacy `plugin: ["superpowers@git+…"]`).

## Approaches considered

- **A — dasik writes the MCP servers itself (chosen by the user).** One writer,
  one shape; `plan`/`sync` read what `apply` wrote. Refined by the measurements
  above: dasik writes **`opencode.json`**, the file opencode's own CLI writes,
  in the same `mcp.servers` shape — so the user's `opencode.jsonc` and its
  comments are never rewritten in normal operation.
- B — `opencode mcp add` to add, a direct edit to remove. Two writers. Rejected.
- C — a dasik-only config file. Unnecessary: `opencode.json` already is the
  machine-managed file and opencode merges it.

Plugins: `opencode plugin add` (it also installs the package), removal via
`opencode plugin remove <exact spec>` confirmed by re-reading the file.

## Design

### Reading (shared by MCP and plugins)

`opencode_state` reads `<home>/.config/opencode/opencode.json` and
`opencode.jsonc` (fixed paths, like the other agents), each with a stdlib JSONC
parser (`//` and `/* */` comments outside strings, trailing commas), and
returns the **union**: MCP servers from flat `mcp.<name>` and nested
`mcp.servers.<name>`, plugins from `plugin` and `plugins` — each tagged with
the file and key it came from (needed for removal). An absent or unparseable
file contributes nothing, like the other readers; `apply` refuses to write a
file it cannot parse (per `failure_policy`) instead of overwriting it.

### MCP (`mcp_servers`, agent `opencode`)

- **Model:** add `"opencode"` to `AGENTS` and to `_HEADERS_AGENTS`.
  `bearer_token_env_var` stays codex-only.
- **Normalize** to the shared dict: `local` → `stdio`, `command[0]`/`[1:]` →
  `command`/`args`, `environment` → `env`; `remote` → `http`, `url`, `headers`.
- **Add / change:** written into `opencode.json` under `mcp.servers.<name>`
  (created with `{"$schema": "https://opencode.ai/config.json"}` if absent);
  every other key and undeclared server is preserved. Atomic write (temp +
  rename), keeping owner/group/mode; a new file is owned by the user, 0644. A
  changed spec for a server that lives in `opencode.jsonc` is rewritten into
  `opencode.json` and removed from `.jsonc` (see removal).
- **Removal:** deleted from wherever the reader found it. In `opencode.json`
  this is the normal case. **Only if it lives in `opencode.jsonc`** (put there
  by hand, then captured by `sync`) does dasik rewrite `.jsonc`, which drops its
  comments — the cost the user accepted with approach A, now confined to that
  case; the plan line says which file.
- **opencode not installed** for that user (no `opencode` binary in the
  target): the item fails under `failure_policy`, like a missing `claude` /
  `codex` CLI — dasik does not create a config for an absent program.
- **`enabled: false`:** read as present; dasik does not fight a switch the
  user flipped.
- **`plan` / `sync`:** same item keys, `_same_registration` comparison, and
  merge-by-identical-spec capture as the other agents.

### Plugins (`ai_skills`, method `opencode-plugin`)

- **Model:** `Method` gains `"opencode-plugin"`, `METHOD_AGENT` maps it to
  `opencode`. Fields: `name` (`PLUGIN_NAME_RE`) and `source`, the exact
  specifier (npm name with optional `@version`, or `<name>@git+https://…`);
  validated: no control chars, no leading `-`, no local/`file:` paths.
- **Identity is the exact specifier.** Present ⇔ `source` appears in `plugin`
  or `plugins` of either file. A changed `source` plans remove-old + add-new
  (`remove` needs the exact old specifier anyway).
- **Install:** `opencode plugin add <source>` as the user (`_su_argv`).
- **Remove:** `opencode plugin remove <exact spec>` when it lives in
  `opencode.json`; since it exits 0 on a no-op, success is judged by re-reading
  the file. One living in `opencode.jsonc` is removed by editing `.jsonc`
  (same rule as MCP).
- **`sync`:** each plugin element whose specifier the model accepts becomes an
  `opencode-plugin` entry (`name` = the part before the version/`@git+`);
  scoped (`@scope/pkg`), local/`file:` or tuple-with-options elements are
  skipped with a warning — never captured into a config `check` would reject.

### Tests and verification

- Unit (TDD): JSONC reader (comments, strings containing `//`, trailing commas,
  nested `mcp.servers`, garbage); normalization both ways; writer preserves
  unrelated keys and undeclared servers; model validation of the new agent and
  method.
- One journey test through the CLI (`tests/cli/test_verbs_integration.py`
  style): `plan → apply → plan` silent, `sync → check → plan` silent, block
  removed → removal planned.
- One guest script + `config/vm-opencode.json`: opencode installed, one stdio
  MCP + one plugin declared; apply, re-plan silent, sync round-trip, remove.
  The installed guest has no network (memory), so the plugin step either runs
  during the install-driven apply or uses a local-registry-free specifier; the
  builder decides and says so in the script header.

## Left to the builder

- Exact JSONC parser implementation (stdlib only — runtime deps stay
  `pydantic`, `colorama`).
- Whether `opencode plugin add` works inside `arch-chroot` during an install
  (it spawns its own service); measured in the guest. If it does not, the
  plugin step is post-install only and the guest script says so.
- Whether `enabled: false` handling needs to change after seeing it in use.

## Calls made without the user (check while reading)

- Write MCP into `opencode.json`, not `.jsonc` (measured: opencode merges both
  and its own CLI writes `.json`).
- `.jsonc` is rewritten only to remove/move something that lives there.
- Plugin identity = exact specifier; source change = remove + add.
- `sync` skips plugin specifiers the model cannot represent, with a warning.
- No `opencode` binary ⇒ the item fails under `failure_policy`.
